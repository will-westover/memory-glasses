import SwiftUI

struct ReprocessEstimate: Decodable {
    let frames: Int
    let newFrames: Int
    let keptCaptions: Int
    let usd: Double
}

struct UploadID: Decodable {
    let id: UUID
}

struct AddFramesControl: View {
    let momentId: String
    let currentRate: Double
    let onReprocessed: () async -> Void

    @State private var rate = 60.0
    @State private var estimate: ReprocessEstimate?
    @State private var loadingEstimate = false
    @State private var progressMessage: String?
    @State private var problem: String?

    private var canAdd: Bool { currentRate < 60 }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionTitle("Frames per minute")

            Text("\(Int(currentRate)) a minute")
                .font(.subheadline.weight(.medium))

            if !canAdd {
                Text("This moment already has the most frames.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else if let progressMessage {
                Label(progressMessage, systemImage: "hourglass")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else {
                Text("Add more frames to look closer. Frames already described are kept, so a moment can only go up.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)

                Slider(value: $rate, in: (currentRate + 1)...60, step: 1) { editing in
                    if !editing { Task { await loadEstimate() } }
                }

                if loadingEstimate {
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
        .task { if canAdd { rate = min(60, currentRate * 2); await loadEstimate() } }
    }

    private func summary(_ estimate: ReprocessEstimate) -> String {
        "\(estimate.newFrames) new frames, about \(dollars(estimate.usd)). \(estimate.keptCaptions) kept."
    }

    private func loadEstimate() async {
        loadingEstimate = true
        problem = nil

        do {
            estimate = try await API.current.get("/moments/\(momentId)/reprocess-estimate?frames_per_minute=\(Int(rate))")
        } catch {
            problem = error.localizedDescription
        }

        loadingEstimate = false
    }

    private func reprocess() async {
        do {
            let started: UploadID = try await API.current.send(
                "/moments/\(momentId)/reprocess", method: "POST", body: FrameRate(framesPerMinute: rate)
            )
            progressMessage = "Starting…"
            await watchReprocess(started.id)
        } catch {
            problem = error.localizedDescription
        }
    }

    private func watchReprocess(_ id: UUID) async {
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(3))

            guard let uploads: [UploadStatus] = try? await API.current.get("/uploads"),
                  let upload = uploads.first(where: { $0.id == id }) else { continue }

            switch upload.status {
            case "ready":
                progressMessage = nil
                estimate = nil
                await onReprocessed()
                return
            case "failed":
                progressMessage = nil
                problem = upload.error ?? "Adding frames failed."
                return
            default:
                progressMessage = (upload.step ?? "Waiting") + "…"
            }
        }
    }
}
