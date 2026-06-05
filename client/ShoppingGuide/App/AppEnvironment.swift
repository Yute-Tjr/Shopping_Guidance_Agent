import Foundation
import SwiftUI
import Combine

/// 全局环境：保存后端 BaseURL 与匿名会话 ID。
/// Phase 0 仅做最小占位，Phase 2 起再注入更多依赖（APIClient、SessionStore 等）。
@MainActor
final class AppEnvironment: ObservableObject {
    /// 后端 BaseURL，沙箱期默认指向 ECS Nginx 公网入口；发布前从 Info.plist 注入 HTTPS 域名。
    @Published var baseURL: URL = URL(string: "http://121.196.247.225")!

    /// 匿名会话 ID：iOS 端用 IDFV，首次启动持久化到 UserDefaults。
    @Published var sessionID: String

    init() {
        let key = "shopping_guide.session_id"
        if let stored = UserDefaults.standard.string(forKey: key) {
            self.sessionID = stored
        } else {
            let generated = UIDevice.current.identifierForVendor?.uuidString
                ?? UUID().uuidString
            UserDefaults.standard.set(generated, forKey: key)
            self.sessionID = generated
        }
    }
}
