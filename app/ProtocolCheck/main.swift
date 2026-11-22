import Foundation

struct ProtocolVectors: Decodable {
    struct Sum: Decodable { let data: String; let crc: UInt32 }
    struct Frame: Decodable { let length: Int; let number: UInt32; let recording, start, piece, data, end: String; let crc: UInt32 }
    struct Ack: Decodable { let number: UInt32; let packet: String }
    struct Update: Decodable { let size: Int; let crc: UInt32; let begin, piece, data, end, cancel: String; let step: Int }
    struct Status: Decodable { let text, kind: String; let bytes: Int?; let reason: String? }
    struct Auth: Decodable { let token, phone, glasses, answer, reply: String }

    let crc32: Sum
    let frame: Frame
    let ack: Ack
    let update: Update
    let statuses: [Status]
    let auth: Auth
}

func hexBytes(_ hex: String) -> Data {
    Data(stride(from: 0, to: hex.count, by: 2).map { offset in
        UInt8(hex.dropFirst(offset).prefix(2), radix: 16)!
    })
}

var failures = 0

func check(_ name: String, _ passed: Bool) {
    print("\(passed ? "ok  " : "FAIL") \(name)")
    failures += passed ? 0 : 1
}

let cases = try JSONDecoder().decode(ProtocolVectors.self, from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])))

check("crc32", CRC32.of(hexBytes(cases.crc32.data)) == cases.crc32.crc)
check("frame start", FramePacket(hexBytes(cases.frame.start)) == .start(length: cases.frame.length, number: cases.frame.number,
                                                                      recording: cases.frame.recording))
check("frame data", FramePacket(hexBytes(cases.frame.data)) == .data(hexBytes(cases.frame.piece)))
check("frame end", FramePacket(hexBytes(cases.frame.end)) == .end(crc: cases.frame.crc))
check("ack", GlassesPackets.ack(cases.ack.number) == hexBytes(cases.ack.packet))
check("update begin", GlassesPackets.updateBegin(size: cases.update.size, crc: cases.update.crc) == hexBytes(cases.update.begin))
check("update data", GlassesPackets.updateData(hexBytes(cases.update.piece)) == hexBytes(cases.update.data))
check("update end", GlassesPackets.updateEnd == hexBytes(cases.update.end))
check("update cancel", GlassesPackets.updateCancel == hexBytes(cases.update.cancel))
check("update step", GlassesProtocol.updateStep == cases.update.step)

for status in cases.statuses {
    let expected: UpdateStatus? = switch status.kind {
    case "ready": .ready
    case "got": .got(status.bytes ?? -1)
    case "done": .done
    case "error": .error(status.reason ?? "")
    default: nil
    }

    check("status \(status.text)", UpdateStatus(status.text) == expected)
}

let reply = GlassesPackets.reply(to: hexBytes(cases.auth.answer), token: cases.auth.token, phone: hexBytes(cases.auth.phone))
check("handshake reply", reply == hexBytes(cases.auth.reply))
check("handshake refuses a wrong token", GlassesPackets.reply(to: hexBytes(cases.auth.answer), token: "wrong",
                                                              phone: hexBytes(cases.auth.phone)) == nil)

exit(failures == 0 ? 0 : 1)
