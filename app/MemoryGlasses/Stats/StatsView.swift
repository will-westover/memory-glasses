import SwiftUI

struct StatsView: View {
    @State private var stats: Stats?
    @State private var problem: String?

    private let stepNames = [
        "caption": "Describing photos",
        "transcript": "Transcribing speech",
        "summary": "Describing scenes",
        "link": "Matching things",
        "activities": "Grouping activities",
        "day": "Summing up days",
        "overview": "Titling recordings",
        "rooms": "Naming rooms",
        "place": "Finding places",
        "when": "Working out the time",
        "embed": "Indexing",
        "embed-question": "Reading questions",
        "agent": "Answering",
        "rag": "Plain search",
        "judge": "Grading tests",
        "suggest": "Suggesting questions",
    ]

    var body: some View {
        NavigationStack {
            Group {
                if let stats {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 32) {
                            spend(stats)
                            stored(stats.counts)

                            if let eval = stats.eval {
                                EvalSection(eval: eval)
                            }

                            steps(stats.costs)
                        }
                        .padding(20)
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

    private func spend(_ stats: Stats) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(dollars(stats.totalUsd))
                .font(.system(size: 44, weight: .semibold, design: .rounded))
                .monospacedDigit()

            Text("spent on models so far")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
    }

    private func stored(_ counts: Counts) -> some View {
        HStack(spacing: 12) {
            CountTile(value: counts.moments, label: "Recordings")
            CountTile(value: counts.frames, label: "Photos")
            CountTile(value: counts.facts, label: "Facts")
        }
    }

    private func steps(_ costs: [StepCost]) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionTitle("By step")

            ForEach(costs) { cost in
                HStack {
                    Text(stepNames[cost.step] ?? cost.step.capitalized)

                    Text("\(cost.calls)")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.tertiary)

                    Spacer()

                    Text(dollars(cost.usd))
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                }
                .font(.subheadline)
            }
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

struct CountTile: View {
    let value: Int
    let label: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("\(value)")
                .font(.title2.weight(.semibold))
                .monospacedDigit()

            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }
}

struct EvalSection: View {
    let eval: EvalRun

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            SectionTitle("Accuracy on the test questions")

            ForEach(eval.scores.keys.sorted(), id: \.self) { mode in
                if let scores = eval.scores[mode] {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack(alignment: .firstTextBaseline) {
                            Text(mode == "plain" ? "Plain search" : "Agent")
                                .font(.subheadline.weight(.medium))

                            Spacer()

                            Text(scores.score.formatted(.percent.precision(.fractionLength(0))))
                                .font(.subheadline.weight(.semibold))
                                .monospacedDigit()
                        }

                        Capsule()
                            .fill(Color(.tertiarySystemFill))
                            .frame(height: 6)
                            .overlay(alignment: .leading) {
                                GeometryReader { space in
                                    Capsule()
                                        .fill(Color.primary)
                                        .frame(width: space.size.width * scores.score)
                                }
                            }

                        Text("\(scores.questions) questions · \(dollars(scores.usd)) per answer")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            Text("Answered by \(eval.model), graded by \(eval.judge).")
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
    }
}
