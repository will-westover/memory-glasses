import Foundation

struct UploadStatus: Decodable, Identifiable {
    let id: UUID
    let receivedAt: Date
    let status: String
    let step: String?
    let momentId: String?
    let error: String?
}

final class UploadProgress: NSObject, URLSessionTaskDelegate {
    let report: (Double) -> Void

    init(report: @escaping (Double) -> Void) {
        self.report = report
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64,
                    totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
        guard totalBytesExpectedToSend > 0 else { return }

        report(Double(totalBytesSent) / Double(totalBytesExpectedToSend))
    }
}

struct MultipartBody {
    let boundary = "memory-glasses-\(UUID().uuidString)"

    func write(fields: [String: String], files: [(field: String, url: URL)], to target: URL) throws {
        FileManager.default.createFile(atPath: target.path, contents: nil)
        let out = try FileHandle(forWritingTo: target)
        defer { try? out.close() }

        for (name, value) in fields {
            out.write(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"\r\n\r\n\(value)\r\n".utf8))
        }

        for file in files {
            out.write(Data(("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(file.field)\"; " +
                            "filename=\"\(file.url.lastPathComponent)\"\r\nContent-Type: application/octet-stream\r\n\r\n").utf8))

            let source = try FileHandle(forReadingFrom: file.url)
            while let chunk = try source.read(upToCount: 4 * 1024 * 1024), !chunk.isEmpty {
                out.write(chunk)
            }
            try source.close()

            out.write(Data("\r\n".utf8))
        }

        out.write(Data("--\(boundary)--\r\n".utf8))
    }
}

struct DirectUpload: Encodable {
    let framesPerMinute: Double
    let placeId: Int?
    let videoSuffix: String
    let extras: [String]
}

struct DirectUploadLinks: Decodable {
    let id: UUID
    let urls: [String: String]
}

extension API {
    func uploadMoment(video: URL, framesPerMinute: Double, placeId: Int? = nil, extras: [(field: String, url: URL)] = [],
                      progress: @escaping (Double) -> Void) async throws {
        let start = DirectUpload(framesPerMinute: framesPerMinute.rounded(), placeId: placeId,
                                 videoSuffix: "." + video.pathExtension.lowercased(), extras: extras.map(\.field))

        do {
            let links: DirectUploadLinks = try await send("/uploads", method: "POST", body: start)
            try await uploadDirectly(links, video: video, extras: extras, progress: progress)
        } catch APIError.status(409) {
            try await uploadThroughServer(video: video, framesPerMinute: framesPerMinute, placeId: placeId,
                                          extras: extras, progress: progress)
        }
    }

    private func uploadDirectly(_ links: DirectUploadLinks, video: URL, extras: [(field: String, url: URL)],
                                progress: @escaping (Double) -> Void) async throws {
        for (field, file) in extras + [("video", video)] {
            guard let link = links.urls[field], let url = URL(string: link) else { continue }

            var request = URLRequest(url: url)
            request.httpMethod = "PUT"
            request.timeoutInterval = 600

            let delegate = field == "video" ? UploadProgress(report: progress) : nil
            let (_, response) = try await URLSession.shared.upload(for: request, fromFile: file, delegate: delegate)
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0

            guard status == 200 else {
                throw APIError.status(status)
            }
        }

        let _: UploadStarted = try await send("/uploads/\(links.id)/done", method: "POST", body: [String: String]())
    }

    private func uploadThroughServer(video: URL, framesPerMinute: Double, placeId: Int?,
                                     extras: [(field: String, url: URL)], progress: @escaping (Double) -> Void) async throws {
        let body = MultipartBody()
        let bodyFile = FileManager.default.temporaryDirectory.appending(path: "upload-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: bodyFile) }

        var fields = ["frames_per_minute": String(Int(framesPerMinute))]

        if let placeId {
            fields["place_id"] = String(placeId)
        }

        try body.write(fields: fields,
                       files: [("video", video)] + extras, to: bodyFile)

        var request = try request("/moments", method: "POST")
        request.setValue("multipart/form-data; boundary=\(body.boundary)", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 600

        let (_, response) = try await URLSession.shared.upload(for: request, fromFile: bodyFile,
                                                                delegate: UploadProgress(report: progress))
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0

        guard status == 200 else {
            throw APIError.status(status)
        }
    }
}
