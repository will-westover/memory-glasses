import Foundation

enum APIError: LocalizedError {
    case badAddress
    case status(Int)

    var errorDescription: String? {
        switch self {
        case .badAddress: "The server address isn't a valid URL."
        case .status(401): "The server refused the token."
        case .status(let code): "The server answered with error \(code)."
        }
    }
}

struct API {
    static let defaultAddress = built("ServerAddress") ?? "http://127.0.0.1:8000"
    static let defaultToken = built("ServerToken") ?? ""

    let address: String
    let token: String

    static var current: API {
        return API(
            address: saved("serverAddress") ?? defaultAddress,
            token: saved("serverToken") ?? defaultToken
        )
    }

    private static func built(_ key: String) -> String? {
        let value = Bundle.main.object(forInfoDictionaryKey: key) as? String

        return value?.isEmpty == false ? value : nil
    }

    private static func saved(_ key: String) -> String? {
        let value = UserDefaults.standard.string(forKey: key)

        return value?.isEmpty == false ? value : nil
    }

    static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .custom(decodeDate)

        return decoder
    }()

    static let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase

        return encoder
    }()

    @Sendable static func decodeDate(_ decoder: Decoder) throws -> Date {
        let container = try decoder.singleValueContainer()
        let text = try container.decode(String.self)
        let wholeSeconds = text.replacingOccurrences(of: #"\.\d+"#, with: "", options: .regularExpression)

        guard let date = ISO8601DateFormatter().date(from: wholeSeconds) else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "not a date: \(text)")
        }

        return date
    }

    func request(_ path: String, method: String = "GET", body: Data? = nil) throws -> URLRequest {
        guard let url = URL(string: address.trimmingCharacters(in: .whitespaces) + path) else {
            throw APIError.badAddress
        }

        var request = URLRequest(url: url)
        request.httpMethod = method
        request.httpBody = body
        request.timeoutInterval = 60

        if body != nil {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }

        if !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        return request
    }

    func data(for request: URLRequest) async throws -> Data {
        let (data, response) = try await URLSession.shared.data(for: request, delegate: TokenStaysHome())
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0

        guard status == 200 else {
            throw APIError.status(status)
        }

        return data
    }

    func get<Result: Decodable>(_ path: String) async throws -> Result {
        try API.decoder.decode(Result.self, from: await data(for: request(path)))
    }

    func send<Body: Encodable, Result: Decodable>(_ path: String, method: String, body: Body) async throws -> Result {
        let encoded = try API.encoder.encode(body)

        return try API.decoder.decode(Result.self, from: await data(for: request(path, method: method, body: encoded)))
    }

    func delete(_ path: String) async throws {
        _ = try await data(for: request(path, method: "DELETE"))
    }

    func download(_ path: String) async throws -> Data {
        try await data(for: request(path))
    }

    func audio(moment: String, from start: Double, to end: Double) async throws -> Data {
        try await data(for: request("/moments/\(moment)/audio?start=\(start)&end=\(end)"))
    }
}

func clock(_ seconds: Double) -> String {
    String(format: "%d:%02d", Int(seconds) / 60, Int(seconds) % 60)
}

func dollars(_ amount: Double) -> String {
    if amount == 0 {
        return "$0"
    }

    if amount < 0.0001 {
        return "under $0.0001"
    }

    return amount < 0.01 ? String(format: "$%.4f", amount) : String(format: "$%.2f", amount)
}

final class TokenStaysHome: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest) async -> URLRequest? {
        guard request.url?.host != task.originalRequest?.url?.host else { return request }

        var next = request
        next.setValue(nil, forHTTPHeaderField: "Authorization")

        return next
    }
}
