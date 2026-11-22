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
                                ForEach(active) { upload in UploadRow(upload: upload) { await retry(upload) } }
                            }
                        }

                        ForEach(days, id: \.self) { day in
                            Section {
                                ForEach(moments.filter { Calendar.current.isDate($0.startedAt, inSameDayAs: day) }) { moment in
                                    NavigationLink(value: MomentRoute(id: moment.id, startedAt: moment.startedAt)) {
                                        VStack(alignment: .leading, spacing: 6) {
                                            MomentRow(moment: moment)

                                            let marks = router.citedSources(in: moment.id)

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
            .navigationDestination(for: MomentRoute.self) { MomentView(link: $0) }
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

    private func retry(_ upload: UploadStatus) async {
        let _: UploadID? = try? await API.current.send("/uploads/\(upload.id)/retry", method: "POST", body: [String: String]())

        await load()
        await watchUploads()
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
                    Text(moment.title ?? moment.startedAt.formatted(date: .omitted, time: .shortened))
                        .font(.body.weight(.medium))
                        .lineLimit(1)

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
        let time = moment.title == nil ? nil : moment.startedAt.formatted(date: .omitted, time: .shortened)
        let parts = [time, moment.place, clock(moment.durationSeconds), "\(moment.frames) frames"]

        return parts.compactMap { $0 }.joined(separator: " · ")
    }
}

struct UploadRow: View {
    let upload: UploadStatus
    let retry: () async -> Void

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

            if upload.status == "failed" {
                Spacer()

                Button("Try again") { Task { await retry() } }
                    .buttonStyle(.bordered)
            }
        }
        .padding(.vertical, 4)
    }
}
