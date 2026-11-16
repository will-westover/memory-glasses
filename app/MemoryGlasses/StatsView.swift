import SwiftUI

struct StatsView: View {
    @State private var stats: Stats?
    @State private var problem: String?

    var body: some View {
        NavigationStack {
            Group {
                if let stats {
                    List {
                        Section("Stored") {
                            LabeledContent("Recordings", value: "\(stats.counts.sessions)")
                            LabeledContent("Moments", value: "\(stats.counts.frames)")
                            LabeledContent("Facts", value: "\(stats.counts.facts)")
                        }

                        Section("Spend") {
                            LabeledContent("Total", value: dollars(stats.totalUsd))
                                .fontWeight(.semibold)

                            ForEach(stats.costs) { cost in
                                LabeledContent("\(cost.step) (\(cost.calls) calls)", value: dollars(cost.usd))
                            }
                        }

                        if let eval = stats.eval {
                            EvalSection(eval: eval)
                        }
                    }
                    .refreshable { await load() }
                } else if let problem {
                    ServerProblem(message: problem, retry: load)
                } else {
                    ProgressView()
                }
            }
            .navigationTitle("Stats")
            .task { await load() }
        }
    }

    private func load() async {
        do {
            stats = try await API.current.get("/stats")
            problem = nil
        } catch {
            problem = error.localizedDescription
        }
    }
}

struct EvalSection: View {
    let eval: EvalRun

    var body: some View {
        Section {
            ForEach(eval.scores.keys.sorted(), id: \.self) { mode in
                if let scores = eval.scores[mode] {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Text(mode == "plain" ? "Plain search" : "Agent")
                                .font(.headline)

                            Spacer()

                            Text(scores.score.formatted(.percent.precision(.fractionLength(0))))
                                .font(.title3.weight(.semibold).monospacedDigit())
                        }

                        ProgressView(value: scores.score)

                        Text(details(scores))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 4)
                }
            }
        } header: {
            Text("Latest test")
        } footer: {
            Text("Answered by \(eval.model), graded by \(eval.judge). Run \(eval.run).")
        }
    }

    private func details(_ scores: Scores) -> String {
        let supported = scores.supported.formatted(.percent.precision(.fractionLength(0)))

        return "\(scores.questions) questions · \(supported) backed by sources · \(dollars(scores.usd)) each"
    }
}
