import Foundation

extension GlassesLink {
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

    static func download(_ link: String) async -> Data? {
        guard let url = URL(string: link), let (image, response) = try? await URLSession.shared.data(from: url),
              (response as? HTTPURLResponse)?.statusCode == 200 else { return nil }

        return image
    }

    @MainActor
    func watchUpdate(_ version: String) async {
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

    func sendUpdate(_ value: Data) {
        guard let peripheral, let updateOut, peripheral.state == .connected else { return }

        peripheral.writeValue(value, for: updateOut, type: .withoutResponse)
    }

    func pump() {
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

    func updateSaid(_ text: String) {
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

    func versionArrived(_ version: String) {
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

}
