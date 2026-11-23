import Foundation

struct MomentSummary: Decodable, Identifiable, Hashable {
    let id: String
    let source: String
    let startedAt: Date
    let durationSeconds: Double
    let frames: Int
    let hasSound: Bool
    let place: String?
    let cover: String?
    var title: String? = nil
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
    let people: [PersonTag]
    let position: Position?
    let room: String?
    var short: String? = nil
}

struct Position: Decodable, Hashable {
    let x: Double
    let y: Double
    let z: Double
    let yaw: Double
}

struct WalkedPath: Decodable {
    let points: [[Double]]
}

struct SpokenLine: Decodable, Hashable {
    let start: Double
    let end: Double
    let speaker: String?
    let text: String
}

extension Array where Element == SpokenLine {
    var sentences: [SpokenLine] {
        var joined: [SpokenLine] = []

        for line in self {
            if let last = joined.last, !last.text.endsWithSentence, line.start - last.end < 1.5 {
                joined[joined.count - 1] = SpokenLine(start: last.start, end: line.end, speaker: nil, text: "\(last.text) \(line.text)")
            } else {
                joined.append(SpokenLine(start: line.start, end: line.end, speaker: nil, text: line.text))
            }
        }

        return joined
    }
}

private extension String {
    var endsWithSentence: Bool {
        guard let last = trimmingCharacters(in: .whitespaces).last else { return false }

        return ".?!".contains(last)
    }
}

struct Fact: Codable, Identifiable, Hashable {
    let id: Int
    let kind: String
    let at: Date
    let momentId: String
    let offsetSeconds: Double
    let text: String
    let image: String?
    var endSeconds: Double? = nil
    var audioUrl: String? = nil
    var speaker: String? = nil
    var people: [PersonTag]? = nil
    var place: SourcePlace? = nil
}

struct SourcePlace: Codable, Hashable {
    let id: Int
    let name: String?
    let lat: Double?
    let lon: Double?
}

struct MomentDetail: Decodable {
    let id: String
    let frames: [Frame]
    let transcript: [SpokenLine]
    let summaries: [Fact]
    let hasVideo: Bool
    let framesPerMinute: Double?
    let hasMesh: Bool
    let place: PlaceTag?
    var title: String? = nil
    var overview: String? = nil
    var chapters: [Chapter]? = nil
}

struct Chapter: Decodable, Identifiable, Hashable {
    let id: Int
    let start: Double
    let end: Double
    let title: String?
    let text: String

    var label: String {
        if let title, !title.isEmpty { return title }

        let words = text.split(separator: " ")

        return words.prefix(6).joined(separator: " ") + (words.count > 6 ? "…" : "")
    }
}

struct PlaceTag: Decodable, Hashable {
    let id: Int
    let name: String?
}

struct Place: Decodable, Identifiable, Hashable {
    let id: Int
    let name: String?
    let updatedAt: Date
    let moments: Int
    let hasMap: Bool
    let rooms: [String]

    var label: String { name ?? "Unnamed place" }
}

struct RoomInfo: Decodable, Identifiable, Hashable {
    let id: Int
    let name: String
    let description: String?
    let frames: Int
}

struct PlaceRooms: Decodable {
    let id: Int
    let name: String?
    let rooms: [RoomInfo]
    let momentIds: [String]
}

struct Answer: Codable {
    let answer: String
    let found: Bool
    let sources: [Fact]
    let modelCalls: Int
    let seconds: Double
    let usd: Double
    var highlight: Int? = nil
    var highlightReason: String? = nil
    var layout: String? = nil
    var followUps: [String]? = nil
    var nextSteps: [NextStep]? = nil

    var featuredSource: Fact? {
        guard let highlight, sources.indices.contains(highlight) else { return sources.first { $0.image != nil } }

        return sources[highlight]
    }
}

struct NextStep: Codable, Hashable {
    let label: String
    let question: String
}

struct Counts: Decodable {
    let moments: Int
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

struct FrameRate: Encodable {
    let framesPerMinute: Double
}

struct GlassesSettings: Codable, Equatable {
    var framesPerMinute = 30.0
    var displayScale = 1.0
    var displayX = 0.0
    var displayY = 0.0
    var displayBrightness = 1.0
    var displayLargeText = false
    var displayMirror = false
    var displayFlip = false
    var displayPattern = false
    var recording = true
}

struct GlassesStatus: Decodable {
    let sending: Bool
    let photos: Int
    let secondsSinceLast: Double?
    let processing: Int
}

struct MomentRoute: Hashable {
    let id: String
    let startedAt: Date
    var focus: Double? = nil
}

extension Fact {
    var moment: MomentRoute {
        MomentRoute(id: momentId, startedAt: at.addingTimeInterval(-offsetSeconds), focus: offsetSeconds)
    }

    var isSpeech: Bool { kind == "speech" || kind == "said" }
}

func sourceIcon(_ source: String) -> (symbol: String, label: String) {
    switch source {
    case "iphone": ("iphone", "Recorded on iPhone")
    case "glasses": ("eyeglasses", "Recorded on the glasses")
    default: ("film", "Imported video")
    }
}

struct WorldMoment: Decodable, Identifiable, Hashable {
    let id: String
    let source: String
    let startedAt: Date
    let durationSeconds: Double
    let lat: Double
    let lon: Double
    let place: PlaceTag?
    let cover: String?
}

struct World: Decodable {
    let places: [WorldPlace]
    let moments: [WorldMoment]
}

struct WorldPlace: Decodable, Identifiable, Hashable {
    let id: Int
    let name: String?
    let lat: Double
    let lon: Double
    let moments: Int
    let hasMap: Bool
    let rooms: [String]

    var label: String { name ?? "Unnamed place" }
}

struct SceneMoment: Decodable, Identifiable {
    let id: String
    let startedAt: Date
    let path: [[Double]]
    let frames: [Frame]
}

struct PlaceScene: Decodable {
    let id: Int
    let meshMoment: String?
    let moments: [SceneMoment]
}

struct SearchHits: Decodable {
    let results: [Fact]
}
