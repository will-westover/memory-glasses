import AVFoundation
import SwiftUI

struct MomentView: View {
    static let saidWindow = 30.0
    static let listenBefore = 3.0
    static let listenFor = 30.0

    let fact: Fact

    @State private var detail: SessionDetail?
    @State private var problem: String?
    @State private var index = 0
    @State private var player: AVAudioPlayer?
    @State private var audioState = "Listen from here"
    @State private var showDetails = false

    var body: some View {
        Group {
            if let detail, !detail.frames.isEmpty {
                ScrollView {
                    content(detail)
                        .padding(.vertical, 12)
                }
            } else if let problem {
                ServerProblem(message: problem, retry: load)
            } else if detail != nil {
                ContentUnavailableView("No pictures in this recording", systemImage: "photo")
            } else {
                ProgressView()
            }
        }
        .navigationTitle(fact.at.formatted(.dateTime.weekday(.abbreviated).hour().minute()))
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
        .onDisappear { player?.stop() }
    }

    private func content(_ detail: SessionDetail) -> some View {
        let frame = detail.frames[min(index, detail.frames.count - 1)]

        return VStack(alignment: .leading, spacing: 24) {
            TabView(selection: $index) {
                ForEach(detail.frames.indices, id: \.self) { position in
                    FrameImage(path: detail.frames[position].image)
                        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                        .padding(.horizontal, 20)
                        .tag(position)
                }
            }
            .tabViewStyle(.page(indexDisplayMode: .never))
            .aspectRatio(4 / 3, contentMode: .fit)

            VStack(alignment: .leading, spacing: 18) {
                HStack {
                    Text("\(frame.at.formatted(date: .omitted, time: .standard)) · \(clock(frame.offsetSeconds)) in")
                    Spacer()
                    Text("\(index + 1) of \(detail.frames.count)")
                }
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)

                Text(frame.summary)
                    .lineSpacing(3)

                listenButton

                said(detail.transcript, around: frame.offsetSeconds)

                seen(frame)

                NavigationLink(value: SessionLink(id: fact.sessionId, startedAt: fact.session.startedAt,
                                                  focus: frame.offsetSeconds)) {
                    Label("Open in Timeline", systemImage: "clock")
                        .font(.subheadline.weight(.medium))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                        .background(Color(.secondarySystemBackground), in: Capsule())
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, 20)
        }
    }

    private var listenButton: some View {
        Button { Task { await toggleAudio() } } label: {
            Label(audioState, systemImage: player?.isPlaying == true ? "stop.fill" : "play.fill")
                .font(.subheadline.weight(.medium))
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .background(Color.primary, in: Capsule())
                .foregroundStyle(Color(.systemBackground))
        }
    }

    @ViewBuilder
    private func said(_ lines: [SpokenLine], around offset: Double) -> some View {
        let nearby = lines.filter { abs($0.start - offset) <= Self.saidWindow }

        if !nearby.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                SectionTitle("Said around then")

                ForEach(nearby, id: \.self) { line in
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Text(clock(line.start))
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.tertiary)

                        Text(line.text)
                            .font(.subheadline)
                    }
                    .padding(.vertical, 6)
                    .padding(.horizontal, 10)
                    .background(isCited(line) ? Color.yellow.opacity(0.25) : .clear,
                                in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                }
            }
        }
    }

    @ViewBuilder
    private func seen(_ frame: Frame) -> some View {
        if !frame.objects.isEmpty || !frame.texts.isEmpty {
            DisclosureGroup(isExpanded: $showDetails) {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(frame.texts, id: \.self) { Text("“\($0.text)” on \($0.surface)") }
                    ForEach(frame.objects, id: \.self) { Text("\($0.name): \($0.location)") }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 8)
            } label: {
                Text("What it saw (\(frame.objects.count + frame.texts.count))")
                    .font(.subheadline.weight(.medium))
            }
        }
    }

    private func isCited(_ line: SpokenLine) -> Bool {
        fact.isSpeech && line.start <= fact.offsetSeconds + 0.5 && fact.offsetSeconds <= line.end
    }

    private func toggleAudio() async {
        if let player, player.isPlaying {
            player.stop()
            audioState = "Listen from here"
            return
        }

        guard let frames = detail?.frames, !frames.isEmpty else { return }

        let start = max(0, frames[min(index, frames.count - 1)].offsetSeconds - Self.listenBefore)
        audioState = "Loading…"

        do {
            let data = try await API.current.audio(session: fact.sessionId, from: start, to: start + Self.listenFor)
            try AVAudioSession.sharedInstance().setCategory(.playback)

            let loaded = try AVAudioPlayer(data: data)
            loaded.play()
            player = loaded
            audioState = "Stop"
        } catch {
            audioState = "No sound here"
        }
    }

    private func load() async {
        do {
            let loaded: SessionDetail = try await API.current.get("/sessions/\(fact.sessionId)")

            index = startIndex(in: loaded.frames)
            detail = loaded
            problem = nil
        } catch {
            problem = error.localizedDescription
        }
    }

    private func startIndex(in frames: [Frame]) -> Int {
        if let exact = frames.firstIndex(where: { $0.image != nil && $0.image == fact.image }) {
            return exact
        }

        let nearest = frames.indices.min { abs(frames[$0].offsetSeconds - fact.offsetSeconds) < abs(frames[$1].offsetSeconds - fact.offsetSeconds) }

        return nearest ?? 0
    }
}
