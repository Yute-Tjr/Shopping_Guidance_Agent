import Foundation
import Combine

/// 聊天主页 ViewModel。
///
/// 关键流程：
/// 1. `send()` 先把用户输入追到 messages 尾，再追一条空 assistant；
/// 2. 走 `transport.stream(...)`，把 SSE 事件按类型分发：
///    - token   → 累加到 assistant.text（SwiftUI 自动 diff 重渲染）
///    - productCard → 追加到 assistant.productCards
///    - clarify → 写入 assistant.clarify
///    - error   → assistant.errorNotice 写一句友好提示，不中断流
///    - done    → assistant.isStreaming = false
/// 3. `status / session` 不污染可见 UI；session 单独存到 `sessionID`，下一轮带上。
@MainActor
public final class ChatViewModel: ObservableObject {

    @Published public var messages: [ChatMessage] = []
    @Published public var inputText: String = ""
    @Published public var isSending: Bool = false
    @Published public var sessionID: String?

    private let transport: ChatTransport

    public init(transport: ChatTransport, initialSessionID: String? = nil) {
        self.transport = transport
        self.sessionID = initialSessionID
    }

    /// 发送当前 inputText。空白直接忽略。
    public func send() async {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        guard !isSending else { return }

        let userMsg = ChatMessage(role: .user, text: text)
        messages.append(userMsg)
        var assistant = ChatMessage(role: .assistant, isStreaming: true)
        messages.append(assistant)
        let assistantIndex = messages.count - 1

        inputText = ""
        isSending = true
        defer {
            // 兜底：循环里没拿到 .done 时也要清流式状态
            if assistantIndex < messages.count {
                messages[assistantIndex].isStreaming = false
            }
            isSending = false
        }

        for await event in transport.stream(message: text, sessionID: sessionID) {
            switch event {
            case .session(let id):
                sessionID = id
            case .status:
                break
            case .token(let chunk):
                messages[assistantIndex].text += chunk
            case .productCard(let card):
                messages[assistantIndex].productCards.append(card)
            case .clarify(let payload):
                messages[assistantIndex].clarify = payload
            case .error(let code, let message):
                // 错误不替换正文，仅在气泡末尾挂一条提示
                let notice = "[\(code)] \(message)"
                if let prev = messages[assistantIndex].errorNotice {
                    messages[assistantIndex].errorNotice = prev + "\n" + notice
                } else {
                    messages[assistantIndex].errorNotice = notice
                }
            case .done:
                messages[assistantIndex].isStreaming = false
            }
            _ = assistant   // 抑制未使用警告
        }
    }

    /// 新建会话：清空消息 + sessionID。
    public func resetSession() {
        messages.removeAll()
        sessionID = nil
    }
}
