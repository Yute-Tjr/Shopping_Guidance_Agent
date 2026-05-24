import Foundation

/// 路由路径常量。改路径只动这里，避免到处硬编码。
public enum Endpoints {
    public static let chatStream = "api/v1/chat/stream"

    public static func productDetail(_ productId: String) -> String {
        "api/v1/products/\(productId)"
    }

    public static let health = "healthz"
}
