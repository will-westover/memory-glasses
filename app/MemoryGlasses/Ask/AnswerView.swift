import SwiftUI

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
                            FrameImage(path: image, small: true)
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
        case "when": "calendar"
        case "entity": "cube"
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
    var question = ""
    var latest = false
    var ask: (String) -> Void = { _ in }

    @Environment(Router.self) private var router

    private var openSource: (Fact) -> Void {
        { [router, answer, question] fact in router.open(fact, answer: answer, question: question) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if !answer.found {
                Label("Not in your recordings", systemImage: "circle.dashed")
                    .font(.footnote.weight(.medium))
                    .foregroundStyle(.secondary)
            }

            if let featured = answer.featuredSource {
                FeaturedSourceCard(fact: featured, number: number(of: featured))
            }

            CitedText(text: answer.answer, sources: answer.sources, open: openSource)

            LayoutCard(layout: answer.layout ?? "text", sources: answer.sources)

            if !remainingSources.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(remainingSources) { SourceChip(fact: $0, number: number(of: $0)) }
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
        }
        .environment(\.openSource, openSource)
    }

    private var remainingSources: [Fact] {
        answer.sources.filter { $0 != answer.featuredSource }.sorted { $0.at < $1.at }
    }

    private func number(of fact: Fact) -> Int? {
        answer.sources.firstIndex(of: fact).map { $0 + 1 }
    }
}

struct SourceChip: View {
    let fact: Fact
    var number: Int? = nil

    @Environment(\.openSource) private var openSource

    var body: some View {
        Button { openSource(fact) } label: {
            ZStack(alignment: .bottomLeading) {
                if fact.image != nil {
                    FrameImage(path: fact.image, small: true)
                } else {
                    Rectangle()
                        .fill(Color(.secondarySystemBackground))
                        .overlay(alignment: .topLeading) {
                            Text(fact.isSpeech ? "“\(fact.text)”" : fact.text)
                                .font(.caption)
                                .lineLimit(4)
                                .multilineTextAlignment(.leading)
                                .padding(10)
                        }
                }

                SourceStamp(fact: fact, number: number)
                    .padding(6)
            }
            .frame(width: 128, height: 96)
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(fact.text)
    }
}

struct SourceStamp: View {
    let fact: Fact
    var number: Int? = nil

    var body: some View {
        HStack(spacing: 5) {
            if let number {
                Text("\(number)")
                    .font(.caption2.weight(.bold).monospacedDigit())
            }

            Text(fact.at.formatted(.dateTime.weekday(.abbreviated).hour().minute()))
                .font(.caption2.weight(.semibold))
        }
        .padding(.horizontal, 7)
        .padding(.vertical, 3)
        .background(.ultraThinMaterial, in: Capsule())
    }
}
