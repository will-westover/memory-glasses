import SwiftUI

struct Exchange: Identifiable {
    let id = UUID()
    let question: String
    var answer: Answer?
    var problem: String?
}

struct AskView: View {
    @State private var exchanges: [Exchange] = []
    @State private var draft = ""
    @State private var asking = false

    var body: some View {
        NavigationStack {
            ScrollViewReader { scroll in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 20) {
                        if exchanges.isEmpty {
                            ContentUnavailableView(
                                "Ask about anything you saw or heard",
                                systemImage: "bubble.left.and.text.bubble.right",
                                description: Text("What a sign said, what someone told you, what you were doing.")
                            )
                            .padding(.top, 80)
                        }

                        ForEach(exchanges) { ExchangeView(exchange: $0).id($0.id) }
                    }
                    .padding()
                }
                .onChange(of: exchanges.count) {
                    withAnimation { scroll.scrollTo(exchanges.last?.id, anchor: .bottom) }
                }
            }
            .navigationTitle("Ask")
            .safeAreaInset(edge: .bottom) { questionBar }
        }
    }

    private var questionBar: some View {
        HStack(spacing: 8) {
            TextField("Ask a question", text: $draft, axis: .vertical)
                .textFieldStyle(.roundedBorder)
                .lineLimit(1...4)
                .onSubmit(ask)

            Button(action: ask) {
                Image(systemName: "arrow.up.circle.fill").font(.title)
            }
            .disabled(asking || draft.trimmingCharacters(in: .whitespaces).isEmpty)
            .accessibilityLabel("Ask")
        }
        .padding()
        .background(.bar)
    }

    private func ask() {
        let question = draft.trimmingCharacters(in: .whitespacesAndNewlines)

        guard !question.isEmpty, !asking else { return }

        draft = ""
        asking = true
        exchanges.append(Exchange(question: question))
        let index = exchanges.count - 1

        Task {
            do {
                exchanges[index].answer = try await API.current.send(
                    "/ask", method: "POST", body: Question(question: question, plain: false)
                )
            } catch {
                exchanges[index].problem = error.localizedDescription
            }

            asking = false
        }
    }
}

struct ExchangeView: View {
    let exchange: Exchange

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(exchange.question)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(Color.accentColor, in: RoundedRectangle(cornerRadius: 16))
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity, alignment: .trailing)

            if let answer = exchange.answer {
                AnswerView(answer: answer)
            } else if let problem = exchange.problem {
                Label(problem, systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.red)
            } else {
                ProgressView("Looking through your memories…")
            }
        }
    }
}

struct AnswerView: View {
    let answer: Answer

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !answer.found {
                Label("Not in your recordings", systemImage: "questionmark.circle")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.orange)
            }

            Text(answer.answer)

            ForEach(answer.sources) { SourceRow(fact: $0) }

            Text("\(String(format: "%.1f", answer.seconds)) s · \(dollars(answer.usd))")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}

struct SourceRow: View {
    let fact: Fact

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            if fact.image != nil {
                FrameImage(path: fact.image)
                    .frame(width: 72, height: 54)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
            }

            VStack(alignment: .leading, spacing: 2) {
                Text(fact.at.formatted(date: .abbreviated, time: .shortened))
                    .font(.caption)
                    .foregroundStyle(.secondary)

                Text(fact.text)
                    .font(.footnote)
                    .lineLimit(3)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 12))
    }
}
