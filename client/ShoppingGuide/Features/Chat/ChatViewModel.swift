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
    /// Phase 5C：语音输入是否正在录音识别。
    @Published public var isListening: Bool = false
    /// Phase 5C：语音权限 / 识别错误提示。
    @Published public var voiceNotice: String? = nil
    /// Phase 5C：TTS 合成/播放准备态提示。
    @Published public var speechNotice: String? = nil
    /// Phase 5C：是否在 assistant 回复结束后自动播报。
    @Published public var autoSpeakEnabled: Bool = false
    /// Phase 5C：TTS 播报音色。
    @Published public var selectedVoice: SpeechVoice = .default

    private let transport: ChatTransport
    /// Phase 5：注入 UploadService 启用图片上传链路；nil 时纯文本模式（兼容测试 / pre-Phase5 场景）。
    private let uploadService: UploadService?
    private let speechRecognizer: SpeechRecognizing?
    private let speechSpeaker: SpeechSpeaking?

    /// 流式 token 节流缓冲：后端 SSE 每秒可能推 30+ chunk，每个 chunk 直接写 messages.text
    /// 会触发 ScrollView ViewSizeCache miss → 全量 sizeThatFits 重测，主线程 100% CPU。
    /// 累积到 buffer，100ms 一次性 flush，把 publish 频率降到 ~10Hz，肉眼仍是流式。
    private var pendingTokenBuffer: String = ""
    private var pendingFlushTask: Task<Void, Never>? = nil
    private var speechNoticeClearTask: Task<Void, Never>? = nil
    private static let tokenFlushIntervalNs: UInt64 = 100_000_000  // 100ms
    private var activeSendTask: Task<Void, Never>?
    private var activeTurnID = UUID()
    private var activeVoiceTurnID = UUID()

    public init(
        transport: ChatTransport,
        initialSessionID: String? = nil,
        uploadService: UploadService? = nil,
        speechRecognizer: SpeechRecognizing? = nil,
        speechSpeaker: SpeechSpeaking? = nil
    ) {
        self.transport = transport
        self.sessionID = initialSessionID
        self.uploadService = uploadService
        self.speechRecognizer = speechRecognizer
        self.speechSpeaker = speechSpeaker
    }

    /// 发送当前 inputText（+ 可选 pickedImage）。clarify chip 可传入 explicitText，避免经由共享输入框状态中转。
    public func send(text explicitText: String? = nil) async {
        let text = (explicitText ?? inputText).trimmingCharacters(in: .whitespacesAndNewlines)
        let picked = pickedImage
        guard !text.isEmpty || picked != nil else { return }
        guard !isSending else { return }
        if isListening {
            cancelVoiceInput()
        }
        let turnID = UUID()
        activeTurnID = turnID

        // 用户气泡：保留本地缩略图 URL，气泡渲染时直接显示
        let userMsg = ChatMessage(
            role: .user,
            text: text,
            localImageURL: picked?.localURL,
        )
        messages.append(userMsg)
        let assistant = ChatMessage(role: .assistant, isStreaming: true)
        messages.append(assistant)
        let assistantIndex = messages.count - 1

        inputText = ""
        pickedImage = nil
        isSending = true
        uploadNotice = nil

        await runSend(
            text: text,
            picked: picked,
            assistantIndex: assistantIndex,
            turnID: turnID
        )
    }

    private func runSend(
        text: String,
        picked: PickedImage?,
        assistantIndex: Int,
        turnID: UUID
    ) async {
        defer {
            // 兜底：循环里没拿到 .done 时也要清流式状态；旧轮次已被 reset 失效时不再写 UI。
            if isActiveTurn(turnID) {
                if assistantIndex < messages.count {
                    messages[assistantIndex].isStreaming = false
                }
                isSending = false
                activeSendTask = nil
            }
        }

        // Phase 5：先上传图（如有），换 image_id；失败按降级策略分支处理
        var imageID: String? = nil
        if let picked, let upload = uploadService {
            do {
                imageID = try await upload.upload(image: picked.data)
                guard isActiveTurn(turnID) else { return }
            } catch let UploadError.degraded(message) {
                guard isActiveTurn(turnID) else { return }
                // 503：vision API 繁忙——保留用户消息（含缩略图），但下面继续发纯文本流
                uploadNotice = "\(message)（已按文字继续）"
            } catch {
                guard isActiveTurn(turnID), assistantIndex < messages.count else { return }
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
            guard isActiveTurn(turnID), assistantIndex < messages.count else { break }
            switch event {
            case .session(let id):
                sessionID = id
            case .status:
                break
            case .token(let chunk):
                pendingTokenBuffer += chunk
                scheduleTokenFlush(into: assistantIndex, turnID: turnID)
            case .productCard(let card):
                // 商品卡件数变化也是高成本 layout，先 flush pending text 一起 publish
                flushPendingTokens(into: assistantIndex, turnID: turnID)
                messages[assistantIndex].productCards.append(card)
            case .clarify(let payload):
                flushPendingTokens(into: assistantIndex, turnID: turnID)
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
                flushPendingTokens(into: assistantIndex, turnID: turnID)
                messages[assistantIndex].isStreaming = false
                if autoSpeakEnabled {
                    speakAssistantText(messages[assistantIndex].text)
                }
                return
            }
        }
        // 兜底：upstream 流意外中断（未发 .done）时也把 buffer 排空
        flushPendingTokens(into: assistantIndex, turnID: turnID)
    }

    /// 把累积的 token buffer 一次性追到 messages[idx].text，并清空。
    /// 同时取消任何 pending 的延迟 flush —— 同步 flush 后没必要再异步触发一次。
    private func flushPendingTokens(into idx: Int, turnID: UUID) {
        guard isActiveTurn(turnID) else { return }
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
    private func scheduleTokenFlush(into idx: Int, turnID: UUID) {
        guard isActiveTurn(turnID) else { return }
        if pendingFlushTask != nil { return }
        pendingFlushTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: Self.tokenFlushIntervalNs)
            guard let self, !Task.isCancelled else { return }
            self.pendingFlushTask = nil
            self.flushPendingTokens(into: idx, turnID: turnID)
        }
    }

    private func isActiveTurn(_ turnID: UUID) -> Bool {
        activeTurnID == turnID && !Task.isCancelled
    }

    /// Phase 5C：启动语音输入，partial transcript 实时写入 inputText。
    public func startVoiceInput() async {
        guard !isSending else { return }
        guard let speechRecognizer else {
            voiceNotice = "语音输入暂不可用"
            return
        }
        let voiceTurnID = UUID()
        activeVoiceTurnID = voiceTurnID
        voiceNotice = nil
        do {
            try await speechRecognizer.start { [weak self] transcript in
                guard let self, self.activeVoiceTurnID == voiceTurnID else { return }
                self.inputText = transcript
            } onCompletion: { [weak self] error in
                guard let self, self.activeVoiceTurnID == voiceTurnID else { return }
                self.isListening = false
                if let error, let notice = Self.voiceNotice(for: error) {
                    self.voiceNotice = notice
                }
            }
            guard activeVoiceTurnID == voiceTurnID else {
                speechRecognizer.stop()
                return
            }
            isListening = true
        } catch {
            guard activeVoiceTurnID == voiceTurnID else { return }
            isListening = false
            voiceNotice = Self.voiceNotice(for: error)
        }
    }

    public func stopVoiceInput() {
        speechRecognizer?.stop()
        isListening = false
    }

    public func speakAssistantText(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        guard let speechSpeaker else { return }
        speechNoticeClearTask?.cancel()
        if speechSpeaker.hasCachedAudio(for: trimmed, voice: selectedVoice) {
            speechNotice = nil
        } else {
            speechNotice = "正在准备朗读..."
            speechNoticeClearTask = Task { @MainActor [weak self] in
                try? await Task.sleep(nanoseconds: 8_000_000_000)
                guard let self, !Task.isCancelled else { return }
                self.speechNotice = nil
                self.speechNoticeClearTask = nil
            }
        }
        speechSpeaker.speak(trimmed, voice: selectedVoice)
    }

    public func stopSpeaking() {
        speechNoticeClearTask?.cancel()
        speechNoticeClearTask = nil
        speechNotice = nil
        speechSpeaker?.stop()
    }

    private static func voiceNotice(for error: Error) -> String? {
        if let speechError = error as? SpeechRecognitionError {
            if speechError.isEmptySpeech {
                return nil
            }
            return speechError.localizedDescription
        }
        return error.localizedDescription
    }

    /// 新建会话：停止当前流、清空消息 / 输入 / sessionID，并立即解锁输入栏。
    public func resetSession() {
        // 不直接 cancel activeSendTask：底层 AsyncStream/SSE 在取消后不一定立即结束，
        // 外层 await send() 可能被挂住。activeTurnID 失效足以让旧流事件不再写 UI。
        activeSendTask = nil
        activeTurnID = UUID()
        pendingFlushTask?.cancel()
        pendingFlushTask = nil
        speechNoticeClearTask?.cancel()
        speechNoticeClearTask = nil
        pendingTokenBuffer.removeAll(keepingCapacity: true)
        messages.removeAll()
        inputText = ""
        sessionID = nil
        isSending = false
        pickedImage = nil
        uploadNotice = nil
        cancelVoiceInput()
        stopSpeaking()
        voiceNotice = nil
        speechNotice = nil
    }

    private func cancelVoiceInput() {
        activeVoiceTurnID = UUID()
        speechRecognizer?.stop()
        isListening = false
    }
}

private extension SpeechRecognitionError {
    var isEmptySpeech: Bool {
        guard case .recognitionFailed(let message) = self else { return false }
        return message.contains("ASR 返回空文本") || message.contains("没有录到语音")
    }
}
