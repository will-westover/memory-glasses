import SwiftUI

struct ChatView: View {
    let id: UUID
    let newChat: () -> Void

    @Environment(ConversationStore.self) private var store

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

struct Composer: View {
    @Binding var text: String
    let busy: Bool
    var focused: FocusState<Bool>.Binding
    let send: () -> Void

    @State private var dictation = Dictation()

    private var ready: Bool { !busy && !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    var body: some View {
        HStack(alignment: .bottom, spacing: 4) {
            TextField(dictation.listening ? "Listening…" : "Ask anything", text: $text, axis: .vertical)
                .lineLimit(1...5)
                .focused(focused)
                .onSubmit { if ready { send() } }
                .padding(.leading, 18)
                .padding(.vertical, 13)

            if text.isEmpty || dictation.listening {
                microphone
            }

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
        .alert("Can't listen", isPresented: Binding(get: { dictation.problem != nil }, set: { if !$0 { dictation.problem = nil } })) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(dictation.problem ?? "")
        }
    }

    private var microphone: some View {
        Image(systemName: dictation.listening ? "waveform" : "mic")
            .font(.system(size: 17, weight: .medium))
            .symbolEffect(.variableColor.iterative, isActive: dictation.listening)
            .foregroundStyle(dictation.listening ? Color.red : Color.secondary)
            .frame(width: 32, height: 32)
            .padding(.vertical, 7)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { _ in
                        if !dictation.listening && !busy {
                            Task { await dictation.start { text = $0 } }
                        }
                    }
                    .onEnded { _ in
                        dictation.stop()

                        if ready { send() }
                    }
            )
            .accessibilityLabel("Hold to talk")
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
                AnswerView(answer: answer, latest: latest, ask: ask)
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

struct LiveAnswer: View {
    let steps: [LiveStep]
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if steps.isEmpty && text.isEmpty {
                Thinking()
            }

            ForEach(steps) { step in
                StepLine(step: step, current: step.id == steps.last?.id && text.isEmpty)
            }

            if !text.isEmpty {
                CitedText(text: text, sources: [])
            }
        }
    }
}

struct StepLine: View {
    let step: LiveStep
    let current: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Image(systemName: symbol)
                    .symbolEffect(.pulse, isActive: current)
                    .frame(width: 18)

                Text(step.text)
                    .lineLimit(2)
            }
            .font(.subheadline)
            .foregroundStyle(.secondary)

            if !step.images.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(step.images, id: \.self) { image in
                            FrameImage(path: image)
                                .frame(width: 44, height: 44)
                                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                        }
                    }
                    .padding(.leading, 26)
                }
                .scrollClipDisabled()
                .transition(.opacity)
            }
        }
    }

    private var symbol: String {
        switch step.kind {
        case "search": "magnifyingglass"
        case "timeline": "clock"
        case "nearby": "scope"
        case "answer": "text.bubble"
        default: "sparkle"
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
    var latest = false
    var ask: (String) -> Void = { _ in }

    @Environment(\.openSource) private var openSource

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if !answer.found {
                Label("Not in your recordings", systemImage: "circle.dashed")
                    .font(.footnote.weight(.medium))
                    .foregroundStyle(.secondary)
            }

            if let highlighted = answer.highlighted {
                HighlightCard(fact: highlighted, reason: answer.highlightReason)
            }

            CitedText(text: answer.answer, sources: answer.sources, open: openSource)

            LayoutCard(layout: answer.layout ?? "text", sources: answer.sources)

            if !others.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 10) {
                        ForEach(others) { SourceChip(fact: $0, number: number(of: $0)) }
                    }
                }
                .scrollClipDisabled()
            }

            if !answer.found, let steps = answer.nextSteps, !steps.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(steps, id: \.self) { step in
                        Button { ask(step.question) } label: {
                            Label(step.label, systemImage: "arrow.turn.down.right")
                                .font(.subheadline.weight(.medium))
                                .padding(.horizontal, 14)
                                .padding(.vertical, 9)
                                .background(Color(.secondarySystemBackground), in: Capsule())
                        }
                        .buttonStyle(.plain)
                    }
                }
            }

            if latest, let followUps = answer.followUps, !followUps.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(followUps, id: \.self) { question in
                            Button { ask(question) } label: {
                                Text(question)
                                    .font(.subheadline)
                                    .padding(.horizontal, 14)
                                    .padding(.vertical, 9)
                                    .overlay(Capsule().stroke(Color(.separator)))
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
                .scrollClipDisabled()
            }

            Text("\(answer.sources.count) sources · \(String(format: "%.1f", answer.seconds)) s · \(dollars(answer.usd))")
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
    }

    private var others: [Fact] {
        answer.sources.enumerated().filter { $0.offset != answer.highlight }.map(\.element)
    }

    private func number(of fact: Fact) -> Int? {
        answer.sources.firstIndex(of: fact).map { $0 + 1 }
    }
}

struct SourceChip: View {
    let fact: Fact
    var number: Int? = nil

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

                    HStack(spacing: 6) {
                        if let number {
                            Text("\(number)")
                                .font(.caption2.weight(.bold).monospacedDigit())
                        }

                        Text(fact.at.formatted(.dateTime.weekday(.abbreviated).hour().minute()))
                            .font(.caption2.weight(.semibold))
                    }
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
