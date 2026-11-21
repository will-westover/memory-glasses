import SwiftUI

struct ReprocessEstimate: Decodable {
    let frames: Int
    let newFrames: Int
    let keptCaptions: Int
    let usd: Double
}

struct UploadStarted: Decodable {
    let id: UUID
}

struct RateControl: View {
    let momentId: String
    let current: Double
    let finished: () async -> Void

    @State private var rate = 60.0
    @State private var estimate: ReprocessEstimate?
    @State private var checking = false
    @State private var working: String?
    @State private var problem: String?

    private var canAdd: Bool { current < 60 }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionTitle("Frames per minute")

            Text("\(Int(current)) a minute")
                .font(.subheadline.weight(.medium))

            if !canAdd {
                Text("This moment already has the most frames.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else if let working {
                Label(working, systemImage: "hourglass")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else {
                Text("Add more frames to look closer. Frames already described are kept, so a moment can only go up.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)

                Slider(value: $rate, in: (current + 1)...60, step: 1) { editing in
                    if !editing { Task { await check() } }
                }

                if checking {
                    ProgressView()
                } else if let estimate {
                    Text(summary(estimate))
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Button("Add frames: \(Int(rate)) a minute") { Task { await reprocess() } }
                    .buttonStyle(FilledButton())
            }

            if let problem {
                Text(problem)
                    .font(.footnote)
                    .foregroundStyle(.red)
            }
        }
        .task { if canAdd { rate = min(60, current * 2); await check() } }
    }

    private func summary(_ estimate: ReprocessEstimate) -> String {
        "\(estimate.newFrames) new frames, about \(dollars(estimate.usd)). \(estimate.keptCaptions) kept."
    }

    private func check() async {
        checking = true
        problem = nil

        do {
            estimate = try await API.current.get("/moments/\(momentId)/reprocess-estimate?frames_per_minute=\(Int(rate))")
        } catch {
            problem = error.localizedDescription
        }

        checking = false
    }

    private func reprocess() async {
        do {
            let started: UploadStarted = try await API.current.send(
                "/moments/\(momentId)/reprocess", method: "POST", body: FrameRate(framesPerMinute: rate)
            )
            working = "Starting…"
            await follow(started.id)
        } catch {
            problem = error.localizedDescription
        }
    }

    private func follow(_ id: UUID) async {
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(3))

            guard let uploads: [UploadStatus] = try? await API.current.get("/uploads"),
                  let upload = uploads.first(where: { $0.id == id }) else { continue }

            switch upload.status {
            case "ready":
                working = nil
                estimate = nil
                await finished()
                return
            case "failed":
                working = nil
                problem = upload.error ?? "Adding frames failed."
                return
            default:
                working = (upload.step ?? "Waiting") + "…"
            }
        }
    }
}
