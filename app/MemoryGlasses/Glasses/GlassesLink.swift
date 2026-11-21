import CoreBluetooth
import Foundation
import Observation

enum GlassesLinkState {
    case unsupported
    case off
    case notAllowed
    case looking
    case connecting
    case connected

    var label: String {
        switch self {
        case .unsupported: "Bluetooth isn't available on this device"
        case .off: "Bluetooth is off"
        case .notAllowed: "Bluetooth isn't allowed for this app"
        case .looking: "Looking for the glasses"
        case .connecting: "Connecting to the glasses"
        case .connected: "Connected over Bluetooth"
        }
    }
}

struct FirmwareInfo: Decodable {
    let version: String
    let size: Int
    let crc32: UInt32
    let url: String
}

private struct RecordingEnded: Decodable {
    let ended: [String]
}

struct ReceivedFrame {
    let recording: String
    let number: UInt32
    let jpeg: Data
}

@Observable
final class GlassesLink: NSObject {
    static let shared = GlassesLink()

    static let service = CBUUID(string: GlassesProtocol.serviceUUID)
    static let frameCharacteristic = CBUUID(string: GlassesProtocol.frameUUID)
    static let settingsCharacteristic = CBUUID(string: GlassesProtocol.settingsUUID)
    static let ackCharacteristic = CBUUID(string: GlassesProtocol.ackUUID)
    static let updateCharacteristic = CBUUID(string: GlassesProtocol.updateUUID)
    static let updateStatusCharacteristic = CBUUID(string: GlassesProtocol.updateStatusUUID)
    static let versionCharacteristic = CBUUID(string: GlassesProtocol.versionUUID)
    static let authCharacteristic = CBUUID(string: GlassesProtocol.authUUID)
    static let confirmEvery = GlassesProtocol.updateStep
    static let updateQuiet = 45.0

    static let rememberedKey = "glassesPeripheral"
    static let restoreKey = "memory-glasses-link"

    var state = GlassesLinkState.looking
    var recording: String?
    var photosSent = 0
    var lastUpload: Date?
    var status = ""
    var glassesVersion: String?
    var availableUpdate: FirmwareInfo?
    var updateProgress: Double?
    var updateStage: String?

    var canUpdate: Bool {
        state == .connected && trusted && availableUpdate != nil && update == nil
    }

    var central: CBCentralManager?
    var peripheral: CBPeripheral?
    var settingsOut: CBCharacteristic?
    var ackOut: CBCharacteristic?
    var updateOut: CBCharacteristic?
    var versionIn: CBCharacteristic?
    var update: (image: Data, sent: Int, confirmed: Int, version: String, ended: Bool)?
    var updateHeard = Date()
    var trusted = false
    var ending: Set<String> = []
    var challenge: Data?
    var expectedVersion: String?
    var incoming: (length: Int, number: UInt32, recording: String, data: Data)?
    var lastSettings: GlassesSettings?
    let uploader = FrameUploader()
    var checking = false
    var failedVersion: String?

    override private init() {
        super.init()
    }

    func start() {
        guard central == nil else { return }

        central = CBCentralManager(delegate: self, queue: nil,
                                   options: [CBCentralManagerOptionRestoreIdentifierKey: Self.restoreKey])

        Task { @MainActor in
            while true {
                try? await Task.sleep(for: .seconds(600))
                await self.checkForUpdate()
            }
        }

        Task { @MainActor in
            while true {
                if await self.uploader.waiting() > 0 {
                    await self.deliver()
                }

                await self.settleEndings()

                try? await Task.sleep(for: .seconds(60))
            }
        }
    }

    func push(_ settings: GlassesSettings) {
        lastSettings = settings
        write(settings, status: status.isEmpty ? nil : status)

        if !settings.recording {
            finishRecording()
        }
    }

    func finishRecording() {
        guard let id = recording else { return }

        ending.insert(id)
        recording = nil

        Task { @MainActor in
            await self.deliver()
            await self.settleEndings()
        }
    }

    @MainActor
    func settleEndings() async {
        for id in ending where await uploader.waiting(for: id) == 0 {
            let done: RecordingEnded? = try? await API.current.send("/glasses/recordings/\(id)/end", method: "POST",
                                                                    body: [String: String]())

            if done != nil {
                ending.remove(id)
            }
        }
    }

    func write(_ settings: GlassesSettings, status: String?) {
        guard let peripheral, let settingsOut, peripheral.state == .connected,
              let encoded = try? API.encoder.encode(settings),
              var values = (try? JSONSerialization.jsonObject(with: encoded)) as? [String: Any] else { return }

        if let status {
            values["status"] = status
        }

        guard let data = try? JSONSerialization.data(withJSONObject: values) else { return }

        peripheral.writeValue(data, for: settingsOut, type: .withResponse)
    }

    func acknowledge(_ number: UInt32) {
        guard let peripheral, let ackOut, peripheral.state == .connected else { return }

        peripheral.writeValue(GlassesPackets.ack(number), for: ackOut, type: .withoutResponse)
    }

    func findGlasses() {
        guard let central, central.state == .poweredOn else { return }

        if let saved = UserDefaults.standard.string(forKey: Self.rememberedKey), let id = UUID(uuidString: saved),
           let known = central.retrievePeripherals(withIdentifiers: [id]).first {
            connect(known)
        } else if let attached = central.retrieveConnectedPeripherals(withServices: [Self.service]).first {
            connect(attached)
        }

        state = peripheral == nil ? .looking : .connecting
        central.scanForPeripherals(withServices: [Self.service])
    }

    func connect(_ found: CBPeripheral) {
        peripheral = found
        found.delegate = self
        central?.connect(found)
        UserDefaults.standard.set(found.identifier.uuidString, forKey: Self.rememberedKey)
    }

    func received(_ value: Data) {
        guard trusted, update == nil else { return }

        switch FramePacket(value) {
        case .start(let length, let number, let name):
            incoming = (length, number, name, Data(capacity: length))
        case .data(let piece):
            incoming?.data.append(piece)
        case .end(let crc):
            finish(expected: crc)
        case nil:
            break
        }
    }

    func finish(expected: UInt32) {
        guard let frame = incoming else { return }

        incoming = nil

        guard frame.data.count == frame.length, CRC32.of(frame.data) == expected else {
            acknowledge(frame.number)
            return
        }

        if frame.recording != recording {
            recording = frame.recording
            photosSent = 0
        }

        ending.remove(frame.recording)

        let received = ReceivedFrame(recording: frame.recording, number: frame.number, jpeg: frame.data)

        Task { @MainActor in
            await self.uploader.enqueue(received)
            self.acknowledge(received.number)
            await self.deliver()
        }
    }

    @MainActor
    func deliver() async {
        let result = await uploader.flush()

        guard !result.busy else { return }

        if let settings = result.settings {
            lastSettings = settings
        }

        photosSent += result.sent

        if result.sent > 0 {
            lastUpload = Date()
        }

        status = result.waiting == 0 ? "Rec \(photosSent)" : (result.sent > 0 ? "Sending…" : "Phone offline")

        if let settings = lastSettings {
            write(settings, status: status)
        }
    }
}
