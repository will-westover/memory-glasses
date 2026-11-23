import SwiftUI

struct LinkSide: Decodable, Hashable {
    let id: Int
    let name: String
    let photo: String
}

struct ThingLink: Decodable, Identifiable, Hashable {
    let id: Int
    let name: String
    let photo: String
    let sameAs: LinkSide
    var confirmed: Bool
}

struct LinksView: View {
    @State private var links: [ThingLink] = []
    @State private var problem: String?
    @State private var loaded = false
    @State private var more = false

    var body: some View {
        Group {
            if let problem {
                ServerProblem(message: problem, retry: load)
            } else if !loaded {
                ProgressView()
            } else if links.isEmpty {
                ContentUnavailableView("Nothing seen again yet", systemImage: "square.on.square")
            } else {
                List {
                    ForEach($links) { $link in
                        LinkRow(link: $link) { links.removeAll { $0.id == link.id } }
                    }

                    if more {
                        ProgressView()
                            .frame(maxWidth: .infinity)
                            .task(id: links.count) { await loadMore() }
                    }
                }
                .listStyle(.plain)
                .refreshable { await load() }
            }
        }
        .navigationTitle("Seen again")
        .task { await load() }
    }

    private func page(before: Int?) async throws -> [ThingLink] {
        try await API.current.get("/things/links" + (before.map { "?before=\($0)" } ?? ""))
    }

    private func load() async {
        do {
            links = try await page(before: nil)
            more = !links.isEmpty
            problem = nil
        } catch {
            problem = error.localizedDescription
        }

        loaded = true
    }

    private func loadMore() async {
        guard let oldest = links.last?.id, let next = try? await page(before: oldest) else { return }

        links += next
        more = !next.isEmpty
    }
}

struct LinkRow: View {
    @Binding var link: ThingLink
    let removed: () -> Void

    @State private var working = false

    var body: some View {
        VStack(spacing: 12) {
            HStack(spacing: 10) {
                side(link.photo, link.name)

                Image(systemName: "equal")
                    .foregroundStyle(.secondary)

                side(link.sameAs.photo, link.sameAs.name)
            }

            if link.confirmed {
                Label("Same", systemImage: "checkmark")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            } else {
                HStack(spacing: 10) {
                    Button("Not the same") { Task { await answer("unlink") } }
                        .buttonStyle(.bordered)

                    Button("Same") { Task { await answer("confirm") } }
                        .buttonStyle(.borderedProminent)
                }
                .disabled(working)
                .font(.subheadline.weight(.medium))
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
    }

    private func side(_ photo: String, _ name: String) -> some View {
        VStack(spacing: 6) {
            FrameImage(path: photo)
                .aspectRatio(1, contentMode: .fit)
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))

            Text(name)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity)
    }

    private func answer(_ action: String) async {
        working = true
        defer { working = false }

        guard (try? await API.current.send("/things/\(link.id)/\(action)", method: "POST", body: [String: String]()) as EmptyReply) != nil else {
            return
        }

        if action == "unlink" {
            removed()
        } else {
            link.confirmed = true
        }
    }
}
