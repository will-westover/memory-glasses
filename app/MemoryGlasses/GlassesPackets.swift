import CryptoKit
import Foundation

enum FramePacket: Equatable {
    case start(length: Int, number: UInt32, recording: String)
    case data(Data)
    case end(crc: UInt32)

    init?(_ packet: Data) {
        guard let kind = packet.first else { return nil }

        let rest = packet.dropFirst()

        switch kind {
        case GlassesProtocol.frameStart where rest.count >= 8:
            let name = String(decoding: rest.dropFirst(8).prefix(GlassesProtocol.recordingBytes), as: UTF8.self)
                .trimmingCharacters(in: .controlCharacters.union(.whitespaces))
            self = .start(length: Int(rest.uint32(at: 0)), number: rest.uint32(at: 4), recording: name)
        case GlassesProtocol.frameData:
            self = .data(Data(rest))
        case GlassesProtocol.frameEnd where rest.count >= 4:
            self = .end(crc: rest.uint32(at: 0))
        default:
            return nil
        }
    }
}

enum UpdateStatus: Equatable {
    case ready
    case got(Int)
    case done
    case error(String)

    init?(_ text: String) {
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)

        if text == GlassesProtocol.statusReady {
            self = .ready
        } else if text == GlassesProtocol.statusDone {
            self = .done
        } else if text.hasPrefix(GlassesProtocol.statusGot), let bytes = Int(text.dropFirst(GlassesProtocol.statusGot.count)) {
            self = .got(bytes)
        } else if text.hasPrefix(GlassesProtocol.statusError) {
            self = .error(text.dropFirst(GlassesProtocol.statusError.count).trimmingCharacters(in: .whitespaces))
        } else {
            return nil
        }
    }
}

enum GlassesPackets {
    static func ack(_ number: UInt32) -> Data {
        number.littleBytes
    }

    static func updateBegin(size: Int, crc: UInt32) -> Data {
        Data([GlassesProtocol.updateBegin]) + UInt32(size).littleBytes + crc.littleBytes
    }

    static func updateData(_ piece: Data) -> Data {
        Data([GlassesProtocol.updateData]) + piece
    }

    static let updateEnd = Data([GlassesProtocol.updateEnd])
    static let updateCancel = Data([GlassesProtocol.updateCancel])

    static func proof(token: String, label: UInt8, phone: Data, glasses: Data) -> Data {
        Data(HMAC<SHA256>.authenticationCode(for: Data([label]) + phone + glasses,
                                             using: SymmetricKey(data: Data(token.utf8))))
    }

    static func reply(to answer: Data, token: String, phone: Data) -> Data? {
        guard answer.count == GlassesProtocol.nonceBytes + GlassesProtocol.shortProofBytes else { return nil }

        let glasses = Data(answer.prefix(GlassesProtocol.nonceBytes))
        let expected = proof(token: token, label: GlassesProtocol.glassesLabel, phone: phone, glasses: glasses)

        guard Data(answer.suffix(GlassesProtocol.shortProofBytes)) == expected.prefix(GlassesProtocol.shortProofBytes) else {
            return nil
        }

        return proof(token: token, label: GlassesProtocol.phoneLabel, phone: phone, glasses: glasses)
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

extension UInt32 {
    var littleBytes: Data {
        var value = littleEndian

        return Data(bytes: &value, count: 4)
    }
}

extension Data {
    func uint32(at offset: Int) -> UInt32 {
        let start = index(startIndex, offsetBy: offset)

        return self[start..<index(start, offsetBy: 4)].enumerated().reduce(UInt32(0)) { value, pair in
            value | UInt32(pair.element) << (8 * UInt32(pair.offset))
        }
    }
}
