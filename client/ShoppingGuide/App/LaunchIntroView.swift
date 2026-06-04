import AVFoundation
import SwiftUI
import UIKit

struct LaunchIntroView: View {
    let videoURL: URL
    let onFinished: () -> Void

    @State private var player: AVPlayer?
    @State private var didFinish = false
    @State private var canAutoFinish = false
    @State private var queuedAutoFinish = false

    var body: some View {
        ZStack(alignment: .topTrailing) {
            Theme.Palette.brand.ignoresSafeArea()

            if let player {
                PlayerLayerView(player: player)
                    .ignoresSafeArea()
                    .overlay(Theme.Palette.brand.opacity(0.08))
            }

            Button {
                finish()
            } label: {
                Text("跳过")
                    .font(Theme.Typo.caption(.semibold))
                    .foregroundStyle(Theme.Palette.onBrand)
                    .padding(.horizontal, 12)
                    .frame(height: 34)
                    .background(
                        Capsule(style: .continuous)
                            .fill(Theme.Palette.brand.opacity(0.72))
                    )
                    .overlay(
                        Capsule(style: .continuous)
                            .stroke(Theme.Palette.onBrand.opacity(0.22), lineWidth: 1)
                    )
            }
            .buttonStyle(.plain)
            .accessibilityLabel("跳过开场动画")
            .padding(.top, 58)
            .padding(.trailing, Theme.Spacing.l)
        }
        .onAppear(perform: startPlayback)
        .task {
            try? await Task.sleep(nanoseconds: 900_000_000)
            canAutoFinish = true
            if queuedAutoFinish {
                finish()
            }
        }
        .onDisappear {
            player?.pause()
            player?.replaceCurrentItem(with: nil)
        }
        .onReceive(NotificationCenter.default.publisher(for: .AVPlayerItemDidPlayToEndTime)) { notification in
            guard let item = notification.object as? AVPlayerItem,
                  item === player?.currentItem else { return }
            autoFinish()
        }
        .onReceive(NotificationCenter.default.publisher(for: .AVPlayerItemFailedToPlayToEndTime)) { notification in
            guard let item = notification.object as? AVPlayerItem,
                  item === player?.currentItem else { return }
            autoFinish()
        }
        .accessibilityAddTraits(.isModal)
    }

    private func startPlayback() {
        guard player == nil else { return }
        let item = AVPlayerItem(url: videoURL)
        let nextPlayer = AVPlayer(playerItem: item)
        nextPlayer.isMuted = true
        nextPlayer.actionAtItemEnd = .pause
        player = nextPlayer
        nextPlayer.play()
    }

    private func autoFinish() {
        guard canAutoFinish else {
            queuedAutoFinish = true
            return
        }
        finish()
    }

    private func finish() {
        guard !didFinish else { return }
        didFinish = true
        player?.pause()
        onFinished()
    }
}

private struct PlayerLayerView: UIViewRepresentable {
    let player: AVPlayer

    func makeUIView(context: Context) -> PlayerUIView {
        let view = PlayerUIView()
        view.playerLayer.videoGravity = .resizeAspectFill
        view.playerLayer.player = player
        return view
    }

    func updateUIView(_ uiView: PlayerUIView, context: Context) {
        uiView.playerLayer.player = player
    }
}

private final class PlayerUIView: UIView {
    override static var layerClass: AnyClass {
        AVPlayerLayer.self
    }

    var playerLayer: AVPlayerLayer {
        layer as! AVPlayerLayer
    }
}
