import Foundation
import Observation

struct LiveStep: Decodable, Identifiable {
    let id = UUID()
    let kind: String
    let text: String
    let images: [String]

    enum CodingKeys: String, CodingKey {
        case kind, text, images
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        kind = try container.decodeIfPresent(String.self, forKey: .kind) ?? ""
        text = try container.decodeIfPresent(String.self, forKey: .text) ?? ""
        images = try container.decodeIfPresent([String].self, forKey: .images) ?? []
    }
}

struct Turn: Decodable, Identifiable {
    let id: UUID
    let question: String
    var answer: Answer?
    var problem: String?
    var steps: [LiveStep] = []
    var streamed = ""

    enum CodingKeys: String, CodingKey {
        case id, question, answer, problem
    }

    init(question: String) {
        id = UUID()
        self.question = question
    }
}

struct ConversationSummary: Decodable, Identifiable {
    let id: UUID
    let updatedAt: Date
    let title: String?
    let preview: String

    var label: String { title ?? preview }
}

struct ConversationDetail: Decodable {
    let id: UUID
    let turns: [Turn]
}

struct ChatQuestion: Encodable {
    let question: String
    var frameId: Int? = nil
}

struct FrameContext: Hashable {
    let id: Int
    let image: String?
    let at: Date
    let summary: String
}

struct Suggestions: Decodable {
    let questions: [String]
}

struct StreamText: Decodable {
    let text: String
}

struct StreamProblem: Decodable {
    let message: String
}

struct ConversationGroup: Identifiable {
    let title: String
    let conversations: [ConversationSummary]

    var id: String { title }
}

@MainActor
@Observable
final class ConversationStore {
    private(set) var summaries: [ConversationSummary] = []
    private(set) var turns: [UUID: [Turn]] = [:]
    private(set) var frameContexts: [UUID: FrameContext] = [:]
    private(set) var problem: String?
    private var query = ""

    func refresh() async {
        await search(query)
    }

    func search(_ text: String) async {
        query = text.trimmingCharacters(in: .whitespacesAndNewlines)

        var path = "/conversations"

        if !query.isEmpty, let encoded = query.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) {
            path += "?q=\(encoded)"
        }

        do {
            summaries = try await API.current.get(path)
            problem = nil
        } catch {
            problem = error.localizedDescription
        }
    }

    func load(_ id: UUID) async {
        do {
            let detail: ConversationDetail = try await API.current.get("/conversations/\(id)")
            turns[id] = detail.turns
        } catch {
            problem = error.localizedDescription
        }
    }

    func start(about frame: FrameContext? = nil) async -> UUID? {
        do {
            let detail: ConversationDetail = try await API.current.send("/conversations", method: "POST", body: [String: String]())
            turns[detail.id] = []
            frameContexts[detail.id] = frame

            return detail.id
        } catch {
            problem = error.localizedDescription

            return nil
        }
    }

    func title(of id: UUID) -> String? {
        summaries.first { $0.id == id }?.title ?? turns[id]?.first?.question
    }

    func ask(_ question: String, in id: UUID) async {
        let pending = Turn(question: question)
        turns[id, default: []].append(pending)

        let body = ChatQuestion(question: question, frameId: frameContexts[id]?.id)

        do {
            if let answered = try await stream(body, in: id, turn: pending.id) {
                updateTurn(pending.id, in: id) { $0 = answered }
            } else {
                let answered: Turn = try await API.current.send("/conversations/\(id)/ask", method: "POST", body: body)
                updateTurn(pending.id, in: id) { $0 = answered }
            }
        } catch {
            updateTurn(pending.id, in: id) { $0.problem = error.localizedDescription }
        }

        await refresh()
    }

    private func stream(_ body: ChatQuestion, in id: UUID, turn: UUID) async throws -> Turn? {
        var request = try API.current.request("/conversations/\(id)/ask/stream", method: "POST",
                                              body: API.encoder.encode(body))
        request.timeoutInterval = 180
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")

        let (bytes, response) = try await URLSession.shared.bytes(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0

        if status == 404 || status == 405 {
            return nil
        }

        guard status == 200 else {
            throw APIError.status(status)
        }

        var event = "message"

        for try await line in bytes.lines {
            if line.hasPrefix("event:") {
                event = line.dropFirst(6).trimmingCharacters(in: .whitespaces)
                continue
            }

            guard line.hasPrefix("data:") else { continue }

            let data = Data(line.dropFirst(5).trimmingCharacters(in: .whitespaces).utf8)

            switch event {
            case "step":
                if let step = try? API.decoder.decode(LiveStep.self, from: data) {
                    updateTurn(turn, in: id) { $0.steps.append(step) }
                }
            case "delta":
                if let delta = try? API.decoder.decode(StreamText.self, from: data) {
                    updateTurn(turn, in: id) { $0.streamed += delta.text }
                }
            case "done":
                return try API.decoder.decode(Turn.self, from: data)
            case "error":
                let problem = (try? API.decoder.decode(StreamProblem.self, from: data))?.message
                throw StreamFailure(message: problem ?? "The answer stopped partway.")
            default:
                break
            }
        }

        throw StreamFailure(message: "The answer stopped partway.")
    }

    func delete(_ ids: [UUID]) async {
        for id in ids {
            try? await API.current.delete("/conversations/\(id)")
            turns[id] = nil
            frameContexts[id] = nil
        }

        await refresh()
    }

    func groups() -> [ConversationGroup] {
        let calendar = Calendar.current
        let weekAgo = calendar.date(byAdding: .day, value: -7, to: .now) ?? .now

        let buckets: [(String, (ConversationSummary) -> Bool)] = [
            ("Today", { calendar.isDateInToday($0.updatedAt) }),
            ("Yesterday", { calendar.isDateInYesterday($0.updatedAt) }),
            ("Previous 7 days", { $0.updatedAt > weekAgo }),
            ("Older", { _ in true }),
        ]

        var remaining = summaries
        var groups: [ConversationGroup] = []

        for (title, belongs) in buckets {
            let members = remaining.filter(belongs)
            remaining.removeAll(where: belongs)

            if !members.isEmpty {
                groups.append(ConversationGroup(title: title, conversations: members))
            }
        }

        return groups
    }

    private func updateTurn(_ turnID: UUID, in id: UUID, change: (inout Turn) -> Void) {
        guard let index = turns[id]?.firstIndex(where: { $0.id == turnID }) else { return }

        change(&turns[id]![index])
    }
}

struct StreamFailure: LocalizedError {
    let message: String

    var errorDescription: String? { message }
}
