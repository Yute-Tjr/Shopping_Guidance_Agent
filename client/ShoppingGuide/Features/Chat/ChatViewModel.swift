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
    /// Phase 5：用户选择/拍下的图片（上传 + 渲染气泡缩略图）。已发送后置 nil。
    @Published public var pickedImage: PickedImage? = nil
    /// Phase 5：上传 / 多模态错误的提示文案（如 vision API 限流降级时）。
    @Published public var uploadNotice: String? = nil

    private let transport: ChatTransport
    /// Phase 5：注入 UploadService 启用图片上传链路；nil 时纯文本模式（兼容测试 / pre-Phase5 场景）。
    private let uploadService: UploadService?

    /// 流式 token 节流缓冲：后端 SSE 每秒可能推 30+ chunk，每个 chunk 直接写 messages.text
    /// 会触发 ScrollView ViewSizeCache miss → 全量 sizeThatFits 重测，主线程 100% CPU。
    /// 累积到 buffer，100ms 一次性 flush，把 publish 频率降到 ~10Hz，肉眼仍是流式。
    private var pendingTokenBuffer: String = ""
    private var pendingFlushTask: Task<Void, Never>? = nil
    private static let tokenFlushIntervalNs: UInt64 = 100_000_000  // 100ms

    public init(
        transport: ChatTransport,
        initialSessionID: String? = nil,
        uploadService: UploadService? = nil
    ) {
        self.transport = transport
        self.sessionID = initialSessionID
        self.uploadService = uploadService
    }

    /// 发送当前 inputText（+ 可选 pickedImage）。空白且无图直接忽略。
    public func send() async {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        let picked = pickedImage
        guard !text.isEmpty || picked != nil else { return }
        guard !isSending else { return }

        // 用户气泡：保留本地缩略图 URL，气泡渲染时直接显示
        let userMsg = ChatMessage(
            role: .user,
            text: text,
            localImageURL: picked?.localURL,
        )
        messages.append(userMsg)
        var assistant = ChatMessage(role: .assistant, isStreaming: true)
        messages.append(assistant)
        let assistantIndex = messages.count - 1

        inputText = ""
        pickedImage = nil
        isSending = true
        uploadNotice = nil
        defer {
            // 兜底：循环里没拿到 .done 时也要清流式状态
            if assistantIndex < messages.count {
                messages[assistantIndex].isStreaming = false
            }
            isSending = false
        }

        // Phase 5：先上传图（如有），换 image_id；失败按降级策略分支处理
        var imageID: String? = nil
        if let picked, let upload = uploadService {
            do {
                imageID = try await upload.upload(image: picked.data)
            } catch let UploadError.degraded(message) {
                // 503：vision API 繁忙——保留用户消息（含缩略图），但下面继续发纯文本流
                uploadNotice = "\(message)（已按文字继续）"
            } catch {
                // 其它错误：把错误挂到 assistant 气泡，停止本轮
                messages[assistantIndex].errorNotice = "图片上传失败：\(error.localizedDescription)"
                messages[assistantIndex].isStreaming = false
                return
            }
        }

        // 流式：带或不带 imageID
        let messageToSend = text.isEmpty ? "看看这张图" : text
        for await event in transport.stream(
            message: messageToSend, sessionID: sessionID, imageID: imageID,
        ) {
            switch event {
            case .session(let id):
                sessionID = id
            case .status:
                break
            case .token(let chunk):
                pendingTokenBuffer += chunk
                scheduleTokenFlush(into: assistantIndex)
            case .productCard(let card):
                // 商品卡件数变化也是高成本 layout，先 flush pending text 一起 publish
                flushPendingTokens(into: assistantIndex)
                messages[assistantIndex].productCards.append(card)
            case .clarify(let payload):
                flushPendingTokens(into: assistantIndex)
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
                // 流式结束：必须把残余 buffer flush 完再翻 isStreaming，否则末尾几个 token 会丢
                flushPendingTokens(into: assistantIndex)
                messages[assistantIndex].isStreaming = false
            }
            _ = assistant   // 抑制未使用警告
        }
        // 兜底：upstream 流意外中断（未发 .done）时也把 buffer 排空
        flushPendingTokens(into: assistantIndex)
    }

    /// 把累积的 token buffer 一次性追到 messages[idx].text，并清空。
    /// 同时取消任何 pending 的延迟 flush —— 同步 flush 后没必要再异步触发一次。
    private func flushPendingTokens(into idx: Int) {
        pendingFlushTask?.cancel()
        pendingFlushTask = nil
        guard !pendingTokenBuffer.isEmpty, idx < messages.count else {
            pendingTokenBuffer.removeAll(keepingCapacity: true)
            return
        }
        messages[idx].text += pendingTokenBuffer
        pendingTokenBuffer.removeAll(keepingCapacity: true)
    }

    /// 调度一次延迟 flush。100ms 内多个 token 落到同一个 buffer，到点合并 publish 一次。
    /// 已有 pending task 时不重复调度——让它正常到点，下次 token 会续到 buffer。
    private func scheduleTokenFlush(into idx: Int) {
        if pendingFlushTask != nil { return }
        pendingFlushTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: Self.tokenFlushIntervalNs)
            guard let self, !Task.isCancelled else { return }
            self.pendingFlushTask = nil
            self.flushPendingTokens(into: idx)
        }
    }

    /// 新建会话：清空消息 + sessionID + 取消未发送的图。
    public func resetSession() {
        messages.removeAll()
        sessionID = nil
        pickedImage = nil
        uploadNotice = nil
    }
}
