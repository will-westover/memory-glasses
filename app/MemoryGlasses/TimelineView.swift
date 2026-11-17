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
                                    NavigationLink(value: session) { SessionRow(session: session) }
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
            .navigationDestination(for: SessionSummary.self) { SessionView(session: $0) }
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
    let session: SessionSummary

    @State private var detail: SessionDetail?
    @State private var problem: String?
    @State private var showTranscript = false

    var body: some View {
        Group {
            if let detail {
                ScrollView {
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

                            ForEach(detail.frames) { MomentView(frame: $0) }
                        }
                    }
                    .padding(20)
                }
            } else if let problem {
                ServerProblem(message: problem, retry: load)
            } else {
                ProgressView()
            }
        }
        .navigationTitle(session.startedAt.formatted(.dateTime.weekday(.abbreviated).hour().minute()))
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
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

    private func load() async {
        do {
            detail = try await API.current.get("/sessions/\(session.id)")
            problem = nil
        } catch {
            problem = error.localizedDescription
        }
    }
}

struct MomentView: View {
    let frame: Frame

    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            FrameImage(path: frame.image)
                .aspectRatio(4 / 3, contentMode: .fit)
                .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))

            HStack(alignment: .firstTextBaseline) {
                Text(clock(frame.offsetSeconds))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.tertiary)

                Spacer()

                if !frame.objects.isEmpty || !frame.texts.isEmpty {
                    Button(expanded ? "Less" : "\(frame.objects.count + frame.texts.count) details") {
                        withAnimation(.snappy) { expanded.toggle() }
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
            }

            Text(frame.summary)
                .font(.subheadline)
                .lineSpacing(2)

            if expanded {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(frame.texts, id: \.self) { Text("“\($0.text)” on \($0.surface)") }
                    ForEach(frame.objects, id: \.self) { Text("\($0.name): \($0.location)") }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
        }
    }
}
