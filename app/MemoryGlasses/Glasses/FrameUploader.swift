import Foundation

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
