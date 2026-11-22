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

struct FirmwareRelease: Decodable {
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
    static let frameCharacteristicID = CBUUID(string: GlassesProtocol.frameUUID)
    static let settingsCharacteristicID = CBUUID(string: GlassesProtocol.settingsUUID)
    static let ackCharacteristicID = CBUUID(string: GlassesProtocol.ackUUID)
    static let updateCharacteristicID = CBUUID(string: GlassesProtocol.updateUUID)
    static let updateStatusCharacteristicID = CBUUID(string: GlassesProtocol.updateStatusUUID)
    static let versionCharacteristicID = CBUUID(string: GlassesProtocol.versionUUID)
    static let authCharacteristicID = CBUUID(string: GlassesProtocol.authUUID)
    static let maxUnconfirmedBytes = GlassesProtocol.updateStep
    static let updateStallTimeout = 45.0

    static let savedPeripheralDefaultsKey = "glassesPeripheral"
    static let restoreIdentifier = "memory-glasses-link"

    var state = GlassesLinkState.looking
    var recording: String?
    var photosSent = 0
    var lastUpload: Date?
    var glassesDisplayText = ""
    var glassesVersion: String?
    var availableUpdate: FirmwareRelease?
    var updateProgress: Double?
    var updateMessage: String?

    var canUpdate: Bool {
        state == .connected && isAuthenticated && availableUpdate != nil && firmwareTransfer == nil
    }

    var central: CBCentralManager?
    var peripheral: CBPeripheral?
    var settingsCharacteristic: CBCharacteristic?
    var ackCharacteristic: CBCharacteristic?
    var updateCharacteristic: CBCharacteristic?
    var versionIn: CBCharacteristic?
    var firmwareTransfer: (image: Data, bytesSent: Int, bytesConfirmed: Int, version: String, endSent: Bool)?
    var lastUpdateReplyAt = Date()
    var isAuthenticated = false
    var recordingsToEnd: Set<String> = []
    var phoneNonce: Data?
    var versionAfterRestart: String?
    var partialFrame: (length: Int, number: UInt32, recording: String, data: Data)?
    var lastSettings: GlassesSettings?
    let uploader = FrameUploader()
    var isCheckingForUpdate = false
    var versionNotToRetry: String?

    override private init() {
        super.init()
    }

    func start() {
        guard central == nil else { return }

        central = CBCentralManager(delegate: self, queue: nil,
                                   options: [CBCentralManagerOptionRestoreIdentifierKey: Self.restoreIdentifier])

        Task { @MainActor in
            while true {
                try? await Task.sleep(for: .seconds(600))
                await self.checkForUpdate()
            }
        }

        Task { @MainActor in
            while true {
                if await self.uploader.waiting() > 0 {
                    await self.uploadQueuedFrames()
                }

                await self.reportEndedRecordings()

                try? await Task.sleep(for: .seconds(60))
            }
        }
    }

    func applySettings(_ settings: GlassesSettings) {
        lastSettings = settings
        sendSettings(settings, displayText: glassesDisplayText.isEmpty ? nil : glassesDisplayText)

        if !settings.recording {
            finishRecording()
        }
    }

    func finishRecording() {
        guard let id = recording else { return }

        recordingsToEnd.insert(id)
        recording = nil

        Task { @MainActor in
            await self.uploadQueuedFrames()
            await self.reportEndedRecordings()
        }
    }

    @MainActor
    func reportEndedRecordings() async {
        for id in recordingsToEnd where await uploader.waiting(for: id) == 0 {
            let done: RecordingEnded? = try? await API.current.send("/glasses/recordings/\(id)/end", method: "POST",
                                                                    body: [String: String]())

            if done != nil {
                recordingsToEnd.remove(id)
            }
        }
    }

    func sendSettings(_ settings: GlassesSettings, displayText: String?) {
        guard let peripheral, let settingsCharacteristic, peripheral.state == .connected,
              let encoded = try? API.encoder.encode(settings),
              var values = (try? JSONSerialization.jsonObject(with: encoded)) as? [String: Any] else { return }

        if let displayText {
            values["status"] = displayText
        }

        guard let data = try? JSONSerialization.data(withJSONObject: values) else { return }

        peripheral.writeValue(data, for: settingsCharacteristic, type: .withResponse)
    }

    func acknowledge(_ number: UInt32) {
        guard let peripheral, let ackCharacteristic, peripheral.state == .connected else { return }

        peripheral.writeValue(GlassesPackets.ack(number), for: ackCharacteristic, type: .withoutResponse)
    }

    func findGlasses() {
        guard let central, central.state == .poweredOn else { return }

        if let saved = UserDefaults.standard.string(forKey: Self.savedPeripheralDefaultsKey), let id = UUID(uuidString: saved),
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
        UserDefaults.standard.set(found.identifier.uuidString, forKey: Self.savedPeripheralDefaultsKey)
    }

    func handleFramePacket(_ value: Data) {
        guard isAuthenticated, firmwareTransfer == nil else { return }

        switch FramePacket(value) {
        case .start(let length, let number, let name):
            partialFrame = (length, number, name, Data(capacity: length))
        case .data(let piece):
            partialFrame?.data.append(piece)
        case .end(let crc):
            completeFrame(expectedCRC: crc)
        case nil:
            break
        }
    }

    func completeFrame(expectedCRC: UInt32) {
        guard let frame = partialFrame else { return }

        partialFrame = nil

        guard frame.data.count == frame.length, CRC32.of(frame.data) == expectedCRC else {
            acknowledge(frame.number)
            return
        }

        if frame.recording != recording {
            recording = frame.recording
            photosSent = 0
        }

        recordingsToEnd.remove(frame.recording)

        let received = ReceivedFrame(recording: frame.recording, number: frame.number, jpeg: frame.data)

        Task { @MainActor in
            await self.uploader.enqueue(received)
            self.acknowledge(received.number)
            await self.uploadQueuedFrames()
        }
    }

    @MainActor
    func uploadQueuedFrames() async {
        let flush = await uploader.flush()

        guard !flush.busy else { return }

        if let settings = flush.settings {
            lastSettings = settings
        }

        photosSent += flush.sent

        if flush.sent > 0 {
            lastUpload = Date()
        }

        glassesDisplayText = flush.waiting == 0 ? "Rec \(photosSent)" : (flush.sent > 0 ? "Sending…" : "Phone offline")

        if let settings = lastSettings {
            sendSettings(settings, displayText: glassesDisplayText)
        }
    }
}
