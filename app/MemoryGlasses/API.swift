import Foundation

struct SessionSummary: Decodable, Identifiable, Hashable {
    let id: String
    let startedAt: Date
    let durationSeconds: Double
    let frames: Int
    let hasSound: Bool
    let place: String?
    let cover: String?
}

struct SeenObject: Decodable, Hashable {
    let name: String
    let location: String
}

struct SeenText: Decodable, Hashable {
    let text: String
    let surface: String
}

struct Frame: Decodable, Identifiable {
    let id: Int
    let at: Date
    let offsetSeconds: Double
    let image: String?
    let summary: String
    let objects: [SeenObject]
    let texts: [SeenText]
}

struct SpokenLine: Decodable, Hashable {
    let start: Double
    let speaker: String?
    let text: String
}

struct Fact: Codable, Identifiable {
    let id: Int
    let kind: String
    let at: Date
    let sessionId: String
    let offsetSeconds: Double
    let text: String
    let image: String?
}

struct SessionDetail: Decodable {
    let id: String
    let frames: [Frame]
    let transcript: [SpokenLine]
    let summaries: [Fact]
}

struct Answer: Codable {
    let answer: String
    let found: Bool
    let sources: [Fact]
    let modelCalls: Int
    let seconds: Double
    let usd: Double
}

struct Counts: Decodable {
    let sessions: Int
    let frames: Int
    let facts: Int
}

struct StepCost: Decodable, Identifiable {
    let step: String
    let calls: Int
    let usd: Double

    var id: String { step }
}

struct Scores: Decodable {
    let questions: Int
    let score: Double
    let supported: Double
    let retrieved: Double
    let seconds: Double
    let usd: Double
}

struct EvalRun: Decodable {
    let run: String
    let model: String
    let judge: String
    let scores: [String: Scores]
}

struct Stats: Decodable {
    let counts: Counts
    let costs: [StepCost]
    let totalUsd: Double
    let eval: EvalRun?
}

struct GlassesSettings: Codable {
    var framesPerMinute: Double
}

struct PastExchange: Encodable {
    let question: String
    let answer: String
}

struct Question: Encodable {
    let question: String
    let plain: Bool
    let history: [PastExchange]
}

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
    static let defaultAddress = "http://127.0.0.1:8000"

    let address: String
    let token: String

    static var current: API {
        let defaults = UserDefaults.standard

        return API(
            address: defaults.string(forKey: "serverAddress") ?? defaultAddress,
            token: defaults.string(forKey: "serverToken") ?? ""
        )
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
        let (data, response) = try await URLSession.shared.data(for: request)
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

    func image(_ path: String) async throws -> Data {
        try await data(for: request(path))
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
