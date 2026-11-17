import Foundation
import Observation

struct Turn: Codable, Identifiable {
    var id = UUID()
    let question: String
    var answer: Answer?
    var problem: String?
}

struct Conversation: Codable, Identifiable {
    var id = UUID()
    var turns: [Turn] = []
    var updated = Date()

    var title: String { turns.first?.question ?? "New chat" }

    var preview: String {
        guard let last = turns.last else { return "" }

        return last.answer?.answer ?? last.problem ?? "Waiting for an answer…"
    }
}

struct ConversationGroup: Identifiable {
    let title: String
    let conversations: [Conversation]

    var id: String { title }
}

@MainActor
@Observable
final class ConversationStore {
    static let historyLimit = 6

    private(set) var conversations: [Conversation] = []

    private let file = URL.documentsDirectory.appending(path: "conversations.json")

    init() {
        guard let data = try? Data(contentsOf: file),
              let saved = try? JSONDecoder().decode([Conversation].self, from: data) else { return }

        conversations = saved
    }

    func conversation(_ id: UUID) -> Conversation? {
        conversations.first { $0.id == id }
    }

    func start() -> UUID {
        let conversation = Conversation()
        conversations.insert(conversation, at: 0)

        return conversation.id
    }

    func delete(_ ids: [UUID]) {
        conversations.removeAll { ids.contains($0.id) }
        save()
    }

    func removeEmpty() {
        conversations.removeAll { $0.turns.isEmpty }
    }

    func groups(matching search: String) -> [ConversationGroup] {
        let calendar = Calendar.current
        let weekAgo = calendar.date(byAdding: .day, value: -7, to: .now) ?? .now

        let visible = conversations
            .filter { !$0.turns.isEmpty }
            .filter { search.isEmpty || matches($0, search) }
            .sorted { $0.updated > $1.updated }

        let buckets: [(String, (Conversation) -> Bool)] = [
            ("Today", { calendar.isDateInToday($0.updated) }),
            ("Yesterday", { calendar.isDateInYesterday($0.updated) }),
            ("Previous 7 days", { $0.updated > weekAgo }),
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

    func ask(_ question: String, in id: UUID) async {
        guard let index = index(of: id) else { return }

        let history = conversations[index].turns
            .compactMap { turn in turn.answer.map { PastExchange(question: turn.question, answer: $0.answer) } }
            .suffix(Self.historyLimit)

        let turn = Turn(question: question)
        conversations[index].turns.append(turn)
        conversations[index].updated = .now
        save()

        var answer: Answer?
        var problem: String?

        do {
            answer = try await API.current.send(
                "/ask", method: "POST", body: Question(question: question, plain: false, history: Array(history))
            )
        } catch {
            problem = error.localizedDescription
        }

        update(turn.id, in: id) {
            $0.answer = answer
            $0.problem = problem
        }
    }

    private func matches(_ conversation: Conversation, _ search: String) -> Bool {
        conversation.turns.contains {
            $0.question.localizedCaseInsensitiveContains(search)
                || ($0.answer?.answer.localizedCaseInsensitiveContains(search) ?? false)
        }
    }

    private func index(of id: UUID) -> Int? {
        conversations.firstIndex { $0.id == id }
    }

    private func update(_ turnID: UUID, in id: UUID, change: (inout Turn) -> Void) {
        guard let index = index(of: id),
              let turnIndex = conversations[index].turns.firstIndex(where: { $0.id == turnID }) else { return }

        change(&conversations[index].turns[turnIndex])
        conversations[index].updated = .now
        save()
    }

    private func save() {
        let saved = conversations.filter { !$0.turns.isEmpty }

        try? JSONEncoder().encode(saved).write(to: file, options: .atomic)
    }
}
