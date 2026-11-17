import SwiftUI

struct ChatView: View {
    let id: UUID
    let newChat: () -> Void

    @Environment(ConversationStore.self) private var store

    @State private var draft = ""
    @FocusState private var composing: Bool

    private let suggestions = [
        "What did I do today?",
        "Summarize my last recording",
        "What was said in my last conversation?",
        "What text did I see?",
    ]

    var body: some View {
        let conversation = store.conversation(id)
        let turns = conversation?.turns ?? []
        let busy = turns.last.map { $0.answer == nil && $0.problem == nil } ?? false

        ScrollView {
            LazyVStack(alignment: .leading, spacing: 32) {
                if turns.isEmpty {
                    welcome
                }

                ForEach(turns) { TurnView(turn: $0) }
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
        .navigationTitle(turns.isEmpty ? "New chat" : conversation?.title ?? "")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button(action: newChat) { Image(systemName: "square.and.pencil") }
                    .disabled(turns.isEmpty)
                    .accessibilityLabel("New chat")
            }
        }
        .onAppear { composing = turns.isEmpty }
    }

    private var welcome: some View {
        VStack(alignment: .leading, spacing: 20) {
            Image(systemName: "eyeglasses")
                .font(.system(size: 30, weight: .light))
                .foregroundStyle(.secondary)

            Text("What would you like to remember?")
                .font(.title2.weight(.semibold))

            VStack(alignment: .leading, spacing: 10) {
                ForEach(suggestions, id: \.self) { suggestion in
                    Button { send(suggestion) } label: {
                        Text(suggestion)
                            .font(.subheadline)
                            .padding(.horizontal, 14)
                            .padding(.vertical, 9)
                            .overlay(Capsule().stroke(Color(.separator)))
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .padding(.top, 80)
    }

    private func send(_ text: String) {
        let question = text.trimmingCharacters(in: .whitespacesAndNewlines)

        guard !question.isEmpty else { return }

        draft = ""
        Task { await store.ask(question, in: id) }
    }
}

struct Composer: View {
    @Binding var text: String
    let busy: Bool
    var focused: FocusState<Bool>.Binding
    let send: () -> Void

    private var ready: Bool { !busy && !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    var body: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField("Ask anything", text: $text, axis: .vertical)
                .lineLimit(1...5)
                .focused(focused)
                .onSubmit { if ready { send() } }
                .padding(.leading, 18)
                .padding(.vertical, 13)

            Button(action: send) {
                Image(systemName: "arrow.up")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(Color(.systemBackground))
                    .frame(width: 32, height: 32)
                    .background(ready ? Color.primary : Color(.systemGray4), in: Circle())
            }
            .disabled(!ready)
            .padding(7)
            .accessibilityLabel("Send")
        }
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 24, style: .continuous))
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(.bar)
    }
}

struct TurnView: View {
    let turn: Turn

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(turn.question)
                .padding(.horizontal, 16)
                .padding(.vertical, 11)
                .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 20, style: .continuous))
                .padding(.leading, 56)
                .frame(maxWidth: .infinity, alignment: .trailing)

            if let answer = turn.answer {
                AnswerView(answer: answer)
            } else if let problem = turn.problem {
                Label(problem, systemImage: "exclamationmark.circle")
                    .font(.subheadline)
                    .foregroundStyle(.red)
            } else {
                Thinking()
            }
        }
    }
}

struct Thinking: View {
    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "sparkle")
                .symbolEffect(.pulse)

            Text("Searching your memories")
        }
        .font(.subheadline)
        .foregroundStyle(.secondary)
    }
}

struct AnswerView: View {
    let answer: Answer

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if !answer.found {
                Label("Not in your recordings", systemImage: "circle.dashed")
                    .font(.footnote.weight(.medium))
                    .foregroundStyle(.secondary)
            }

            Text(answer.answer)
                .lineSpacing(3)
                .textSelection(.enabled)

            if !answer.sources.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 10) {
                        ForEach(answer.sources) { SourceChip(fact: $0) }
                    }
                }
                .scrollClipDisabled()
            }

            Text("\(answer.sources.count) sources · \(String(format: "%.1f", answer.seconds)) s · \(dollars(answer.usd))")
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
    }
}

struct SourceChip: View {
    let fact: Fact

    var body: some View {
        NavigationLink(value: fact) {
            VStack(alignment: .leading, spacing: 8) {
                ZStack(alignment: .bottomLeading) {
                    if fact.image != nil {
                        FrameImage(path: fact.image)
                    } else {
                        Rectangle()
                            .fill(Color(.tertiarySystemFill))
                            .overlay {
                                Image(systemName: fact.isSpeech ? "waveform" : "text.alignleft")
                                    .font(.title2)
                                    .foregroundStyle(.secondary)
                            }
                    }

                    Text(fact.at.formatted(.dateTime.weekday(.abbreviated).hour().minute()))
                        .font(.caption2.weight(.semibold))
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(.ultraThinMaterial, in: Capsule())
                        .padding(8)
                }
                .frame(width: 200, height: 120)
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))

                Text(fact.text)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                    .frame(width: 200, alignment: .leading)
            }
        }
        .buttonStyle(.plain)
    }
}
