import Foundation
import SwiftUI

/// 全局环境：保存后端 BaseURL 与匿名会话 ID。
/// Phase 0 仅做最小占位，Phase 2 起再注入更多依赖（APIClient、SessionStore 等）。
@MainActor
final class AppEnvironment: ObservableObject {
    /// 后端 BaseURL，开发期默认指向本机 8000 端口。
    /// 真机调试请改成 Mac 在局域网中的 IP；发布前从 Info.plist 注入。
    @Published var baseURL: URL = URL(string: "http://127.0.0.1:8000")!

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
