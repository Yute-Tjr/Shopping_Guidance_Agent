import Foundation

/// 后端 SSE 流投递的所有事件类型；SSEParser 把原始 `event: xxx\ndata: {...}` 块
/// 解析成本枚举的某个 case。
public enum SSEEvent: Equatable, Sendable {
    case session(id: String)
    case status(stage: String)
    case token(text: String)
    case productCard(ProductCard)
    case clarify(ClarifyPayload)
    case error(code: String, message: String)
    case done
}
