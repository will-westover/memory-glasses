import SwiftUI

struct TimelineView: View {
    @State private var moments: [MomentSummary] = []
    @State private var problem: String?
    @State private var loaded = false

    var body: some View {
        NavigationStack {
            Group {
                if let problem {
                    ServerProblem(message: problem, retry: load)
                } else if loaded && moments.isEmpty {
                    ContentUnavailableView("Nothing recorded yet", systemImage: "clock")
                } else {
                    List {
                        ForEach(days, id: \.self) { day in
                            Section {
                                ForEach(moments.filter { Calendar.current.isDate($0.startedAt, inSameDayAs: day) }) { moment in
                                    NavigationLink(value: MomentLink(id: moment.id, startedAt: moment.startedAt)) {
                                        MomentRow(moment: moment)
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
            .navigationDestination(for: MomentLink.self) { MomentView(link: $0) }
            .navigationDestination(for: Fact.self) { FrameView(fact: $0) }
            .task { await load() }
        }
    }

    private var days: [Date] {
        let starts = moments.map { Calendar.current.startOfDay(for: $0.startedAt) }

        return Array(Set(starts)).sorted(by: >)
    }

    private func load() async {
        do {
            moments = try await API.current.get("/moments")
            problem = nil
        } catch {
            problem = error.localizedDescription
        }

        loaded = true
    }
}

struct MomentRow: View {
    let moment: MomentSummary

    var body: some View {
        HStack(spacing: 14) {
            FrameImage(path: moment.cover)
                .frame(width: 56, height: 56)
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))

            VStack(alignment: .leading, spacing: 3) {
                Text(moment.startedAt.formatted(date: .omitted, time: .shortened))
                    .font(.body.weight(.medium))

                Text(details)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
    }

    private var details: String {
        let parts = [moment.place, clock(moment.durationSeconds), "\(moment.frames) frames"]

        return parts.compactMap { $0 }.joined(separator: " · ")
    }
}

struct MomentView: View {
    let link: MomentLink

    @State private var detail: MomentDetail?
    @State private var problem: String?
    @State private var showTranscript = false
    @State private var index = 0

    var body: some View {
        Group {
            if let detail {
                ScrollView {
                    content(detail)
                        .padding(.vertical, 12)
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

    private func content(_ detail: MomentDetail) -> some View {
        VStack(alignment: .leading, spacing: 28) {
            if !detail.frames.isEmpty {
                let frame = detail.frames[min(index, detail.frames.count - 1)]

                VStack(alignment: .leading, spacing: 14) {
                    PhotoPager(frames: detail.frames, index: $index)

                    VStack(alignment: .leading, spacing: 12) {
                        Text("\(clock(frame.offsetSeconds)) in")
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.tertiary)

                        PeopleChips(people: frame.people)

                        Text(frame.summary)
                            .font(.subheadline)
                            .lineSpacing(2)

                        NavigationLink(value: frameFact(frame)) {
                            Label("Listen and see details", systemImage: "waveform")
                                .font(.subheadline.weight(.medium))
                        }
                    }
                    .padding(.horizontal, 20)
                }
            }

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
            }
            .padding(.horizontal, 20)
        }
    }

    private func frameFact(_ frame: Frame) -> Fact {
        Fact(id: -frame.id, kind: "frame", at: frame.at, momentId: link.id, offsetSeconds: frame.offsetSeconds,
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

    private func load() async {
        do {
            let loaded: MomentDetail = try await API.current.get("/moments/\(link.id)")

            if let focus = link.focus {
                index = loaded.frames.indices.min {
                    abs(loaded.frames[$0].offsetSeconds - focus) < abs(loaded.frames[$1].offsetSeconds - focus)
                } ?? 0
            }

            detail = loaded
            problem = nil
        } catch {
            problem = error.localizedDescription
        }
    }
}
