import Foundation

struct FlushResult {
    let sent: Int
    let waiting: Int
    let settings: GlassesSettings?
    var busy = false
}

private struct PresignedFrameUpload: Decodable {
    let uploadUrl: String?
    let uploadName: String?
}

actor FrameUploader {
    private static let spaceToLeaveFree: Int64 = 500 << 20

    private let folder: URL
    private var presignedUploads: [String: (url: URL, name: String)] = [:]
    private var fileSequence = 0
    private var flushing = false

    init() {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        folder = base.appendingPathComponent("glasses-queue", isDirectory: true)
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    }

    func enqueue(_ frame: ReceivedFrame) {
        fileSequence += 1
        let name = String(format: "%.3f-%06d-%@-%u.jpg", Date().timeIntervalSince1970, fileSequence, frame.recording, frame.number)
        try? frame.jpeg.write(to: folder.appendingPathComponent(name))

        var waiting = queued()

        while freeSpace() < Self.spaceToLeaveFree, waiting.count > 1 {
            try? FileManager.default.removeItem(at: waiting.removeFirst())
        }
    }

    private func freeSpace() -> Int64 {
        let values = try? folder.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])

        return values?.volumeAvailableCapacityForImportantUsage ?? .max
    }

    func waiting() -> Int {
        queued().count
    }

    func waiting(for recording: String) -> Int {
        queued().filter { Self.recording(of: $0) == recording }.count
    }

    func flush() async -> FlushResult {
        guard !flushing else { return FlushResult(sent: 0, waiting: queued().count, settings: nil, busy: true) }

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

        return FlushResult(sent: sent, waiting: queued().count, settings: settings)
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

        if presignedUploads[recording] == nil {
            storePresignedUpload(from: try await api.data(for: withRecordingHeader(api.request("/settings"), recording)), for: recording)
        }

        if let slot = presignedUploads[recording] {
            if try await !put(jpeg, to: slot.url) {
                presignedUploads[recording] = nil
                storePresignedUpload(from: try await api.data(for: withRecordingHeader(api.request("/settings"), recording)), for: recording)

                guard let fresh = presignedUploads[recording], try await put(jpeg, to: fresh.url) else { throw APIError.status(0) }
            }

            var notify = withRecordingHeader(try api.request("/glasses/frames", method: "POST"), recording)
            notify.setValue(presignedUploads[recording]?.name, forHTTPHeaderField: "X-Uploaded")
            notify.setValue(taken.map { String($0) }, forHTTPHeaderField: "X-Taken")
            presignedUploads[recording] = nil

            return try settings(from: await api.data(for: notify), for: recording)
        }

        var direct = withRecordingHeader(try api.request("/glasses/frames", method: "POST"), recording)
        direct.httpBody = jpeg
        direct.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
        direct.setValue(taken.map { String($0) }, forHTTPHeaderField: "X-Taken")

        return try settings(from: await api.data(for: direct), for: recording)
    }

    private func withRecordingHeader(_ request: URLRequest, _ recording: String) -> URLRequest {
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

    private func storePresignedUpload(from reply: Data, for recording: String) {
        guard let slot = try? API.decoder.decode(PresignedFrameUpload.self, from: reply),
              let link = slot.uploadUrl, let url = URL(string: link), let name = slot.uploadName else { return }

        presignedUploads[recording] = (url, name)
    }

    private func settings(from reply: Data, for recording: String) throws -> GlassesSettings {
        storePresignedUpload(from: reply, for: recording)

        return try API.decoder.decode(GlassesSettings.self, from: reply)
    }
}
