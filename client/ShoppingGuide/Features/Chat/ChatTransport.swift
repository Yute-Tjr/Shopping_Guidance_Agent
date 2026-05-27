import Foundation

/// 把 ChatViewModel 与具体 SSE/HTTP 实现解耦。
/// 生产路径：`LiveChatTransport`（包装 APIClient + StreamingClient）
/// 测试路径：`FakeChatTransport`（直接喂事件数组）
public protocol ChatTransport: Sendable {
    func stream(message: String, sessionID: String?, imageID: String?) -> AsyncStream<SSEEvent>
}

public extension ChatTransport {
    /// 兼容旧调用点：不带 imageID 时走纯文本流（Phase 5 多模态分支才需要带）。
    func stream(message: String, sessionID: String?) -> AsyncStream<SSEEvent> {
        stream(message: message, sessionID: sessionID, imageID: nil)
    }
}

/// 生产环境实现：用 APIClient 构造请求 + StreamingClient 跑 SSE。
public final class LiveChatTransport: ChatTransport, @unchecked Sendable {

    private let api: APIClient

    public init(api: APIClient) {
        self.api = api
    }

    public func stream(message: String, sessionID: String?, imageID: String?) -> AsyncStream<SSEEvent> {
        let request = api.buildChatStreamRequest(
            message: message, sessionID: sessionID, imageID: imageID,
        )
        // 每次请求都新建 StreamingClient，确保 buffer / continuation 不串
        let client = StreamingClient()
        return client.stream(request)
    }
}
