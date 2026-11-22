import AVFoundation
import MapKit
import Speech
import SwiftUI

struct CitedText: View {
    let text: String
    let sources: [Fact]
    var open: (Fact) -> Void = { _ in }

    var body: some View {
        Text(attributed)
            .lineSpacing(3)
            .textSelection(.enabled)
            .environment(\.openURL, OpenURLAction { url in
                guard url.scheme == "source", let number = Int(url.host() ?? ""), sources.indices.contains(number - 1) else {
                    return .discarded
                }

                open(sources[number - 1])

                return .handled
            })
    }

    private var attributed: AttributedString {
        var result = AttributedString()
        var rest = Substring(text)

        while let match = rest.firstMatch(of: /\[(\d+)\]/) {
            result += AttributedString(String(rest[..<match.range.lowerBound]))

            var marker = AttributedString(" \(match.1) ")
            marker.font = .caption2.weight(.bold).monospacedDigit()
            marker.baselineOffset = 5
            marker.foregroundColor = .secondary
            marker.backgroundColor = Color(.tertiarySystemFill)

            if let number = Int(match.1), sources.indices.contains(number - 1) {
                marker.link = URL(string: "source://\(number)")
            }

            result += marker
            rest = rest[match.range.upperBound...]
        }

        result += AttributedString(String(rest))

        return result
    }
}

struct FeaturedSourceCard: View {
    let fact: Fact
    let reason: String?

    @Environment(\.openSource) private var openSource

    var body: some View {
        Button { openSource(fact) } label: {
            VStack(alignment: .leading, spacing: 8) {
                ZStack(alignment: .bottomLeading) {
                    if fact.image != nil {
                        FrameImage(path: fact.image)
                    } else {
                        Rectangle()
                            .fill(Color(.secondarySystemBackground))
                            .overlay(alignment: .topLeading) {
                                Text(fact.isSpeech ? "“\(fact.text)”" : fact.text)
                                    .font(.body)
                                    .lineLimit(6)
                                    .multilineTextAlignment(.leading)
                                    .padding(16)
                            }
                    }

                    Text(fact.at.formatted(.dateTime.weekday(.abbreviated).hour().minute()))
                        .font(.caption.weight(.semibold))
                        .padding(.horizontal, 10)
                        .padding(.vertical, 5)
                        .background(.ultraThinMaterial, in: Capsule())
                        .padding(10)
                }
                .frame(maxWidth: .infinity)
                .aspectRatio(4 / 3, contentMode: .fit)
                .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))

                if let reason, !reason.isEmpty {
                    Text(reason)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.leading)
                }
            }
        }
        .buttonStyle(.plain)
    }
}

struct LayoutCard: View {
    let layout: String
    let sources: [Fact]

    var body: some View {
        switch layout {
        case "quote":
            let quotes = sources.filter(\.isSpeech).prefix(3)

            if !quotes.isEmpty {
                VStack(spacing: 10) {
                    ForEach(Array(quotes)) { QuoteCard(fact: $0) }
                }
            }
        case "people":
            let people = uniquePeople

            if !people.isEmpty {
                PeopleChips(people: people)
            }
        case "place":
            let places = uniquePlaces

            if !places.isEmpty {
                PlaceCard(places: places)
            }
        case "timeline":
            if sources.count > 1 {
                TimelineStrip(sources: sources.sorted { $0.at < $1.at })
            }
        default:
            EmptyView()
        }
    }

    private var uniquePeople: [PersonTag] {
        var seen = Set<Int>()

        return sources.flatMap { $0.people ?? [] }.filter { seen.insert($0.id).inserted }
    }

    private var uniquePlaces: [SourcePlace] {
        var seen = Set<Int>()

        return sources.compactMap(\.place).filter { $0.lat != nil && $0.lon != nil && seen.insert($0.id).inserted }
    }
}

struct QuoteCard: View {
    let fact: Fact

    @State private var player: AVAudioPlayer?
    @State private var playing = false
    @State private var loading = false
    @State private var silent = false

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Button { Task { await toggle() } } label: {
                Image(systemName: silent ? "speaker.slash" : playing ? "pause.fill" : "play.fill")
                    .font(.system(size: 15, weight: .semibold))
                    .frame(width: 36, height: 36)
                    .background(Color(.tertiarySystemFill), in: Circle())
                    .overlay { if loading { ProgressView() } }
            }
            .buttonStyle(.plain)
            .disabled(loading || silent)
            .accessibilityLabel(playing ? "Pause" : "Play what was said")

            VStack(alignment: .leading, spacing: 6) {
                Text("“\(fact.text)”")
                    .font(.callout)

                Text(caption)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        .onDisappear { player?.stop() }
    }

    private var caption: String {
        let when = fact.at.formatted(.dateTime.weekday(.abbreviated).hour().minute())

        guard let speaker = fact.speaker, !speaker.isEmpty else { return when }

        return "\(speaker.count <= 2 ? "Speaker \(speaker)" : speaker) · \(when)"
    }

    private func toggle() async {
        if let player, playing {
            player.pause()
            playing = false
            return
        }

        if let player {
            player.play()
            playing = true
            return
        }

        loading = true
        defer { loading = false }

        do {
            let data: Data

            if let url = fact.audioUrl {
                data = try await API.current.download(url)
            } else {
                let start = max(0, fact.offsetSeconds - 1)
                data = try await API.current.audio(moment: fact.momentId, from: start,
                                                   to: (fact.endSeconds ?? fact.offsetSeconds + 8) + 1)
            }

            try AVAudioSession.sharedInstance().setCategory(.playback)

            let loaded = try AVAudioPlayer(data: data)
            loaded.play()
            player = loaded
            playing = true

            try? await Task.sleep(for: .seconds(loaded.duration))

            if !loaded.isPlaying { playing = false }
        } catch {
            silent = true
        }
    }
}

struct PlaceCard: View {
    let places: [SourcePlace]

    var body: some View {
        Map(initialPosition: .automatic, interactionModes: []) {
            ForEach(places, id: \.id) { place in
                Marker(place.name ?? "Unnamed place", systemImage: "mappin",
                       coordinate: CLLocationCoordinate2D(latitude: place.lat ?? 0, longitude: place.lon ?? 0))
            }
        }
        .frame(height: 160)
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
    }
}

struct TimelineStrip: View {
    let sources: [Fact]

    @Environment(\.openSource) private var openSource

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(alignment: .top, spacing: 0) {
                ForEach(Array(sources.enumerated()), id: \.element.id) { position, fact in
                    Button { openSource(fact) } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            HStack(spacing: 6) {
                                Circle()
                                    .fill(Color.primary)
                                    .frame(width: 7, height: 7)

                                Rectangle()
                                    .fill(Color(.separator))
                                    .frame(height: 1)
                                    .opacity(position == sources.count - 1 ? 0 : 1)
                            }

                            Text(fact.at.formatted(.dateTime.hour().minute()))
                                .font(.caption.weight(.semibold).monospacedDigit())

                            if fact.image != nil {
                                FrameImage(path: fact.image)
                                    .frame(width: 110, height: 80)
                                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                            }

                            Text(fact.text)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(3)
                                .multilineTextAlignment(.leading)
                        }
                        .frame(width: 130, alignment: .leading)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .scrollClipDisabled()
    }
}

@MainActor
@Observable
final class Dictation {
    private(set) var listening = false
    var problem: String?

    private let engine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var starting = false
    private var released = false

    func start(update: @escaping (String) -> Void) async {
        guard !listening, !starting else { return }

        starting = true
        released = false
        defer { starting = false }

        guard await speechAllowed(), await AVAudioApplication.requestRecordPermission() else {
            problem = "Allow speech recognition and the microphone in Settings to talk to Memory Glasses."
            return
        }

        guard !released else { return }

        guard let recognizer = SFSpeechRecognizer(), recognizer.isAvailable else {
            problem = "Speech recognition isn't available right now."
            return
        }

        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.record, mode: .measurement, options: .duckOthers)
            try session.setActive(true, options: .notifyOthersOnDeactivation)

            let request = SFSpeechAudioBufferRecognitionRequest()
            request.shouldReportPartialResults = true
            request.requiresOnDeviceRecognition = recognizer.supportsOnDeviceRecognition

            Self.tap(engine.inputNode, into: request)

            engine.prepare()
            try engine.start()

            self.request = request
            listening = true

            task = Self.recognise(request, with: recognizer) { text in
                Task { @MainActor in update(text) }
            }
        } catch {
            problem = error.localizedDescription
            stop()
        }
    }

    func stop() {
        released = true

        guard listening else { return }

        engine.stop()
        engine.inputNode.removeTap(onBus: 0)
        request?.endAudio()
        task?.finish()
        request = nil
        task = nil
        listening = false

        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    nonisolated private static func tap(_ input: AVAudioInputNode, into request: SFSpeechAudioBufferRecognitionRequest) {
        input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { buffer, _ in
            request.append(buffer)
        }
    }

    nonisolated private static func recognise(_ request: SFSpeechAudioBufferRecognitionRequest, with recognizer: SFSpeechRecognizer,
                                              heard: @escaping @Sendable (String) -> Void) -> SFSpeechRecognitionTask {
        recognizer.recognitionTask(with: request) { result, _ in
            guard let text = result?.bestTranscription.formattedString else { return }

            heard(text)
        }
    }

    private func speechAllowed() async -> Bool {
        await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { continuation.resume(returning: $0 == .authorized) }
        }
    }
}
