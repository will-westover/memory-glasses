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
                    List {
                        ForEach(days, id: \.self) { day in
                            Section {
                                ForEach(sessions.filter { Calendar.current.isDate($0.startedAt, inSameDayAs: day) }) { session in
                                    NavigationLink(value: SessionLink(id: session.id, startedAt: session.startedAt)) {
                                        SessionRow(session: session)
                                    }
                                }
                            } header: {
                                Text(day.formatted(.dateTime.weekday(.wide).month().day()))
                                    .font(.footnote.weight(.medium))
                                    .foregroundStyle(.secondary)
                                    .textCase(nil)
                            }
                        }
                    }
                    .listStyle(.plain)
                    .refreshable { await load() }
                }
            }
            .navigationTitle("Timeline")
            .navigationDestination(for: SessionLink.self) { SessionView(link: $0) }
            .navigationDestination(for: Fact.self) { MomentView(fact: $0) }
            .task { await load() }
        }
    }

    private var days: [Date] {
        let starts = sessions.map { Calendar.current.startOfDay(for: $0.startedAt) }

        return Array(Set(starts)).sorted(by: >)
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
        HStack(spacing: 14) {
            FrameImage(path: session.cover)
                .frame(width: 56, height: 56)
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))

            VStack(alignment: .leading, spacing: 3) {
                Text(session.startedAt.formatted(date: .omitted, time: .shortened))
                    .font(.body.weight(.medium))

                Text(details)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
    }

    private var details: String {
        let parts = [session.place, clock(session.durationSeconds), "\(session.frames) moments"]

        return parts.compactMap { $0 }.joined(separator: " · ")
    }
}

struct SessionView: View {
    let link: SessionLink

    @State private var detail: SessionDetail?
    @State private var problem: String?
    @State private var showTranscript = false
    @State private var highlighted: Int?

    var body: some View {
        Group {
            if let detail {
                ScrollViewReader { scroll in
                    ScrollView {
                        content(detail)
                            .padding(20)
                    }
                    .task { await focus(detail, scroll) }
                }
            } else if let problem {
                ServerProblem(message: problem, retry: load)
            } else {
                ProgressView()
            }
        }
        .navigationTitle(link.startedAt.formatted(.dateTime.weekday(.abbreviated).hour().minute()))
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
    }

    private func content(_ detail: SessionDetail) -> some View {
        VStack(alignment: .leading, spacing: 28) {
            if !detail.summaries.isEmpty {
                VStack(alignment: .leading, spacing: 10) {
                    SectionTitle("Summary")

                    ForEach(detail.summaries) { Text($0.text).lineSpacing(3) }
                }
            }

            if !detail.transcript.isEmpty {
                transcript(detail.transcript)
            }

            VStack(alignment: .leading, spacing: 24) {
                SectionTitle("\(detail.frames.count) moments")

                ForEach(detail.frames) { frame in
                    NavigationLink(value: moment(frame)) {
                        MomentRow(frame: frame, highlighted: highlighted == frame.id)
                    }
                    .buttonStyle(.plain)
                    .id(frame.id)
                }
            }
        }
    }

    private func moment(_ frame: Frame) -> Fact {
        Fact(id: -frame.id, kind: "moment", at: frame.at, sessionId: link.id, offsetSeconds: frame.offsetSeconds,
             text: frame.summary, image: frame.image)
    }

    private func transcript(_ lines: [SpokenLine]) -> some View {
        DisclosureGroup(isExpanded: $showTranscript) {
            VStack(alignment: .leading, spacing: 10) {
                ForEach(lines, id: \.self) { line in
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Text(clock(line.start))
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.tertiary)

                        Text(line.text)
                            .font(.subheadline)
                    }
                }
            }
            .padding(.top, 10)
        } label: {
            Label("Transcript", systemImage: "waveform")
                .font(.subheadline.weight(.medium))
        }
    }

    private func focus(_ detail: SessionDetail, _ scroll: ScrollViewProxy) async {
        guard let offset = link.focus,
              let nearest = detail.frames.min(by: { abs($0.offsetSeconds - offset) < abs($1.offsetSeconds - offset) })
        else { return }

        try? await Task.sleep(for: .milliseconds(300))
        withAnimation(.snappy) { scroll.scrollTo(nearest.id, anchor: .center) }
        highlighted = nearest.id

        try? await Task.sleep(for: .seconds(2.5))
        withAnimation(.easeOut(duration: 0.6)) { highlighted = nil }
    }

    private func load() async {
        do {
            detail = try await API.current.get("/sessions/\(link.id)")
            problem = nil
        } catch {
            problem = error.localizedDescription
        }
    }
}

struct MomentRow: View {
    let frame: Frame
    let highlighted: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            FrameImage(path: frame.image)
                .aspectRatio(4 / 3, contentMode: .fit)
                .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                .overlay {
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .stroke(Color.accentColor, lineWidth: highlighted ? 4 : 0)
                }

            Text(clock(frame.offsetSeconds))
                .font(.caption.monospacedDigit())
                .foregroundStyle(.tertiary)

            Text(frame.summary)
                .font(.subheadline)
                .lineSpacing(2)
                .multilineTextAlignment(.leading)
        }
        .contentShape(Rectangle())
    }
}
