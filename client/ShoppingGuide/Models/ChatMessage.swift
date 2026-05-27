import Foundation

/// 视图层消息模型。
/// Assistant 消息会随 SSE token 流增量追加 `text`，并按 `event: product_card`
/// 追加 `productCards`；`isStreaming` 控制气泡末尾的光标动画。
public struct ChatMessage: Identifiable, Equatable, Sendable {
    public enum Role: String, Sendable {
        case user
        case assistant
        case system
    }

    public let id: UUID
    public let role: Role
    public var text: String
    public var productCards: [ProductCard]
    public var isStreaming: Bool
    public var clarify: ClarifyPayload?
    public var errorNotice: String?
    public let createdAt: Date
    /// Phase 5：用户消息可选携带本地缩略图（PhotosPicker 选完写到 tmpDir 的 URL），
    /// 气泡渲染时用 UIImage(contentsOfFile:) 加载——不依赖网络也免去 AsyncImage 闪烁。
    public var localImageURL: URL?

    public init(
        id: UUID = UUID(),
        role: Role,
        text: String = "",
        productCards: [ProductCard] = [],
        isStreaming: Bool = false,
        clarify: ClarifyPayload? = nil,
        errorNotice: String? = nil,
        createdAt: Date = Date(),
        localImageURL: URL? = nil
    ) {
        self.id = id
        self.role = role
        self.text = text
        self.productCards = productCards
        self.isStreaming = isStreaming
        self.clarify = clarify
        self.errorNotice = errorNotice
        self.createdAt = createdAt
        self.localImageURL = localImageURL
    }
}
