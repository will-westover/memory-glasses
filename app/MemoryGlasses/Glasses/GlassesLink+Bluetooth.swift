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

    func answer(_ value: Data, on peripheral: CBPeripheral, for auth: CBCharacteristic) {
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
