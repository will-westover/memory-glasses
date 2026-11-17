import Foundation
import Observation

struct Turn: Decodable, Identifiable {
    let id: UUID
    let question: String
    var answer: Answer?
    var problem: String?

    init(question: String) {
        id = UUID()
        self.question = question
    }
}

struct ConversationSummary: Decodable, Identifiable {
    let id: UUID
    let updatedAt: Date
    let title: String
    let preview: String
}

struct ConversationDetail: Decodable {
    let id: UUID
    let turns: [Turn]
}

struct ChatQuestion: Encodable {
    let question: String
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
    private(set) var problem: String?

    func refresh() async {
        do {
            summaries = try await API.current.get("/conversations")
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

    func start() async -> UUID? {
        do {
            let detail: ConversationDetail = try await API.current.send("/conversations", method: "POST", body: [String: String]())
            turns[detail.id] = []

            return detail.id
        } catch {
            problem = error.localizedDescription

            return nil
        }
    }

    func title(of id: UUID) -> String? {
        turns[id]?.first?.question ?? summaries.first { $0.id == id }?.title
    }

    func ask(_ question: String, in id: UUID) async {
        let pending = Turn(question: question)
        turns[id, default: []].append(pending)

        var answered: Turn?
        var failure: String?

        do {
            answered = try await API.current.send("/conversations/\(id)/ask", method: "POST",
                                                  body: ChatQuestion(question: question))
        } catch {
            failure = error.localizedDescription
        }

        replace(pending.id, in: id) { turn in
            if let answered {
                turn = answered
            } else {
                turn.problem = failure
            }
        }

        await refresh()
    }

    func delete(_ ids: [UUID]) async {
        for id in ids {
            try? await API.current.delete("/conversations/\(id)")
            turns[id] = nil
        }

        await refresh()
    }

    func groups(matching search: String) -> [ConversationGroup] {
        let calendar = Calendar.current
        let weekAgo = calendar.date(byAdding: .day, value: -7, to: .now) ?? .now

        let visible = summaries.filter {
            search.isEmpty || $0.title.localizedCaseInsensitiveContains(search)
                || $0.preview.localizedCaseInsensitiveContains(search)
        }

        let buckets: [(String, (ConversationSummary) -> Bool)] = [
            ("Today", { calendar.isDateInToday($0.updatedAt) }),
            ("Yesterday", { calendar.isDateInYesterday($0.updatedAt) }),
            ("Previous 7 days", { $0.updatedAt > weekAgo }),
            ("Older", { _ in true }),
        ]

        var remaining = visible
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

    private func replace(_ turnID: UUID, in id: UUID, change: (inout Turn) -> Void) {
        guard let index = turns[id]?.firstIndex(where: { $0.id == turnID }) else { return }

        change(&turns[id]![index])
    }
}
