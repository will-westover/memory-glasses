import SwiftUI

struct TimelineView: View {
    @Environment(Router.self) private var router

    @State private var moments: [MomentSummary] = []
    @State private var query = ""
    @State private var results: [Fact] = []
    @State private var searching = false
    @State private var uploads: [UploadStatus] = []
    @State private var problem: String?
    @State private var loaded = false
    @State private var recording = false

    var body: some View {
        @Bindable var router = router

        NavigationStack(path: $router.timelinePath) {
            Group {
                if let problem {
                    ServerProblem(message: problem, retry: load)
                } else if !query.isEmpty {
                    SearchResults(query: query, results: results, moments: moments, searching: searching)
                } else if loaded && moments.isEmpty {
                    ContentUnavailableView("Nothing recorded yet", systemImage: "clock")
                } else {
                    List {
                        if !active.isEmpty {
                            Section {
                                ForEach(active) { UploadRow(upload: $0) }
                            }
                        }

                        ForEach(days, id: \.self) { day in
                            Section {
                                ForEach(moments.filter { Calendar.current.isDate($0.startedAt, inSameDayAs: day) }) { moment in
                                    NavigationLink(value: MomentLink(id: moment.id, startedAt: moment.startedAt)) {
                                        VStack(alignment: .leading, spacing: 6) {
                                            MomentRow(moment: moment)

                                            let marks = router.numbers(in: moment.id)

                                            if !marks.isEmpty {
                                                MarkerStrip(duration: moment.durationSeconds,
                                                            marks: marks.map { ($0.number, $0.fact.offsetSeconds) })
                                                    .padding(.leading, 70)
                                            }
                                        }
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
            .safeAreaInset(edge: .top) {
                if router.trail != nil { TrailPill() }
            }
            .navigationTitle("Timeline")
            .searchable(text: $query, prompt: "Search your memories")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { recording = true } label: { Image(systemName: "record.circle") }
                        .accessibilityLabel("Record a moment")
                }
            }
            .fullScreenCover(isPresented: $recording, onDismiss: { Task { await load() } }) { RecordView() }
            .navigationDestination(for: MomentLink.self) { MomentView(link: $0) }
            .navigationDestination(for: Fact.self) { FrameView(fact: $0) }
            .task { await load() }
            .task(id: active.map(\.id)) { await watchUploads() }
            .task(id: query) { await search() }
        }
    }

    private func search() async {
        let text = query.trimmingCharacters(in: .whitespaces)

        guard !text.isEmpty else {
            results = []
            return
        }

        try? await Task.sleep(for: .milliseconds(350))

        guard !Task.isCancelled else { return }

        searching = true
        let encoded = text.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? text
        let found: SearchHits? = try? await API.current.get("/search?q=\(encoded)&limit=40")

        if !Task.isCancelled {
            results = found?.results ?? []
            searching = false
        }
    }

    private var active: [UploadStatus] {
        uploads.filter { $0.status != "ready" && $0.receivedAt > .now.addingTimeInterval(-24 * 3600) }
    }

    private func watchUploads() async {
        while !Task.isCancelled && active.contains(where: { $0.status == "processing" }) {
            try? await Task.sleep(for: .seconds(4))
            await load()
        }
    }

    private var days: [Date] {
        let starts = moments.map { Calendar.current.startOfDay(for: $0.startedAt) }

        return Array(Set(starts)).sorted(by: >)
    }

    private func load() async {
        do {
            moments = try await API.current.get("/moments")
            uploads = (try? await API.current.get("/uploads")) ?? []
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
                HStack(spacing: 6) {
                    Text(moment.startedAt.formatted(date: .omitted, time: .shortened))
                        .font(.body.weight(.medium))

                    Image(systemName: sourceIcon(moment.source).symbol)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .accessibilityLabel(sourceIcon(moment.source).label)
                }

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

    @Environment(Router.self) private var router

    @State private var detail: MomentDetail?
    @State private var problem: String?
    @State private var showTranscript = false
    @State private var index = 0
    @State private var playingFrom: Double?
    @State private var path: [[Double]] = []
    @State private var showingRoom = false

    var body: some View {
        Group {
            if let detail {
                ScrollViewReader { scroller in
                    ScrollView {
                        content(detail)
                            .padding(.vertical, 12)
                    }
                    .onChange(of: router.focusToken) { applyFocus(scroller) }
                    .onAppear { applyFocus(scroller) }
                }
            } else if let problem {
                ServerProblem(message: problem, retry: load)
            } else {
                ProgressView()
            }
        }
        .safeAreaInset(edge: .top) {
            if router.trail != nil { TrailPill() }
        }
        .navigationTitle(link.startedAt.formatted(.dateTime.weekday(.abbreviated).hour().minute()))
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
        .sheet(isPresented: Binding(get: { playingFrom != nil }, set: { if !$0 { playingFrom = nil } })) {
            MomentVideo(momentId: link.id, start: playingFrom ?? 0)
        }
        .fullScreenCover(isPresented: $showingRoom) {
            RoomView(momentId: link.id, frames: detail?.frames ?? [], path: path) { picked in index = picked }
        }
    }

    private func content(_ detail: MomentDetail) -> some View {
        VStack(alignment: .leading, spacing: 28) {
            if !detail.frames.isEmpty {
                let frame = detail.frames[min(index, detail.frames.count - 1)]

                VStack(alignment: .leading, spacing: 14) {
                    PhotoPager(frames: detail.frames, index: $index, markers: markers(detail), range: focusRange)

                    if let focused, focused.isSpeech {
                        QuoteCard(fact: focused)
                            .padding(.horizontal, 20)
                    }

                    VStack(alignment: .leading, spacing: 12) {
                        if detail.hasVideo {
                            Button { playingFrom = frame.offsetSeconds } label: {
                                Label("Play video from here", systemImage: "play.fill")
                                    .font(.subheadline.weight(.medium))
                            }
                        }

                        Text([clock(frame.offsetSeconds) + " in", frame.room].compactMap { $0 }.joined(separator: " · "))
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.tertiary)

                        PeopleChips(people: frame.people)

                        Text(Highlight.mark(frame.summary, marked(frame.offsetSeconds)))
                            .font(.subheadline)
                            .lineSpacing(2)

                        ForEach(frame.texts, id: \.self) { seen in
                            Text(Highlight.mark("Text on \(seen.surface): \(seen.text)", marked(frame.offsetSeconds)))
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }

                        NavigationLink(value: frameFact(frame)) {
                            Label("Listen and see details", systemImage: "waveform")
                                .font(.subheadline.weight(.medium))
                        }
                    }
                    .padding(.horizontal, 20)
                }
            }

            VStack(alignment: .leading, spacing: 28) {
                if path.count > 1 {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack {
                            SectionTitle(detail.place?.name.map { "Where you went in \($0)" } ?? "Where you went")
                            Spacer()

                            if detail.hasMesh {
                                Button { showingRoom = true } label: {
                                    Label("View in 3D", systemImage: "cube.transparent")
                                        .font(.subheadline.weight(.medium))
                                }
                            }
                        }

                        PathMap(points: path, frames: detail.frames, index: $index)
                    }
                }

                if !detail.summaries.isEmpty {
                    VStack(alignment: .leading, spacing: 10) {
                        SectionTitle("Summary")

                        ForEach(detail.summaries) { summary in
                            let current = focused?.id == summary.id

                            Text(Highlight.mark(summary.text, current ? router.focusWords : []))
                                .lineSpacing(3)
                                .padding(current ? 10 : 0)
                                .background(current ? Color.yellow.opacity(0.15) : .clear,
                                            in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                                .id("summary-\(summary.id)")
                        }
                    }
                }

                if !detail.transcript.isEmpty {
                    transcript(detail.transcript)
                }

                if detail.hasVideo {
                    RateControl(momentId: link.id, current: detail.framesPerMinute ?? 30) { await load() }
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
                    let spoken = said(line)

                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Text(clock(line.start))
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.tertiary)

                        Text(Highlight.mark(line.text, spoken ? router.focusWords : []))
                            .font(.subheadline)
                    }
                    .padding(spoken ? 8 : 0)
                    .background(spoken ? Color.yellow.opacity(0.15) : .clear,
                                in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                    .id("line-\(line.start)")
                }
            }
            .padding(.top, 10)
        } label: {
            Label("Transcript", systemImage: "waveform")
                .font(.subheadline.weight(.medium))
        }
    }

    private var focused: Fact? {
        guard let fact = router.focus, fact.momentId == link.id else { return nil }

        return fact
    }

    private var focusRange: ClosedRange<Double>? {
        guard let focused, focused.kind == "summary" || focused.isSpeech else { return nil }

        let end = focused.endSeconds ?? focused.offsetSeconds + (focused.kind == "summary" ? 60 : 8)

        return focused.offsetSeconds...max(end, focused.offsetSeconds)
    }

    private func marked(_ offset: Double) -> Set<String> {
        guard let focused else { return [] }

        if let focusRange, focusRange.contains(offset) { return router.focusWords }

        return abs(focused.offsetSeconds - offset) < 1 ? router.focusWords : []
    }

    private func said(_ line: SpokenLine) -> Bool {
        guard let focused, focused.isSpeech, let focusRange else { return false }

        return line.end >= focusRange.lowerBound && line.start <= focusRange.upperBound
    }

    private func markers(_ detail: MomentDetail) -> [Int: Int] {
        var result: [Int: Int] = [:]

        for (number, fact) in router.numbers(in: link.id) {
            if let position = nearest(fact.offsetSeconds, in: detail.frames), result[position] == nil {
                result[position] = number
            }
        }

        return result
    }

    private func nearest(_ offset: Double, in frames: [Frame]) -> Int? {
        frames.indices.min { abs(frames[$0].offsetSeconds - offset) < abs(frames[$1].offsetSeconds - offset) }
    }

    private func applyFocus(_ scroller: ScrollViewProxy) {
        guard let focused, let detail else { return }

        if let position = nearest(focused.offsetSeconds, in: detail.frames) {
            withAnimation(.snappy) { index = position }
        }

        if focused.isSpeech, let line = detail.transcript.first(where: said) {
            showTranscript = true
            scroll(scroller, to: "line-\(line.start)")
        } else if focused.kind == "summary" {
            scroll(scroller, to: "summary-\(focused.id)")
        }
    }

    private func scroll(_ scroller: ScrollViewProxy, to target: String) {
        Task {
            try? await Task.sleep(for: .milliseconds(350))
            withAnimation(.snappy) { scroller.scrollTo(target, anchor: .center) }
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
            path = ((try? await API.current.get("/moments/\(link.id)/path")) as WalkedPath?)?.points ?? []
            problem = nil
        } catch {
            problem = error.localizedDescription
        }
    }
}

struct UploadRow: View {
    let upload: UploadStatus

    var body: some View {
        HStack(spacing: 14) {
            ZStack {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(Color(.tertiarySystemFill))

                if upload.status == "failed" {
                    Image(systemName: "exclamationmark.triangle")
                        .foregroundStyle(.red)
                } else {
                    ProgressView()
                }
            }
            .frame(width: 56, height: 56)

            VStack(alignment: .leading, spacing: 3) {
                Text(upload.status == "failed" ? "Processing failed" : "New moment")
                    .font(.body.weight(.medium))

                Text(upload.status == "failed" ? (upload.error ?? "") : (upload.step ?? "Waiting") + "…")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
        }
        .padding(.vertical, 4)
    }
}

struct TrailPill: View {
    @Environment(Router.self) private var router

    var body: some View {
        if let trail = router.trail {
            HStack(spacing: 10) {
                Button {
                    trail.fromSearch ? router.clear() : router.backToChat()
                } label: {
                    Label(trail.fromSearch ? "Done" : "Back to chat",
                          systemImage: trail.fromSearch ? "xmark" : "chevron.left")
                        .font(.subheadline.weight(.semibold))
                }

                Spacer()

                if trail.sources.count > 1 {
                    Button { router.step(by: -1) } label: { Image(systemName: "chevron.left") }
                        .disabled(trail.index == 0)
                        .accessibilityLabel("Previous source")

                    Text("\(trail.fromSearch ? "Result" : "Source") \(trail.index + 1) of \(trail.sources.count)")
                        .font(.subheadline.monospacedDigit())

                    Button { router.step(by: 1) } label: { Image(systemName: "chevron.right") }
                        .disabled(trail.index == trail.sources.count - 1)
                        .accessibilityLabel("Next source")
                }
            }
            .buttonStyle(.plain)
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(.regularMaterial, in: Capsule())
            .padding(.horizontal, 16)
            .padding(.bottom, 4)
        }
    }
}

struct MarkerStrip: View {
    let duration: Double
    let marks: [(number: Int, offset: Double)]

    var body: some View {
        GeometryReader { space in
            ZStack(alignment: .leading) {
                Capsule()
                    .fill(Color(.tertiarySystemFill))
                    .frame(height: 4)

                ForEach(marks, id: \.number) { mark in
                    Text("\(mark.number)")
                        .font(.system(size: 9, weight: .bold).monospacedDigit())
                        .foregroundStyle(.black)
                        .frame(width: 15, height: 15)
                        .background(Color.yellow, in: Circle())
                        .offset(x: position(mark.offset, width: space.size.width))
                }
            }
            .frame(height: 16)
        }
        .frame(height: 16)
    }

    private func position(_ offset: Double, width: CGFloat) -> CGFloat {
        let share = duration > 0 ? min(max(offset / duration, 0), 1) : 0

        return share * (width - 15)
    }
}

struct SearchResults: View {
    let query: String
    let results: [Fact]
    let moments: [MomentSummary]
    let searching: Bool

    @Environment(Router.self) private var router

    var body: some View {
        if results.isEmpty {
            if searching {
                ProgressView()
            } else {
                ContentUnavailableView.search(text: query)
            }
        } else {
            List {
                ForEach(groups, id: \.momentId) { group in
                    Section {
                        ForEach(group.hits, id: \.fact.id) { hit in
                            Button { router.openSearch(results, at: hit.position, query: query) } label: {
                                HitRow(fact: hit.fact, marked: Highlight.shared(source: hit.fact.text, context: query))
                            }
                            .buttonStyle(.plain)
                        }
                    } header: {
                        header(group)
                    }
                }
            }
            .listStyle(.plain)
        }
    }

    private struct HitGroup {
        let momentId: String
        let hits: [(position: Int, fact: Fact)]
    }

    private var groups: [HitGroup] {
        var order: [String] = []
        var hits: [String: [(position: Int, fact: Fact)]] = [:]

        for (position, fact) in results.enumerated() {
            if hits[fact.momentId] == nil { order.append(fact.momentId) }

            hits[fact.momentId, default: []].append((position, fact))
        }

        return order.map { HitGroup(momentId: $0, hits: hits[$0] ?? []) }
    }

    private func header(_ group: HitGroup) -> some View {
        let moment = moments.first { $0.id == group.momentId }
        let start = moment?.startedAt ?? group.hits[0].fact.moment.startedAt

        return VStack(alignment: .leading, spacing: 6) {
            Text(start.formatted(.dateTime.weekday(.wide).month().day().hour().minute()))
                .font(.footnote.weight(.medium))
                .foregroundStyle(.secondary)
                .textCase(nil)

            if let moment {
                MarkerStrip(duration: moment.durationSeconds,
                            marks: group.hits.map { ($0.position + 1, $0.fact.offsetSeconds) })
            }
        }
    }
}

struct HitRow: View {
    let fact: Fact
    let marked: Set<String>

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            FrameImage(path: fact.image)
                .frame(width: 64, height: 48)
                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))

            VStack(alignment: .leading, spacing: 3) {
                Text(fact.at.formatted(date: .omitted, time: .shortened))
                    .font(.caption.weight(.semibold).monospacedDigit())
                    .foregroundStyle(.secondary)

                Text(Highlight.mark(fact.text, marked))
                    .font(.subheadline)
                    .lineLimit(3)
                    .multilineTextAlignment(.leading)
            }
        }
        .padding(.vertical, 2)
    }
}
