import Foundation

/// 把 ChatViewModel 与具体 SSE/HTTP 实现解耦。
/// 生产路径：`LiveChatTransport`（包装 APIClient + StreamingClient）
/// 测试路径：`FakeChatTransport`（直接喂事件数组）
public protocol ChatTransport: Sendable {
    func stream(message: String, sessionID: String?) -> AsyncStream<SSEEvent>
}

/// 生产环境实现：用 APIClient 构造请求 + StreamingClient 跑 SSE。
public final class LiveChatTransport: ChatTransport, @unchecked Sendable {

    private let api: APIClient

    public init(api: APIClient) {
        self.api = api
    }

    public func stream(message: String, sessionID: String?) -> AsyncStream<SSEEvent> {
        let request = api.buildChatStreamRequest(message: message, sessionID: sessionID)
        // 每次请求都新建 StreamingClient，确保 buffer / continuation 不串
        let client = StreamingClient()
        return client.stream(request)
    }
}
