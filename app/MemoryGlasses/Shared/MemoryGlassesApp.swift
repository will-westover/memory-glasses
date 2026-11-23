import SwiftUI

@main
struct MemoryGlassesApp: App {
    init() {
        GlassesLink.shared.start()
    }

    @State private var store = ConversationStore()
    @State private var router = Router()
    @State private var pairing: Pairing?

    var body: some Scene {
        WindowGroup {
            TabView(selection: $router.tab) {
                AskView()
                    .tabItem { Label("Ask", systemImage: "bubble.left.and.text.bubble.right") }
                    .tag(AppTab.ask)

                TimelineView()
                    .tabItem { Label("Timeline", systemImage: "clock") }
                    .tag(AppTab.timeline)

                WorldView()
                    .tabItem { Label("World", systemImage: "map") }
                    .tag(AppTab.world)

                StatsView()
                    .tabItem { Label("Stats", systemImage: "chart.bar") }
                    .tag(AppTab.stats)

                GlassesView()
                    .tabItem { Label("Glasses", systemImage: "eyeglasses") }
                    .tag(AppTab.glasses)
            }
            .tint(.primary)
            .environment(store)
            .environment(router)
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
    @State private var failed = false

    private static let cache = NSCache<NSString, UIImage>()

    var body: some View {
        Rectangle()
            .fill(Color(.tertiarySystemFill))
            .overlay {
                if let image {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFill()
                        .transition(.opacity)
                } else if failed {
                    Image(systemName: "photo.badge.exclamationmark")
                        .font(.footnote)
                        .foregroundStyle(.tertiary)
                        .accessibilityLabel("Photo didn't load")
                } else if path != nil {
                    Shimmer()
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

        failed = false

        guard let data = try? await API.current.download(path), let loaded = UIImage(data: data) else {
            if !Task.isCancelled { failed = true }
            return
        }

        Self.cache.setObject(loaded, forKey: path as NSString)
        withAnimation(.easeOut(duration: 0.2)) { image = loaded }
    }
}

struct Shimmer: View {
    @State private var moving = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        GeometryReader { space in
            LinearGradient(colors: [.clear, Color.primary.opacity(0.06), .clear], startPoint: .leading, endPoint: .trailing)
                .frame(width: space.size.width * 0.8)
                .offset(x: moving ? space.size.width : -space.size.width * 0.8)
        }
        .onAppear {
            guard !reduceMotion else { return }

            withAnimation(.linear(duration: 1.2).repeatForever(autoreverses: false)) { moving = true }
        }
        .allowsHitTesting(false)
    }
}

struct PlaceholderRows: View {
    var count = 6
    var image = true

    var body: some View {
        List(0..<count, id: \.self) { _ in
            HStack(spacing: 14) {
                if image {
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .fill(Color(.tertiarySystemFill))
                        .frame(width: 56, height: 56)
                }

                VStack(alignment: .leading, spacing: 6) {
                    Text("A moment from earlier today")
                        .font(.body.weight(.medium))

                    Text("Home · 2 min")
                        .font(.subheadline)
                }
            }
            .padding(.vertical, 4)
            .redacted(reason: .placeholder)
            .overlay { Shimmer() }
            .listRowSeparator(.hidden)
        }
        .listStyle(.plain)
        .scrollDisabled(true)
        .accessibilityLabel("Loading")
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
