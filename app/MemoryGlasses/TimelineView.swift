import SwiftUI

struct TimelineView: View {
    @State private var sessions: [SessionSummary] = []
    @State private var problem: String?
    @State private var loaded = false

    var body: some View {
        NavigationStack {
            Group {
                if let problem {
                    ServerProblem(message: problem, retry: load)
                } else if loaded && sessions.isEmpty {
                    ContentUnavailableView("Nothing recorded yet", systemImage: "clock")
                } else {
                    List(sessions) { session in
                        NavigationLink(value: session) { SessionRow(session: session) }
                    }
                    .refreshable { await load() }
                }
            }
            .navigationTitle("Timeline")
            .navigationDestination(for: SessionSummary.self) { SessionView(session: $0) }
            .task { await load() }
        }
    }

    private func load() async {
        do {
            sessions = try await API.current.get("/sessions")
            problem = nil
        } catch {
            problem = error.localizedDescription
        }

        loaded = true
    }
}

struct SessionRow: View {
    let session: SessionSummary

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(session.startedAt.formatted(date: .abbreviated, time: .shortened))
                .font(.headline)

            Text(details)
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 2)
    }

    private var details: String {
        let parts = [session.place, clock(session.durationSeconds), "\(session.frames) moments"]

        return parts.compactMap { $0 }.joined(separator: " · ")
    }
}

struct SessionView: View {
    let session: SessionSummary

    @State private var detail: SessionDetail?
    @State private var problem: String?

    var body: some View {
        Group {
            if let detail {
                List {
                    if !detail.summaries.isEmpty {
                        Section("What happened") {
                            ForEach(detail.summaries) { Text($0.text) }
                        }
                    }

                    Section("Moments") {
                        ForEach(detail.frames) { MomentRow(frame: $0) }
                    }

                    if !detail.transcript.isEmpty {
                        Section("What was said") {
                            ForEach(detail.transcript, id: \.self) { SpokenRow(line: $0) }
                        }
                    }
                }
            } else if let problem {
                ServerProblem(message: problem, retry: load)
            } else {
                ProgressView()
            }
        }
        .navigationTitle(session.startedAt.formatted(date: .abbreviated, time: .shortened))
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
    }

    private func load() async {
        do {
            detail = try await API.current.get("/sessions/\(session.id)")
            problem = nil
        } catch {
            problem = error.localizedDescription
        }
    }
}

struct MomentRow: View {
    let frame: Frame

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            FrameImage(path: frame.image)
                .aspectRatio(16 / 9, contentMode: .fit)
                .clipShape(RoundedRectangle(cornerRadius: 10))

            Text(clock(frame.offsetSeconds))
                .font(.caption)
                .foregroundStyle(.secondary)

            Text(frame.summary)

            if !frame.objects.isEmpty || !frame.texts.isEmpty {
                DisclosureGroup("\(frame.objects.count) things seen") {
                    ForEach(frame.texts, id: \.self) { item in
                        Text("“\(item.text)” on \(item.surface)")
                    }

                    ForEach(frame.objects, id: \.self) { item in
                        Text("\(item.name): \(item.location)")
                    }
                }
                .font(.footnote)
                .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
    }
}

struct SpokenRow: View {
    let line: SpokenLine

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text("\(clock(line.start)) \(line.speaker ?? "?")")
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)

            Text(line.text)
        }
    }
}
