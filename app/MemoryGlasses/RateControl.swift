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

    @State private var rate = 30.0
    @State private var estimate: ReprocessEstimate?
    @State private var checking = false
    @State private var working: String?
    @State private var problem: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionTitle("Frames per minute")

            HStack {
                Text("\(Int(rate)) a minute")
                    .font(.subheadline.weight(.medium))

                if Int(rate) == Int(current) {
                    Text("current")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Slider(value: $rate, in: 1...60, step: 1) { editing in
                if !editing { Task { await check() } }
            }
            .disabled(working != nil)

            if let working {
                Label(working, systemImage: "hourglass")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else if checking {
                ProgressView()
            } else if let estimate, Int(rate) != Int(current) {
                Text(summary(estimate))
                    .font(.footnote)
                    .foregroundStyle(.secondary)

                Button("Reprocess at \(Int(rate)) a minute") { Task { await reprocess() } }
                    .buttonStyle(.borderedProminent)
                    .buttonBorderShape(.capsule)
            }

            if let problem {
                Text(problem)
                    .font(.footnote)
                    .foregroundStyle(.red)
            }
        }
        .onAppear { rate = current }
    }

    private func summary(_ estimate: ReprocessEstimate) -> String {
        if estimate.newFrames == 0 {
            return "\(estimate.frames) frames, all already described. Free."
        }

        return "\(estimate.frames) frames: \(estimate.newFrames) new, \(estimate.keptCaptions) kept. About \(dollars(estimate.usd))."
    }

    private func check() async {
        guard Int(rate) != Int(current) else {
            estimate = nil
            return
        }

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
                "/moments/\(momentId)/reprocess", method: "POST", body: GlassesSettings(framesPerMinute: rate)
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
                problem = upload.error ?? "Reprocessing failed."
                return
            default:
                working = (upload.step ?? "Waiting") + "…"
            }
        }
    }
}
