import Foundation
import SwiftUI

/// 应用入口。Phase 3 起首页是 NavigationStack 包裹的 ChatView，
/// 整个对话 → 详情 → 返回 流程在同一个 stack 里。
@main
struct ShoppingGuideApp: App {
    @StateObject private var environment = AppEnvironment()

    var body: some Scene {
        WindowGroup {
            AppRootView(environment: environment)
                .environmentObject(environment)
        }
    }
}

private struct AppRootView: View {
    @ObservedObject var environment: AppEnvironment
    @State private var introFinished = false

    private var introVideoURL: URL {
        Bundle.main.bundleURL.appendingPathComponent("app_begin.mp4")
    }

    var body: some View {
        let introState = LaunchIntroState(
            videoAvailable: true,
            reduceMotion: false,
            finished: introFinished
        )

        ZStack {
            NavigationStack {
                ChatView(env: environment)
                    .environmentObject(environment)
            }
            .environmentObject(environment)

            if introState.shouldShowIntro {
                LaunchIntroView(videoURL: introVideoURL) {
                    introFinished = true
                }
                .transition(.opacity)
                .zIndex(1)
            }
        }
        .animation(.easeOut(duration: 0.22), value: introState.shouldShowIntro)
    }
}
