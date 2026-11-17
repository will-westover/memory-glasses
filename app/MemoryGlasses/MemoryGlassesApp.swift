import SwiftUI

@main
struct MemoryGlassesApp: App {
    @State private var store = ConversationStore()
    @State private var pairing: Pairing?

    var body: some Scene {
        WindowGroup {
            TabView {
                AskView()
                    .tabItem { Label("Ask", systemImage: "bubble.left.and.text.bubble.right") }

                TimelineView()
                    .tabItem { Label("Timeline", systemImage: "clock") }

                WorldView()
                    .tabItem { Label("World", systemImage: "map") }

                StatsView()
                    .tabItem { Label("Stats", systemImage: "chart.bar") }

                GlassesView()
                    .tabItem { Label("Glasses", systemImage: "eyeglasses") }
            }
            .tint(.primary)
            .environment(store)
            .onOpenURL { pairing = Pairing(url: $0) }
            .alert("Connect to this server?", isPresented: Binding(get: { pairing != nil }, set: { if !$0 { pairing = nil } }),
                   presenting: pairing) { found in
                Button("Connect") { found.save() }
                Button("Cancel", role: .cancel) {}
            } message: { found in
                Text(found.server)
            }
        }
    }
}

struct FrameImage: View {
    let path: String?

    @State private var image: UIImage?

    private static let cache = NSCache<NSString, UIImage>()

    var body: some View {
        Rectangle()
            .fill(Color(.tertiarySystemFill))
            .overlay {
                if let image {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFill()
                }
            }
            .clipped()
            .task(id: path) { await load() }
    }

    private func load() async {
        guard let path else { return }

        if let cached = Self.cache.object(forKey: path as NSString) {
            image = cached
            return
        }

        guard let data = try? await API.current.image(path), let loaded = UIImage(data: data) else { return }

        Self.cache.setObject(loaded, forKey: path as NSString)
        image = loaded
    }
}

struct ServerProblem: View {
    let message: String
    let retry: () async -> Void

    var body: some View {
        ContentUnavailableView {
            Label("Can't reach the server", systemImage: "wifi.exclamationmark")
        } description: {
            Text(message)
        } actions: {
            Button("Try again") { Task { await retry() } }
                .buttonStyle(.bordered)
                .buttonBorderShape(.capsule)
        }
    }
}

struct SectionTitle: View {
    let text: String

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Text(text)
            .font(.footnote.weight(.medium))
            .foregroundStyle(.secondary)
    }
}

struct FilledButton: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        FilledLabel(label: configuration.label, pressed: configuration.isPressed)
    }
}

struct FilledLabel<Content: View>: View {
    let label: Content
    let pressed: Bool

    @Environment(\.isEnabled) private var enabled

    var body: some View {
        label
            .font(.body.weight(.semibold))
            .foregroundStyle(Color(.systemBackground))
            .padding(.horizontal, 18)
            .padding(.vertical, 10)
            .background(Color.primary.opacity(enabled ? (pressed ? 0.7 : 1) : 0.3), in: Capsule())
    }
}
