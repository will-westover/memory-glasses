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
    private static let confirmEvery = GlassesProtocol.updateStep
    private static let updateQuiet = 45.0

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
        state == .connected && trusted && availableUpdate != nil && update == nil
    }

    private var central: CBCentralManager?
    private var peripheral: CBPeripheral?
    private var settingsOut: CBCharacteristic?
    private var ackOut: CBCharacteristic?
    private var updateOut: CBCharacteristic?
    private var versionIn: CBCharacteristic?
    private var update: (image: Data, sent: Int, confirmed: Int, version: String, ended: Bool)?
    private var updateHeard = Date()
    private var trusted = false
    private var ending: Set<String> = []
    private var challenge: Data?
    private var expectedVersion: String?
    private var incoming: (length: Int, number: UInt32, recording: String, data: Data)?
    private var lastSettings: GlassesSettings?
    private let uploader = FrameUploader()
    private var checking = false
    private var failedVersion: String?

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

    private func finishRecording() {
        guard let id = recording else { return }

        ending.insert(id)
        recording = nil

        Task { @MainActor in
            await self.deliver()
            await self.settleEndings()
        }
    }

    @MainActor
    private func settleEndings() async {
        for id in ending where await uploader.waiting(for: id) == 0 {
            let done: RecordingEnded? = try? await API.current.send("/glasses/recordings/\(id)/end", method: "POST",
                                                                    body: [String: String]())

            if done != nil {
                ending.remove(id)
            }
        }
    }

    @MainActor
    func startUpdate() async {
        guard let info = availableUpdate, update == nil else { return }

        updateStage = "Downloading the update…"
        updateProgress = 0

        guard let image = await Self.download(info.url) else {
            updateStage = "Couldn't download the update"
            updateProgress = nil
            return
        }

        guard image.count == info.size, CRC32.of(image) == info.crc32 else {
            updateStage = "The downloaded update was damaged; nothing was sent"
            updateProgress = nil
            return
        }

        guard state == .connected, updateOut != nil, update == nil else {
            updateStage = "The glasses disconnected before the update was sent"
            updateProgress = nil
            return
        }

        update = (image, 0, 0, info.version, false)
        updateHeard = Date()
        updateStage = "Sending the update…"

        Task { @MainActor in await self.watchUpdate(info.version) }

        sendUpdate(GlassesPackets.updateBegin(size: image.count, crc: info.crc32))
    }

    private static func download(_ link: String) async -> Data? {
        guard let url = URL(string: link), let (image, response) = try? await URLSession.shared.data(from: url),
              (response as? HTTPURLResponse)?.statusCode == 200 else { return nil }

        return image
    }

    @MainActor
    private func watchUpdate(_ version: String) async {
        while update?.version == version {
            try? await Task.sleep(for: .seconds(5))

            if update?.version == version, Date().timeIntervalSince(updateHeard) > Self.updateQuiet {
                sendUpdate(GlassesPackets.updateCancel)
                failedVersion = version
                update = nil
                updateProgress = nil
                updateStage = "The update stalled; nothing was installed"
            }
        }
    }

    func cancelUpdate() {
        guard update != nil else { return }

        sendUpdate(GlassesPackets.updateCancel)
        update = nil
        updateProgress = nil
        updateStage = "Update cancelled"
    }

    private func sendUpdate(_ value: Data) {
        guard let peripheral, let updateOut, peripheral.state == .connected else { return }

        peripheral.writeValue(value, for: updateOut, type: .withoutResponse)
    }

    private func pump() {
        guard let peripheral, let job = update, !job.ended else { return }

        let chunk = max(peripheral.maximumWriteValueLength(for: .withoutResponse) - 1, 20)
        var sent = job.sent

        while sent < job.image.count, sent - job.confirmed < Self.confirmEvery, peripheral.canSendWriteWithoutResponse {
            let end = min(sent + chunk, job.image.count, job.confirmed + Self.confirmEvery)
            sendUpdate(GlassesPackets.updateData(job.image.subdata(in: sent..<end)))
            sent = end
        }

        update?.sent = sent
        updateProgress = Double(job.confirmed) / Double(job.image.count)

        if sent == job.image.count, peripheral.canSendWriteWithoutResponse {
            sendUpdate(GlassesPackets.updateEnd)
            update?.ended = true
        }
    }

    private func updateSaid(_ text: String) {
        guard update != nil else { return }

        updateHeard = Date()

        switch UpdateStatus(text) {
        case .ready:
            pump()
        case .got(let bytes):
            update?.confirmed = bytes
            pump()
        case .done:
            expectedVersion = update?.version
            update = nil
            updateProgress = 1
            updateStage = "Restarting…"
        case .error(let reason):
            failedVersion = update?.version
            update = nil
            updateProgress = nil
            updateStage = "Update failed: \(reason)"
        case nil:
            break
        }
    }

    private func versionArrived(_ version: String) {
        glassesVersion = version

        if let expected = expectedVersion {
            updateStage = version == expected ? "Updated to \(version)" : "The glasses still run \(version)"
            failedVersion = version == expected ? nil : expected
            updateProgress = nil
            expectedVersion = nil
        }

        Task { @MainActor in await self.checkForUpdate() }
    }

    @MainActor
    func checkForUpdate(install: Bool = true) async {
        guard let version = glassesVersion, !checking, update == nil else { return }

        checking = true
        defer { checking = false }

        let latest: FirmwareInfo? = try? await API.current.get("/glasses/firmware")
        availableUpdate = latest.flatMap { $0.version > version ? $0 : nil }

        guard let found = availableUpdate else {
            if updateProgress == nil, expectedVersion == nil, !(updateStage ?? "").hasPrefix("Updated") {
                updateStage = "Up to date"
            }
            return
        }

        if install, canUpdate, found.version != failedVersion {
            await startUpdate()
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

        peripheral.writeValue(GlassesPackets.ack(number), for: ackOut, type: .withoutResponse)
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

        ending.remove(frame.recording)

        let received = ReceivedFrame(recording: frame.recording, number: frame.number, jpeg: frame.data)

        Task { @MainActor in
            await self.uploader.enqueue(received)
            self.acknowledge(received.number)
            await self.deliver()
        }
    }

    @MainActor
    private func deliver() async {
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
        trusted = false
        challenge = nil

        if update != nil {
            update = nil
            updateProgress = nil
            updateStage = "The glasses disconnected during the update"
        }
        central.connect(gone)
        central.scanForPeripherals(withServices: [Self.service])

        Task { @MainActor in
            try? await Task.sleep(for: .seconds(30))

            if self.state != .connected {
                self.finishRecording()
            }
        }
    }
}

extension GlassesLink: CBPeripheralDelegate {
    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        guard let service = peripheral.services?.first(where: { $0.uuid == Self.service }) else { return }

        peripheral.discoverCharacteristics([Self.frameCharacteristic, Self.settingsCharacteristic, Self.ackCharacteristic,
                                            Self.updateCharacteristic, Self.updateStatusCharacteristic,
                                            Self.versionCharacteristic, Self.authCharacteristic], for: service)
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

        let token = API.current.token

        if let auth = service.characteristics?.first(where: { $0.uuid == Self.authCharacteristic }), !token.isEmpty {
            let asked = Data((0..<GlassesProtocol.nonceBytes).map { _ in UInt8.random(in: .min ... .max) })

            challenge = asked
            peripheral.writeValue(asked, for: auth, type: .withResponse)
            peripheral.readValue(for: auth)
        } else {
            trusted = true
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
        case Self.authCharacteristic:
            answer(value, on: peripheral, for: characteristic)
        default:
            break
        }
    }

    private func answer(_ value: Data, on peripheral: CBPeripheral, for auth: CBCharacteristic) {
        guard let asked = challenge else { return }

        challenge = nil

        guard let reply = GlassesPackets.reply(to: value, token: API.current.token, phone: asked) else {
            updateStage = "These glasses don't know this server's token"
            return
        }

        peripheral.writeValue(reply, for: auth, type: .withResponse)
        trusted = true

        if let settings = lastSettings {
            write(settings, status: status.isEmpty ? nil : status)
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
    var busy = false
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
    private var flushing = false

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

    func waiting() -> Int {
        queued().count
    }

    func waiting(for recording: String) -> Int {
        queued().filter { Self.recording(of: $0) == recording }.count
    }

    func flush() async -> UploadResult {
        guard !flushing else { return UploadResult(sent: 0, waiting: queued().count, settings: nil, busy: true) }

        flushing = true
        defer { flushing = false }

        var sent = 0
        var settings: GlassesSettings?

        while let file = queued().first {
            guard let recording = Self.recording(of: file), let jpeg = try? Data(contentsOf: file) else {
                try? FileManager.default.removeItem(at: file)
                continue
            }

            do {
                settings = try await upload(jpeg, recording: recording, taken: Self.taken(of: file))
                sent += 1
            } catch APIError.status(let code) where code == 413 || code == 422 {
                try? FileManager.default.removeItem(at: file)
                continue
            } catch {
                break
            }

            try? FileManager.default.removeItem(at: file)
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

    private static func taken(of file: URL) -> Double? {
        file.lastPathComponent.split(separator: "-").first.flatMap { Double($0) }
    }

    private func upload(_ jpeg: Data, recording: String, taken: Double?) async throws -> GlassesSettings {
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
            notify.setValue(taken.map { String($0) }, forHTTPHeaderField: "X-Taken")
            slots[recording] = nil

            return try finished(await api.data(for: notify), for: recording)
        }

        var direct = framed(try api.request("/glasses/frames", method: "POST"), recording)
        direct.httpBody = jpeg
        direct.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
        direct.setValue(taken.map { String($0) }, forHTTPHeaderField: "X-Taken")

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
