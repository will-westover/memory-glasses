import Foundation

extension GlassesLink {
    @MainActor
    func startUpdate() async {
        guard let info = availableUpdate, firmwareTransfer == nil else { return }

        updateMessage = "Downloading the update…"
        updateProgress = 0

        guard let image = await Self.download(info.url) else {
            updateMessage = "Couldn't download the update"
            updateProgress = nil
            return
        }

        guard image.count == info.size, CRC32.of(image) == info.crc32 else {
            updateMessage = "The downloaded update was damaged; nothing was sent"
            updateProgress = nil
            return
        }

        guard state == .connected, updateCharacteristic != nil, firmwareTransfer == nil else {
            updateMessage = "The glasses disconnected before the update was sent"
            updateProgress = nil
            return
        }

        firmwareTransfer = (image, 0, 0, info.version, false)
        lastUpdateReplyAt = Date()
        updateMessage = "Sending the update…"

        Task { @MainActor in await self.cancelIfUpdateStalls(info.version) }

        writeUpdatePacket(GlassesPackets.updateBegin(size: image.count, crc: info.crc32))
    }

    static func download(_ link: String) async -> Data? {
        guard let url = URL(string: link), let (image, response) = try? await URLSession.shared.data(from: url),
              (response as? HTTPURLResponse)?.statusCode == 200 else { return nil }

        return image
    }

    @MainActor
    func cancelIfUpdateStalls(_ version: String) async {
        while firmwareTransfer?.version == version {
            try? await Task.sleep(for: .seconds(5))

            if firmwareTransfer?.version == version, Date().timeIntervalSince(lastUpdateReplyAt) > Self.updateStallTimeout {
                writeUpdatePacket(GlassesPackets.updateCancel)
                versionNotToRetry = version
                firmwareTransfer = nil
                updateProgress = nil
                updateMessage = "The update stalled; nothing was installed"
            }
        }
    }

    func cancelUpdate() {
        guard firmwareTransfer != nil else { return }

        writeUpdatePacket(GlassesPackets.updateCancel)
        firmwareTransfer = nil
        updateProgress = nil
        updateMessage = "Update cancelled"
    }

    func writeUpdatePacket(_ value: Data) {
        guard let peripheral, let updateCharacteristic, peripheral.state == .connected else { return }

        peripheral.writeValue(value, for: updateCharacteristic, type: .withoutResponse)
    }

    func sendNextFirmwareChunks() {
        guard let peripheral, let transfer = firmwareTransfer, !transfer.endSent else { return }

        let chunk = max(peripheral.maximumWriteValueLength(for: .withoutResponse) - 1, 20)
        var sent = transfer.bytesSent

        while sent < transfer.image.count, sent - transfer.bytesConfirmed < Self.maxUnconfirmedBytes, peripheral.canSendWriteWithoutResponse {
            let end = min(sent + chunk, transfer.image.count, transfer.bytesConfirmed + Self.maxUnconfirmedBytes)
            writeUpdatePacket(GlassesPackets.updateData(transfer.image.subdata(in: sent..<end)))
            sent = end
        }

        firmwareTransfer?.bytesSent = sent
        updateProgress = Double(transfer.bytesConfirmed) / Double(transfer.image.count)

        if sent == transfer.image.count, peripheral.canSendWriteWithoutResponse {
            writeUpdatePacket(GlassesPackets.updateEnd)
            firmwareTransfer?.endSent = true
        }
    }

    func handleUpdateStatus(_ text: String) {
        guard firmwareTransfer != nil else { return }

        lastUpdateReplyAt = Date()

        switch UpdateStatus(text) {
        case .ready:
            sendNextFirmwareChunks()
        case .got(let bytes):
            firmwareTransfer?.bytesConfirmed = bytes
            sendNextFirmwareChunks()
        case .done:
            versionAfterRestart = firmwareTransfer?.version
            firmwareTransfer = nil
            updateProgress = 1
            updateMessage = "Restarting…"
        case .error(let reason):
            versionNotToRetry = firmwareTransfer?.version
            firmwareTransfer = nil
            updateProgress = nil
            updateMessage = "Update failed: \(reason)"
        case nil:
            break
        }
    }

    func versionArrived(_ version: String) {
        glassesVersion = version

        if let expected = versionAfterRestart {
            updateMessage = version == expected ? "Updated to \(version)" : "The glasses still run \(version)"
            versionNotToRetry = version == expected ? nil : expected
            updateProgress = nil
            versionAfterRestart = nil
        }

        Task { @MainActor in await self.checkForUpdate() }
    }

    @MainActor
    func checkForUpdate(install: Bool = true) async {
        guard let version = glassesVersion, !isCheckingForUpdate, firmwareTransfer == nil else { return }

        isCheckingForUpdate = true
        defer { isCheckingForUpdate = false }

        let latest: FirmwareRelease? = try? await API.current.get("/glasses/firmware")
        availableUpdate = latest.flatMap { $0.version > version ? $0 : nil }

        guard let found = availableUpdate else {
            if updateProgress == nil, versionAfterRestart == nil, !(updateMessage ?? "").hasPrefix("Updated") {
                updateMessage = "Up to date"
            }
            return
        }

        if install, canUpdate, found.version != versionNotToRetry {
            await startUpdate()
        }
    }

}
