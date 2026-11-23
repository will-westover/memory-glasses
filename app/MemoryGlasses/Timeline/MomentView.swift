import SwiftUI

struct MomentView: View {
    let link: MomentRoute

    @Environment(Router.self) private var router

    @State private var detail: MomentDetail?
    @State private var problem: String?
    @State private var showTranscript = false
    @State private var showDetails = false
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
                    .onChange(of: router.focusChangeCount) { applyFocus(scroller) }
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
        let chapters = chapters(of: detail)
        let offset = currentOffset(detail)

        return VStack(alignment: .leading, spacing: 28) {
            header(detail, chapters: chapters)
                .padding(.horizontal, 20)

            if !detail.frames.isEmpty {
                let frame = detail.frames[min(index, detail.frames.count - 1)]

                VStack(alignment: .leading, spacing: 14) {
                    PhotoPager(frames: detail.frames, index: $index, markers: markers(detail), range: focusRange,
                               ticks: ticks(chapters, in: detail.frames))

                    if let focused, focused.isSpeech {
                        QuoteCard(fact: focused)
                            .padding(.horizontal, 20)
                    }

                    thisPhoto(frame, hasVideo: detail.hasVideo)
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

                if !chapters.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        SectionTitle("Chapters")

                        ForEach(chapters) { chapter in
                            chapterRow(chapter, open: chapterContaining(offset, in: chapters)?.id == chapter.id || focused?.id == chapter.id,
                                       lines: detail.transcript.sentences.filter { chapterContaining($0.start, in: chapters)?.id == chapter.id },
                                       detail: detail)
                        }
                    }
                }

                let outside = detail.transcript.sentences.filter { chapterContaining($0.start, in: chapters) == nil }

                if !outside.isEmpty {
                    transcript(outside, title: chapters.isEmpty ? "Transcript" : "Also said", hasVideo: detail.hasVideo)
                }

                if detail.hasVideo {
                    AddFramesControl(momentId: link.id, currentRate: detail.framesPerMinute ?? 30) { await load() }
                }
            }
            .padding(.horizontal, 20)
        }
    }

    private func header(_ detail: MomentDetail, chapters: [Chapter]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(detail.title ?? link.startedAt.formatted(.dateTime.weekday(.wide).hour().minute()))
                .font(.title3.weight(.semibold))

            if let overview = detail.overview, !overview.isEmpty {
                Text(overview)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(4)
            }

            Text([link.startedAt.formatted(date: .abbreviated, time: .shortened), spokenLength(length(detail, chapters))]
                .joined(separator: " · "))
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)
        }
    }

    private func thisPhoto(_ frame: Frame, hasVideo: Bool) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("This photo")
                    .font(.subheadline.weight(.semibold))

                Text(clock(frame.offsetSeconds))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)

                Spacer()

                if hasVideo {
                    Button { playingFrom = frame.offsetSeconds } label: {
                        Label("Play from here", systemImage: "play.fill")
                            .font(.subheadline.weight(.medium))
                    }
                }
            }

            PeopleChips(people: frame.people)

            Text(KeywordMatch.emphasize(frame.short ?? firstSentence(frame.summary), emphasisWords(at: frame.offsetSeconds)))
                .font(.body)
                .lineSpacing(2)

            DisclosureGroup(isExpanded: $showDetails) {
                VStack(alignment: .leading, spacing: 8) {
                    Text(KeywordMatch.emphasize(frame.summary, emphasisWords(at: frame.offsetSeconds)))
                        .font(.subheadline)
                        .lineSpacing(2)

                    ForEach(frame.objects, id: \.self) { object in
                        Text("\(object.name): \(object.location)")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }

                    ForEach(frame.texts, id: \.self) { seen in
                        Text(KeywordMatch.emphasize("Text on \(seen.surface): \(seen.text)", emphasisWords(at: frame.offsetSeconds)))
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }

                    NavigationLink(value: frameFact(frame)) {
                        Label("Listen and see more", systemImage: "waveform")
                            .font(.subheadline.weight(.medium))
                    }
                }
                .padding(.top, 8)
            } label: {
                Text("Details")
                    .font(.subheadline.weight(.medium))
            }
        }
    }

    private func chapterRow(_ chapter: Chapter, open: Bool, lines: [SpokenLine], detail: MomentDetail) -> some View {
        let focusedHere = focused?.id == chapter.id

        return VStack(alignment: .leading, spacing: 10) {
            Button { jump(to: chapter, in: detail.frames) } label: {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text("\(clock(chapter.start))–\(clock(chapter.end))")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                        .frame(width: 78, alignment: .leading)

                    Text(chapter.label)
                        .font(.subheadline.weight(open ? .semibold : .regular))
                        .lineLimit(open ? 2 : 1)
                        .multilineTextAlignment(.leading)

                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if open {
                Text(KeywordMatch.emphasize(chapter.text, focusedHere ? router.focusWords : []))
                    .font(.subheadline)
                    .lineSpacing(3)

                ForEach(lines, id: \.self) { line in
                    spokenLine(line, hasVideo: detail.hasVideo)
                }
            }
        }
        .padding(12)
        .background(open ? (focusedHere ? Color.yellow.opacity(0.18) : Color(.secondarySystemBackground)) : .clear,
                    in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .animation(.snappy, value: open)
        .id("chapter-\(chapter.id)")
    }

    private func spokenLine(_ line: SpokenLine, hasVideo: Bool) -> some View {
        let spoken = isFocusedLine(line)

        return HStack(alignment: .firstTextBaseline, spacing: 10) {
            if hasVideo {
                Button { playingFrom = line.start } label: {
                    Image(systemName: "play.circle")
                        .font(.subheadline)
                }
                .accessibilityLabel("Play from \(clock(line.start))")
            }

            Text(clock(line.start))
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)

            Text(KeywordMatch.emphasize(line.text, spoken ? router.focusWords : []))
                .font(.subheadline)
        }
        .padding(spoken ? 8 : 0)
        .background(spoken ? Color.yellow.opacity(0.18) : .clear,
                    in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .id("line-\(line.start)")
    }

    private func chapters(of detail: MomentDetail) -> [Chapter] {
        if let chapters = detail.chapters, !chapters.isEmpty { return chapters }

        let summaries = detail.summaries.sorted { $0.offsetSeconds < $1.offsetSeconds }

        return summaries.indices.map { position in
            let summary = summaries[position]
            let next = position + 1 < summaries.count ? summaries[position + 1].offsetSeconds : nil
            let end = next ?? summary.endSeconds ?? summary.offsetSeconds + 60

            return Chapter(id: summary.id, start: summary.offsetSeconds, end: end, title: nil, text: summary.text)
        }
    }

    private func chapterContaining(_ offset: Double, in chapters: [Chapter]) -> Chapter? {
        chapters.last { $0.start <= offset }
    }

    private func length(_ detail: MomentDetail, _ chapters: [Chapter]) -> Double {
        max(detail.frames.last?.offsetSeconds ?? 0, detail.transcript.last?.end ?? 0, chapters.last?.end ?? 0)
    }

    private func currentOffset(_ detail: MomentDetail) -> Double {
        if !detail.frames.isEmpty { return detail.frames[min(index, detail.frames.count - 1)].offsetSeconds }

        return focused?.offsetSeconds ?? 0
    }

    private func ticks(_ chapters: [Chapter], in frames: [Frame]) -> Set<Int> {
        Set(chapters.compactMap { chapter in frames.firstIndex { $0.offsetSeconds >= chapter.start } })
    }

    private func jump(to chapter: Chapter, in frames: [Frame]) {
        guard let position = frames.firstIndex(where: { $0.offsetSeconds >= chapter.start })
                ?? nearest(chapter.start, in: frames) else { return }

        withAnimation(.snappy) { index = position }
    }

    private func firstSentence(_ text: String) -> String {
        guard let end = text.firstIndex(where: { ".!?".contains($0) }) else { return text }

        return String(text[...end])
    }

    private func frameFact(_ frame: Frame) -> Fact {
        Fact(id: -frame.id, kind: "frame", at: frame.at, momentId: link.id, offsetSeconds: frame.offsetSeconds,
             text: frame.summary, image: frame.image)
    }

    private func transcript(_ lines: [SpokenLine], title: String, hasVideo: Bool) -> some View {
        DisclosureGroup(isExpanded: $showTranscript) {
            VStack(alignment: .leading, spacing: 10) {
                ForEach(lines, id: \.self) { line in
                    spokenLine(line, hasVideo: hasVideo)
                }
            }
            .padding(.top, 10)
        } label: {
            Label(title, systemImage: "waveform")
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

    private func emphasisWords(at offset: Double) -> Set<String> {
        guard let focused else { return [] }

        if let focusRange, focusRange.contains(offset) { return router.focusWords }

        return abs(focused.offsetSeconds - offset) < 1 ? router.focusWords : []
    }

    private func isFocusedLine(_ line: SpokenLine) -> Bool {
        guard let focused, focused.isSpeech, let focusRange else { return false }

        return line.end >= focusRange.lowerBound && line.start <= focusRange.upperBound
    }

    private func markers(_ detail: MomentDetail) -> [Int: Int] {
        var result: [Int: Int] = [:]

        for (number, fact) in router.citedSources(in: link.id) {
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

        let chapters = chapters(of: detail)

        if focused.isSpeech, let line = detail.transcript.first(where: isFocusedLine) {
            if chapterContaining(line.start, in: chapters) == nil { showTranscript = true }

            scroll(scroller, to: "line-\(line.start)")
        } else if focused.kind == "summary" {
            if let chapter = chapters.first(where: { $0.id == focused.id }) ?? chapterContaining(focused.offsetSeconds, in: chapters) {
                scroll(scroller, to: "chapter-\(chapter.id)")
            }
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

struct TrailPill: View {
    @Environment(Router.self) private var router

    var body: some View {
        if let trail = router.trail {
            HStack(spacing: 10) {
                Button {
                    trail.fromSearch ? router.clearTrail() : router.backToChat()
                } label: {
                    Label(trail.fromSearch ? "Done" : "Back to chat",
                          systemImage: trail.fromSearch ? "xmark" : "chevron.left")
                        .font(.subheadline.weight(.semibold))
                }

                Spacer()

                if trail.sources.count > 1 {
                    Button { router.stepSource(by: -1) } label: { Image(systemName: "chevron.left") }
                        .disabled(trail.index == 0)
                        .accessibilityLabel("Previous source")

                    Text("\(trail.fromSearch ? "Result" : "Source") \(trail.index + 1) of \(trail.sources.count)")
                        .font(.subheadline.monospacedDigit())

                    Button { router.stepSource(by: 1) } label: { Image(systemName: "chevron.right") }
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
