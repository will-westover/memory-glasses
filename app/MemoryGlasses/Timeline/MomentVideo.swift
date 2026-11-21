import AVKit
import SwiftUI

struct MomentVideo: View {
    let momentId: String
    let start: Double

    @State private var player: AVPlayer?

    var body: some View {
        Group {
            if let player {
                VideoPlayer(player: player)
            } else {
                ProgressView()
            }
        }
        .background(.black)
        .task { await load() }
        .onDisappear { player?.pause() }
    }

    private func load() async {
        let api = API.current
        let link: VideoLink? = try? await api.get("/moments/\(momentId)/video/link")
        let asset: AVURLAsset

        if let direct = link?.url, let url = URL(string: direct) {
            asset = AVURLAsset(url: url)
        } else {
            guard let url = URL(string: api.address.trimmingCharacters(in: .whitespaces) + "/moments/\(momentId)/video")
            else { return }

            let headers = api.token.isEmpty ? [:] : ["Authorization": "Bearer \(api.token)"]
            asset = AVURLAsset(url: url, options: ["AVURLAssetHTTPHeaderFieldsKey": headers])
        }

        let loaded = AVPlayer(playerItem: AVPlayerItem(asset: asset))

        await loaded.seek(to: CMTime(seconds: start, preferredTimescale: 600))
        loaded.play()
        player = loaded
    }
}

struct VideoLink: Decodable {
    let url: String?
}
