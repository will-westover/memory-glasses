import SwiftUI

@main
struct MemoryGlassesApp: App {
    var body: some Scene {
        WindowGroup {
            TabView {
                TimelineView()
                    .tabItem { Label("Timeline", systemImage: "clock") }

                AskView()
                    .tabItem { Label("Ask", systemImage: "bubble.left.and.text.bubble.right") }

                StatsView()
                    .tabItem { Label("Stats", systemImage: "chart.bar") }

                GlassesView()
                    .tabItem { Label("Glasses", systemImage: "eyeglasses") }
            }
        }
    }
}

struct FrameImage: View {
    let path: String?

    @State private var image: UIImage?

    private static let cache = NSCache<NSString, UIImage>()

    var body: some View {
        ZStack {
            Rectangle().fill(.quaternary)

            if let image {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFill()
            }
        }
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
        }
    }
}
