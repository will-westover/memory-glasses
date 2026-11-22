import SwiftUI

struct ChatView: View {
    let id: UUID
    let newChat: () -> Void

    @Environment(ConversationStore.self) private var store
    @Environment(Router.self) private var router

    @State private var draft = ""
    @State private var suggestions: [String]?
    @FocusState private var composing: Bool

    private let fallbackSuggestions = [
        "What did I do today?",
        "Summarize my last recording",
        "What was said in my last conversation?",
        "What text did I see?",
    ]

    var body: some View {
        let turns = store.turns[id] ?? []
        let busy = turns.last.map { $0.answer == nil && $0.problem == nil } ?? false

        ScrollView {
            LazyVStack(alignment: .leading, spacing: 32) {
                if let context = store.contexts[id] {
                    FrameContextCard(context: context)
                }

                if turns.isEmpty {
                    welcome
                }

                ForEach(turns) { turn in
                    TurnView(turn: turn, latest: turn.id == turns.last?.id, ask: send)
                }
            }
            .padding(.horizontal, 20)
            .padding(.top, 12)
            .padding(.bottom, 24)
        }
        .defaultScrollAnchor(.bottom)
        .scrollDismissesKeyboard(.interactively)
        .safeAreaInset(edge: .bottom) {
            Composer(text: $draft, busy: busy, focused: $composing) { send(draft) }
        }
        .navigationTitle(turns.isEmpty ? "New chat" : store.title(of: id) ?? "")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button(action: newChat) { Image(systemName: "square.and.pencil") }
                    .disabled(turns.isEmpty)
                    .accessibilityLabel("New chat")
            }
        }
        .task { await prepare() }
    }

    private var welcome: some View {
        VStack(alignment: .leading, spacing: 20) {
            if store.contexts[id] == nil {
                Image(systemName: "eyeglasses")
                    .font(.system(size: 30, weight: .light))
                    .foregroundStyle(.secondary)

                Text("What would you like to remember?")
                    .font(.title2.weight(.semibold))

                if let suggestions {
                    suggestionList(suggestions)
                } else {
                    suggestionList(["Looking at your recent recordings", "A second suggestion", "One more"])
                        .redacted(reason: .placeholder)
                        .disabled(true)
                }
            } else {
                Text("Ask anything about this photo.")
                    .font(.title3.weight(.semibold))
            }
        }
        .padding(.top, store.contexts[id] == nil ? 80 : 0)
    }

    private func suggestionList(_ questions: [String]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(questions, id: \.self) { suggestion in
                Button { send(suggestion) } label: {
                    Text(suggestion)
                        .font(.subheadline)
                        .multilineTextAlignment(.leading)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 9)
                        .overlay(Capsule().stroke(Color(.separator)))
                }
                .buttonStyle(.plain)
            }
        }
    }

    private func prepare() async {
        if store.turns[id] == nil {
            await store.load(id)
        }

        guard (store.turns[id] ?? []).isEmpty, store.contexts[id] == nil else { return }

        composing = true
        await loadSuggestions()
    }

    private func loadSuggestions() async {
        if let loaded: Suggestions = try? await API.current.get("/suggestions"), !loaded.questions.isEmpty {
            suggestions = loaded.questions
        } else {
            suggestions = fallbackSuggestions
        }
    }

    private func send(_ text: String) {
        let question = text.trimmingCharacters(in: .whitespacesAndNewlines)

        guard !question.isEmpty else { return }

        draft = ""
        router.clearTrail()
        Task { await store.ask(question, in: id) }
    }
}

struct FrameContextCard: View {
    let context: FrameContext

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            FrameImage(path: context.image)
                .frame(width: 96, height: 72)
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))

            VStack(alignment: .leading, spacing: 4) {
                Text(context.at.formatted(.dateTime.weekday(.wide).hour().minute()))
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)

                Text(context.summary)
                    .font(.subheadline)
                    .lineLimit(3)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
    }
}

struct TurnView: View {
    let turn: Turn
    let latest: Bool
    let ask: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(turn.question)
                .padding(.horizontal, 16)
                .padding(.vertical, 11)
                .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 20, style: .continuous))
                .padding(.leading, 56)
                .frame(maxWidth: .infinity, alignment: .trailing)

            if let answer = turn.answer {
                AnswerView(answer: answer, question: turn.question, latest: latest, ask: ask)
            } else if let problem = turn.problem {
                Label(problem, systemImage: "exclamationmark.circle")
                    .font(.subheadline)
                    .foregroundStyle(.red)
            } else {
                LiveAnswer(steps: turn.steps, text: turn.streamed)
            }
        }
    }
}
