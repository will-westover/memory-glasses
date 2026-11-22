import CoreBluetooth
import Foundation

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
        settingsCharacteristic = nil
        ackCharacteristic = nil
        updateCharacteristic = nil
        versionIn = nil
        partialFrame = nil
        isAuthenticated = false
        phoneNonce = nil

        if firmwareTransfer != nil {
            firmwareTransfer = nil
            updateProgress = nil
            updateMessage = "The glasses disconnected during the update"
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

        peripheral.discoverCharacteristics([Self.frameCharacteristicID, Self.settingsCharacteristicID, Self.ackCharacteristicID,
                                            Self.updateCharacteristicID, Self.updateStatusCharacteristicID,
                                            Self.versionCharacteristicID, Self.authCharacteristicID], for: service)
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        for characteristic in service.characteristics ?? [] {
            switch characteristic.uuid {
            case Self.frameCharacteristicID:
                peripheral.setNotifyValue(true, for: characteristic)
            case Self.settingsCharacteristicID:
                settingsCharacteristic = characteristic
            case Self.ackCharacteristicID:
                ackCharacteristic = characteristic
            case Self.updateCharacteristicID:
                updateCharacteristic = characteristic
            case Self.updateStatusCharacteristicID:
                peripheral.setNotifyValue(true, for: characteristic)
            case Self.versionCharacteristicID:
                versionIn = characteristic
                peripheral.readValue(for: characteristic)
            default:
                break
            }
        }

        let token = API.current.token

        if let auth = service.characteristics?.first(where: { $0.uuid == Self.authCharacteristicID }), !token.isEmpty {
            let nonce = Data((0..<GlassesProtocol.nonceBytes).map { _ in UInt8.random(in: .min ... .max) })

            phoneNonce = nonce
            peripheral.writeValue(nonce, for: auth, type: .withResponse)
            peripheral.readValue(for: auth)
        } else {
            isAuthenticated = true
        }

        Task { @MainActor in
            if let settings: GlassesSettings = try? await API.current.get("/settings") {
                self.lastSettings = settings
                self.sendSettings(settings, displayText: self.glassesDisplayText.isEmpty ? nil : self.glassesDisplayText)
            }
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        guard let value = characteristic.value else { return }

        switch characteristic.uuid {
        case Self.frameCharacteristicID:
            handleFramePacket(value)
        case Self.updateStatusCharacteristicID:
            handleUpdateStatus(String(decoding: value, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines))
        case Self.versionCharacteristicID:
            versionArrived(String(decoding: value, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines))
        case Self.authCharacteristicID:
            completeHandshake(value, on: peripheral, for: characteristic)
        default:
            break
        }
    }

    func completeHandshake(_ value: Data, on peripheral: CBPeripheral, for auth: CBCharacteristic) {
        guard let nonce = phoneNonce else { return }

        phoneNonce = nil

        guard let reply = GlassesPackets.reply(to: value, token: API.current.token, phone: nonce) else {
            updateMessage = "These glasses don't know this server's token"
            return
        }

        peripheral.writeValue(reply, for: auth, type: .withResponse)
        isAuthenticated = true

        if let settings = lastSettings {
            sendSettings(settings, displayText: glassesDisplayText.isEmpty ? nil : glassesDisplayText)
        }
    }

    func peripheralIsReady(toSendWriteWithoutResponse peripheral: CBPeripheral) {
        sendNextFirmwareChunks()
    }
}
