import SwiftUI

/// 应用入口。Phase 0 仅展示占位首页，验证 Xcode 工程能跑起来。
/// 后续 Phase 3 替换为 `ChatView()` 主界面 + Tab。
@main
struct ShoppingGuideApp: App {
    @StateObject private var environment = AppEnvironment()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(environment)
        }
    }
}

/// Phase 0 占位首页：能看到这段文字即说明客户端骨架就绪。
struct ContentView: View {
    @EnvironmentObject private var env: AppEnvironment

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "bag.fill")
                .font(.system(size: 64))
                .foregroundStyle(.tint)
            Text("Shopping Guide · Phase 0 就绪")
                .font(.title2.bold())
            Text("Backend: \(env.baseURL.absoluteString)")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .padding()
    }
}

#Preview {
    ContentView()
        .environmentObject(AppEnvironment())
}
