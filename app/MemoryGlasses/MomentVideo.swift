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
        .task { load() }
        .onDisappear { player?.pause() }
    }

    private func load() {
        let api = API.current

        guard let url = URL(string: api.address.trimmingCharacters(in: .whitespaces) + "/moments/\(momentId)/video")
        else { return }

        let headers = api.token.isEmpty ? [:] : ["Authorization": "Bearer \(api.token)"]
        let asset = AVURLAsset(url: url, options: ["AVURLAssetHTTPHeaderFieldsKey": headers])
        let loaded = AVPlayer(playerItem: AVPlayerItem(asset: asset))

        loaded.seek(to: CMTime(seconds: start, preferredTimescale: 600))
        loaded.play()
        player = loaded
    }
}
