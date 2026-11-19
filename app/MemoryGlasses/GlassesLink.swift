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

struct ReceivedFrame {
    let recording: String
    let number: UInt32
    let jpeg: Data
}

@Observable
final class GlassesLink: NSObject {
    static let shared = GlassesLink()

    static let service = CBUUID(string: "6E1F0001-6D65-6D6F-7279-676C61737365")
    static let frameCharacteristic = CBUUID(string: "6E1F0002-6D65-6D6F-7279-676C61737365")
    static let settingsCharacteristic = CBUUID(string: "6E1F0003-6D65-6D6F-7279-676C61737365")
    static let ackCharacteristic = CBUUID(string: "6E1F0004-6D65-6D6F-7279-676C61737365")
    static let updateCharacteristic = CBUUID(string: "6E1F0005-6D65-6D6F-7279-676C61737365")
    static let updateStatusCharacteristic = CBUUID(string: "6E1F0006-6D65-6D6F-7279-676C61737365")
    static let versionCharacteristic = CBUUID(string: "6E1F0007-6D65-6D6F-7279-676C61737365")
    private static let confirmEvery = 32 * 1024

    private static let rememberedKey = "glassesPeripheral"
    private static let restoreKey = "memory-glasses-link"

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
        state == .connected && availableUpdate != nil && update == nil
    }

    private var central: CBCentralManager?
    private var peripheral: CBPeripheral?
    private var settingsOut: CBCharacteristic?
    private var ackOut: CBCharacteristic?
    private var updateOut: CBCharacteristic?
    private var versionIn: CBCharacteristic?
    private var update: (image: Data, sent: Int, confirmed: Int, version: String)?
    private var expectedVersion: String?
    private var incoming: (length: Int, number: UInt32, recording: String, data: Data)?
    private var lastSettings: GlassesSettings?
    private let uploader = FrameUploader()

    override private init() {
        super.init()
    }

    func start() {
        guard central == nil else { return }

        central = CBCentralManager(delegate: self, queue: nil,
                                   options: [CBCentralManagerOptionRestoreIdentifierKey: Self.restoreKey])
    }

    func push(_ settings: GlassesSettings) {
        lastSettings = settings
        write(settings, status: status.isEmpty ? nil : status)
    }

    @MainActor
    func startUpdate() async {
        guard let info = availableUpdate, let url = URL(string: info.url), update == nil else { return }

        updateStage = "Downloading the update…"
        updateProgress = 0

        guard let (image, response) = try? await URLSession.shared.data(from: url),
              (response as? HTTPURLResponse)?.statusCode == 200 else {
            updateStage = "Couldn't download the update"
            updateProgress = nil
            return
        }

        guard image.count == info.size, CRC32.of(image) == info.crc32 else {
            updateStage = "The downloaded update was damaged; nothing was sent"
            updateProgress = nil
            return
        }

        update = (image, 0, 0, info.version)
        updateStage = "Sending the update…"

        var header = Data([1])
        header.append(UInt32(image.count).littleBytes)
        header.append(info.crc32.littleBytes)
        sendUpdate(header)
    }

    func cancelUpdate() {
        guard update != nil else { return }

        sendUpdate(Data([4]))
        update = nil
        updateProgress = nil
        updateStage = "Update cancelled"
    }

    private func sendUpdate(_ value: Data) {
        guard let peripheral, let updateOut, peripheral.state == .connected else { return }

        peripheral.writeValue(value, for: updateOut, type: .withoutResponse)
    }

    private func pump() {
        guard let peripheral, let job = update, job.sent < job.image.count else { return }

        let chunk = max(peripheral.maximumWriteValueLength(for: .withoutResponse) - 1, 20)
        var sent = job.sent

        while sent < job.image.count, sent - job.confirmed < Self.confirmEvery, peripheral.canSendWriteWithoutResponse {
            let end = min(sent + chunk, job.image.count, job.confirmed + Self.confirmEvery)
            sendUpdate(Data([2]) + job.image.subdata(in: sent..<end))
            sent = end
        }

        update?.sent = sent
        updateProgress = Double(job.confirmed) / Double(job.image.count)

        if sent == job.image.count {
            sendUpdate(Data([3]))
        }
    }

    private func updateSaid(_ text: String) {
        guard update != nil else { return }

        if text == "ready" {
            pump()
        } else if text.hasPrefix("got "), let bytes = Int(text.dropFirst(4)) {
            update?.confirmed = bytes
            pump()
        } else if text == "done" {
            expectedVersion = update?.version
            update = nil
            updateProgress = 1
            updateStage = "Restarting…"
        } else if text.hasPrefix("error") {
            update = nil
            updateProgress = nil
            updateStage = "Update failed: \(text.dropFirst(5).trimmingCharacters(in: .whitespaces))"
        }
    }

    private func versionArrived(_ version: String) {
        glassesVersion = version

        if let expected = expectedVersion {
            updateStage = version == expected ? "Updated to \(version)" : "The glasses still run \(version)"
            updateProgress = nil
            expectedVersion = nil
        }

        Task { @MainActor in
            let latest: FirmwareInfo? = try? await API.current.get("/glasses/firmware")
            self.availableUpdate = latest.flatMap { $0.version == version ? nil : $0 }
        }
    }

    private func write(_ settings: GlassesSettings, status: String?) {
        guard let peripheral, let settingsOut, peripheral.state == .connected,
              let encoded = try? API.encoder.encode(settings),
              var values = (try? JSONSerialization.jsonObject(with: encoded)) as? [String: Any] else { return }

        if let status {
            values["status"] = status
        }

        guard let data = try? JSONSerialization.data(withJSONObject: values) else { return }

        peripheral.writeValue(data, for: settingsOut, type: .withResponse)
    }

    private func acknowledge(_ number: UInt32) {
        guard let peripheral, let ackOut, peripheral.state == .connected else { return }

        var value = number.littleEndian
        peripheral.writeValue(Data(bytes: &value, count: 4), for: ackOut, type: .withoutResponse)
    }

    private func findGlasses() {
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

    private func connect(_ found: CBPeripheral) {
        peripheral = found
        found.delegate = self
        central?.connect(found)
        UserDefaults.standard.set(found.identifier.uuidString, forKey: Self.rememberedKey)
    }

    private func received(_ value: Data) {
        guard update == nil, let kind = value.first else { return }

        let payload = value.dropFirst()

        switch kind {
        case 1 where payload.count >= 24:
            let length = Int(payload.uint32(at: 0))
            let number = payload.uint32(at: 4)
            let name = String(decoding: payload.dropFirst(8).prefix(16), as: UTF8.self)
                .trimmingCharacters(in: .controlCharacters.union(.whitespaces))
            incoming = (length, number, name, Data(capacity: length))
        case 2:
            incoming?.data.append(contentsOf: payload)
        case 3 where payload.count >= 4:
            finish(expected: payload.uint32(at: 0))
        default:
            break
        }
    }

    private func finish(expected: UInt32) {
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

        let received = ReceivedFrame(recording: frame.recording, number: frame.number, jpeg: frame.data)

        Task { @MainActor in
            await self.deliver(received)
        }
    }

    @MainActor
    private func deliver(_ frame: ReceivedFrame) async {
        await uploader.enqueue(frame)

        let result = await uploader.flush()

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

        acknowledge(frame.number)
    }
}

extension GlassesLink: CBCentralManagerDelegate {
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        switch central.state {
        case .poweredOn:
            findGlasses()
        case .poweredOff:
            state = .off
        case .unauthorized:
            state = .notAllowed
        case .unsupported:
            state = .unsupported
        default:
            state = .looking
        }
    }

    func centralManager(_ central: CBCentralManager, willRestoreState dict: [String: Any]) {
        guard let restored = (dict[CBCentralManagerRestoredStatePeripheralsKey] as? [CBPeripheral])?.first else { return }

        peripheral = restored
        restored.delegate = self
    }

    func centralManager(_ central: CBCentralManager, didDiscover found: CBPeripheral,
                        advertisementData: [String: Any], rssi: NSNumber) {
        guard peripheral == nil || peripheral?.state == .disconnected else { return }

        state = .connecting
        connect(found)
    }

    func centralManager(_ central: CBCentralManager, didConnect connected: CBPeripheral) {
        central.stopScan()
        state = .connected
        connected.discoverServices([Self.service])
    }

    func centralManager(_ central: CBCentralManager, didFailToConnect failed: CBPeripheral, error: Error?) {
        state = .looking
        central.connect(failed)
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral gone: CBPeripheral, error: Error?) {
        state = .looking
        settingsOut = nil
        ackOut = nil
        updateOut = nil
        versionIn = nil
        incoming = nil

        if update != nil {
            update = nil
            updateProgress = nil
            updateStage = "The glasses disconnected during the update"
        }
        central.connect(gone)
        central.scanForPeripherals(withServices: [Self.service])
    }
}

extension GlassesLink: CBPeripheralDelegate {
    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        guard let service = peripheral.services?.first(where: { $0.uuid == Self.service }) else { return }

        peripheral.discoverCharacteristics([Self.frameCharacteristic, Self.settingsCharacteristic, Self.ackCharacteristic,
                                            Self.updateCharacteristic, Self.updateStatusCharacteristic,
                                            Self.versionCharacteristic], for: service)
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        for characteristic in service.characteristics ?? [] {
            switch characteristic.uuid {
            case Self.frameCharacteristic:
                peripheral.setNotifyValue(true, for: characteristic)
            case Self.settingsCharacteristic:
                settingsOut = characteristic
            case Self.ackCharacteristic:
                ackOut = characteristic
            case Self.updateCharacteristic:
                updateOut = characteristic
            case Self.updateStatusCharacteristic:
                peripheral.setNotifyValue(true, for: characteristic)
            case Self.versionCharacteristic:
                versionIn = characteristic
                peripheral.readValue(for: characteristic)
            default:
                break
            }
        }

        Task { @MainActor in
            if let settings: GlassesSettings = try? await API.current.get("/settings") {
                self.lastSettings = settings
                self.write(settings, status: self.status.isEmpty ? nil : self.status)
            }
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        guard let value = characteristic.value else { return }

        switch characteristic.uuid {
        case Self.frameCharacteristic:
            received(value)
        case Self.updateStatusCharacteristic:
            updateSaid(String(decoding: value, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines))
        case Self.versionCharacteristic:
            versionArrived(String(decoding: value, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines))
        default:
            break
        }
    }

    func peripheralIsReady(toSendWriteWithoutResponse peripheral: CBPeripheral) {
        pump()
    }
}

struct UploadResult {
    let sent: Int
    let waiting: Int
    let settings: GlassesSettings?
}

private struct FrameReply: Decodable {
    let uploadUrl: String?
    let uploadName: String?
}

actor FrameUploader {
    private static let limit = 50

    private let folder: URL
    private var slots: [String: (url: URL, name: String)] = [:]
    private var counter = 0

    init() {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        folder = base.appendingPathComponent("glasses-queue", isDirectory: true)
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    }

    func enqueue(_ frame: ReceivedFrame) {
        counter += 1
        let name = String(format: "%.3f-%06d-%@-%u.jpg", Date().timeIntervalSince1970, counter, frame.recording, frame.number)
        try? frame.jpeg.write(to: folder.appendingPathComponent(name))

        let waiting = queued()

        if waiting.count > Self.limit {
            for old in waiting.prefix(waiting.count - Self.limit) {
                try? FileManager.default.removeItem(at: old)
            }
        }
    }

    func flush() async -> UploadResult {
        var sent = 0
        var settings: GlassesSettings?

        for file in queued() {
            guard let recording = Self.recording(of: file), let jpeg = try? Data(contentsOf: file) else {
                try? FileManager.default.removeItem(at: file)
                continue
            }

            guard let reply = try? await upload(jpeg, recording: recording) else { break }

            try? FileManager.default.removeItem(at: file)
            settings = reply
            sent += 1
        }

        return UploadResult(sent: sent, waiting: queued().count, settings: settings)
    }

    private func queued() -> [URL] {
        let files = (try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? []

        return files.filter { $0.pathExtension == "jpg" }.sorted { $0.lastPathComponent < $1.lastPathComponent }
    }

    private static func recording(of file: URL) -> String? {
        let parts = file.deletingPathExtension().lastPathComponent.split(separator: "-")

        return parts.count >= 4 ? parts[2..<(parts.count - 1)].joined(separator: "-") : nil
    }

    private func upload(_ jpeg: Data, recording: String) async throws -> GlassesSettings {
        let api = API.current

        if slots[recording] == nil {
            remember(try await api.data(for: framed(api.request("/settings"), recording)), for: recording)
        }

        if let slot = slots[recording] {
            if try await !put(jpeg, to: slot.url) {
                slots[recording] = nil
                remember(try await api.data(for: framed(api.request("/settings"), recording)), for: recording)

                guard let fresh = slots[recording], try await put(jpeg, to: fresh.url) else { throw APIError.status(0) }
            }

            var notify = framed(try api.request("/glasses/frames", method: "POST"), recording)
            notify.setValue(slots[recording]?.name, forHTTPHeaderField: "X-Uploaded")
            slots[recording] = nil

            return try finished(await api.data(for: notify), for: recording)
        }

        var direct = framed(try api.request("/glasses/frames", method: "POST"), recording)
        direct.httpBody = jpeg
        direct.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")

        return try finished(await api.data(for: direct), for: recording)
    }

    private func framed(_ request: URLRequest, _ recording: String) -> URLRequest {
        var request = request
        request.setValue(recording, forHTTPHeaderField: "X-Recording")

        return request
    }

    private func put(_ jpeg: Data, to url: URL) async throws -> Bool {
        var request = URLRequest(url: url)
        request.httpMethod = "PUT"
        request.httpBody = jpeg
        request.timeoutInterval = 30

        let (_, response) = try await URLSession.shared.data(for: request)

        return (response as? HTTPURLResponse)?.statusCode == 200
    }

    private func remember(_ reply: Data, for recording: String) {
        guard let slot = try? API.decoder.decode(FrameReply.self, from: reply),
              let link = slot.uploadUrl, let url = URL(string: link), let name = slot.uploadName else { return }

        slots[recording] = (url, name)
    }

    private func finished(_ reply: Data, for recording: String) throws -> GlassesSettings {
        remember(reply, for: recording)

        return try API.decoder.decode(GlassesSettings.self, from: reply)
    }
}

enum CRC32 {
    private static let table: [UInt32] = (0..<256).map { index in
        (0..<8).reduce(UInt32(index)) { value, _ in value & 1 == 1 ? 0xEDB8_8320 ^ (value >> 1) : value >> 1 }
    }

    static func of(_ data: Data) -> UInt32 {
        ~data.reduce(UInt32.max) { crc, byte in table[Int((crc ^ UInt32(byte)) & 0xFF)] ^ (crc >> 8) }
    }
}

private extension UInt32 {
    var littleBytes: Data {
        var value = littleEndian

        return Data(bytes: &value, count: 4)
    }
}

private extension Data {
    func uint32(at offset: Int) -> UInt32 {
        let start = index(startIndex, offsetBy: offset)

        return self[start..<index(start, offsetBy: 4)].enumerated().reduce(UInt32(0)) { value, pair in
            value | UInt32(pair.element) << (8 * UInt32(pair.offset))
        }
    }
}
