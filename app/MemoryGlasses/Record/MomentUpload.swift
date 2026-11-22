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

        let links: DirectUploadLinks = try await send("/uploads", method: "POST", body: start)
        try await uploadDirectly(links, video: video, extras: extras, progress: progress)
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

        let _: UploadID = try await send("/uploads/\(links.id)/done", method: "POST", body: [String: String]())
    }
}
