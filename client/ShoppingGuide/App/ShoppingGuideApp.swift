import SwiftUI

/// 应用入口。Phase 3 起首页是 NavigationStack 包裹的 ChatView，
/// 整个对话 → 详情 → 返回 流程在同一个 stack 里。
@main
struct ShoppingGuideApp: App {
    @StateObject private var environment = AppEnvironment()

    var body: some Scene {
        WindowGroup {
            NavigationStack {
                ChatView(env: environment)
                    .environmentObject(environment)
            }
            .environmentObject(environment)
        }
    }
}
