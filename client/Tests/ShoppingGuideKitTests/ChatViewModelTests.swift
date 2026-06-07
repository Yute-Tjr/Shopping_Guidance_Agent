import Foundation
import Testing
@testable import ShoppingGuideKit

/// 注入 fake stream 工厂，避免打真实网络。
struct FakeChatTransport: ChatTransport {
    let events: [SSEEvent]

    func stream(message: String, sessionID: String?, imageID: String?) -> AsyncStream<SSEEvent> {
        let captured = events
        return AsyncStream { continuation in
            for e in captured { continuation.yield(e) }
            continuation.finish()
        }
    }
}

final class ControlledChatTransport: ChatTransport, @unchecked Sendable {
    private var continuations: [AsyncStream<SSEEvent>.Continuation] = []
    private var streamCountContinuations: [(count: Int, continuation: CheckedContinuation<Void, Never>)] = []

    func stream(message: String, sessionID: String?, imageID: String?) -> AsyncStream<SSEEvent> {
        AsyncStream { continuation in
            continuations.append(continuation)
            let waiting = streamCountContinuations.filter { continuations.count >= $0.count }
            streamCountContinuations.removeAll { continuations.count >= $0.count }
            for waiter in waiting {
                waiter.continuation.resume()
            }
        }
    }

    func waitForStreamCount(_ count: Int) async {
        if continuations.count >= count { return }
        await withCheckedContinuation { continuation in
            streamCountContinuations.append((count, continuation))
        }
    }

    func yield(_ event: SSEEvent, to index: Int = 0) {
        continuations[index].yield(event)
    }

    func finish(_ index: Int = 0) {
        continuations[index].finish()
    }
}

final class RecordingChatTransport: ChatTransport, @unchecked Sendable {
    var messages: [String] = []
    let events: [SSEEvent]

    init(events: [SSEEvent] = [.done]) {
        self.events = events
    }

    func stream(message: String, sessionID: String?, imageID: String?) -> AsyncStream<SSEEvent> {
        messages.append(message)
        let captured = events
        return AsyncStream { continuation in
            for e in captured { continuation.yield(e) }
            continuation.finish()
        }
    }
}

@MainActor
final class FakeSpeechRecognizer: SpeechRecognizing, @unchecked Sendable {
    var startCount = 0
    var stopCount = 0
    var startError: Error?
    private var onPartialResult: (@MainActor @Sendable (String) -> Void)?
    private var onCompletion: (@MainActor @Sendable (SpeechRecognitionError?) -> Void)?

    func start(
        onPartialResult: @escaping @MainActor @Sendable (String) -> Void,
        onCompletion: @escaping @MainActor @Sendable (SpeechRecognitionError?) -> Void
    ) async throws {
        startCount += 1
        if let startError {
            throw startError
        }
        self.onPartialResult = onPartialResult
        self.onCompletion = onCompletion
    }

    func stop() {
        stopCount += 1
    }

    @MainActor
    func emit(_ text: String) {
        onPartialResult?(text)
    }

    @MainActor
    func complete(_ error: SpeechRecognitionError? = nil) {
        onCompletion?(error)
    }
}

@MainActor
final class FakeSpeechSpeaker: SpeechSpeaking, @unchecked Sendable {
    var spokenItems: [(text: String, voice: SpeechVoice)] = []
    var cachedItems: Set<SpeechCacheKey> = []
    var stopCount = 0

    func speak(_ text: String, voice: SpeechVoice) {
        spokenItems.append((text: text, voice: voice))
    }

    func hasCachedAudio(for text: String, voice: SpeechVoice) -> Bool {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return cachedItems.contains(SpeechCacheKey(text: trimmed, voiceID: voice.id))
    }

    func stop() {
        stopCount += 1
    }
}

struct SpeechCacheKey: Hashable {
    let text: String
    let voiceID: String
}

private func sampleCard(_ pid: String = "p_test") -> ProductCard {
    ProductCard(
        productId: pid,
        title: "测试洗面奶",
        brand: "兰蔻",
        category: "美妆",
        imageURL: URL(string: "http://localhost/x.jpg")!,
        priceRange: .init(min: 79.0, max: 129.0),
        skus: [],
        reason: "温和控油"
    )
}

@MainActor
@Suite("ChatViewModel")
struct ChatViewModelTests {

    @Test func sendHappyPathAccumulatesTokenAndAppendsCard() async {
        let transport = FakeChatTransport(events: [
            .session(id: "sess-1"),
            .status(stage: "retrieving"),
            .token(text: "为你"),
            .token(text: "推荐"),
            .productCard(sampleCard()),
            .done,
        ])
        let vm = ChatViewModel(transport: transport)
        vm.inputText = "推荐一款洗面奶"

        await vm.send()

        // 2 条 message：用户 + assistant
        #expect(vm.messages.count == 2)
        #expect(vm.messages[0].role == .user)
        #expect(vm.messages[0].text == "推荐一款洗面奶")
        let asst = vm.messages[1]
        #expect(asst.role == .assistant)
        #expect(asst.text == "为你推荐")
        #expect(asst.productCards.count == 1)
        #expect(asst.productCards[0].productId == "p_test")
        #expect(asst.isStreaming == false)
        #expect(vm.sessionID == "sess-1")
        #expect(vm.inputText == "")
        #expect(vm.isSending == false)
    }

    @Test func clarifyEventStoredOnAssistantMessage() async {
        let transport = FakeChatTransport(events: [
            .session(id: "s"),
            .clarify(ClarifyPayload(question: "请补充", options: ["A", "B"])),
            .done,
        ])
        let vm = ChatViewModel(transport: transport)
        vm.inputText = "手机"
        await vm.send()

        let asst = vm.messages.last
        #expect(asst?.clarify?.question == "请补充")
        #expect(asst?.clarify?.options == ["A", "B"])
    }

    @Test func errorEventSetsErrorNoticeButStillFinishes() async {
        let transport = FakeChatTransport(events: [
            .session(id: "s"),
            .token(text: "暂"),
            .error(code: "LLM_TIMEOUT", message: "超时"),
            .productCard(sampleCard("p_fallback")),
            .done,
        ])
        let vm = ChatViewModel(transport: transport)
        vm.inputText = "推荐一款洗面奶"
        await vm.send()

        let asst = vm.messages.last!
        #expect(asst.errorNotice?.contains("LLM_TIMEOUT") == true)
        #expect(asst.text == "暂")
        #expect(asst.productCards.first?.productId == "p_fallback")  // 兜底卡片仍要展示
        #expect(asst.isStreaming == false)
    }

    @Test func emptyInputDoesNothing() async {
        let transport = FakeChatTransport(events: [.done])
        let vm = ChatViewModel(transport: transport)
        vm.inputText = "   "
        await vm.send()
        #expect(vm.messages.isEmpty)
    }

    @Test func explicitClarifyTextBypassesInputDraft() async {
        let transport = RecordingChatTransport()
        let vm = ChatViewModel(transport: transport)
        vm.inputText = "缓震回弹"

        await vm.send(text: "日常慢跑")

        #expect(transport.messages == ["日常慢跑"])
        #expect(vm.messages.first?.text == "日常慢跑")
        #expect(vm.inputText == "")
    }

    @Test func statusEventsDontPolluteVisibleText() async {
        let transport = FakeChatTransport(events: [
            .session(id: "s"),
            .status(stage: "parsing"),
            .status(stage: "retrieving"),
            .status(stage: "generating"),
            .token(text: "好的"),
            .done,
        ])
        let vm = ChatViewModel(transport: transport)
        vm.inputText = "推荐一款洗面奶"
        await vm.send()
        #expect(vm.messages.last?.text == "好的")
    }

    @Test func resetSessionClearsMessagesAndSessionID() async {
        // 跑完一轮对话，sessionID + messages 都不为空
        let transport = FakeChatTransport(events: [
            .session(id: "sess-1"),
            .token(text: "hi"),
            .productCard(sampleCard()),
            .done,
        ])
        let vm = ChatViewModel(transport: transport)
        vm.inputText = "推荐一款洗面奶"
        await vm.send()
        #expect(vm.sessionID == "sess-1")
        #expect(vm.messages.count == 2)

        // 点"新建会话" → messages 清空 + sessionID 归零
        // ChatView 的 .onChange(of: messages.count) 监听到 count 从 >0 变 0
        // 会触发 ScrollViewReader 把空状态滚回顶部（这部分纯 UI 行为，
        // SwiftUI 视图层不在单测覆盖范围；此用例保证 ViewModel 状态正确清零）
        vm.resetSession()
        #expect(vm.messages.isEmpty)
        #expect(vm.sessionID == nil)
    }

    @Test func resetSessionDuringStreamingCancelsOldTurnAndUnlocksInput() async {
        let transport = ControlledChatTransport()
        let vm = ChatViewModel(transport: transport, initialSessionID: "sess-old")
        vm.inputText = "推荐蓝牙耳机"

        let firstSend = Task { await vm.send() }
        await Task.yield()
        await transport.waitForStreamCount(1)
        #expect(vm.isSending == true)

        vm.inputText = "未发送的新会话草稿"
        vm.resetSession()

        #expect(vm.messages.isEmpty)
        #expect(vm.sessionID == nil)
        #expect(vm.inputText == "")
        #expect(vm.isSending == false)

        transport.yield(.token(text: "旧回复不应出现"))
        transport.yield(.productCard(sampleCard("p_old")))
        transport.yield(.done)
        await firstSend.value

        #expect(vm.messages.isEmpty)
        #expect(vm.isSending == false)

        vm.inputText = "新会话可以发送"
        let secondSend = Task { await vm.send() }
        await Task.yield()
        await transport.waitForStreamCount(2)
        transport.yield(.token(text: "新回复"), to: 1)
        transport.yield(.done, to: 1)
        await secondSend.value

        #expect(vm.messages.count == 2)
        #expect(vm.messages[0].text == "新会话可以发送")
        #expect(vm.messages[1].text == "新回复")
        #expect(vm.messages[1].isStreaming == false)
    }

    @Test func sessionIDPersistsAcrossTurns() async {
        // 第一轮 session 返回 id
        let t1 = FakeChatTransport(events: [
            .session(id: "sess-keep"),
            .token(text: "1"),
            .done,
        ])
        let vm = ChatViewModel(transport: t1)
        vm.inputText = "推荐一款洗面奶"
        await vm.send()
        #expect(vm.sessionID == "sess-keep")

        // 第二轮 transport 不再下发 session，sessionID 应保留
        let t2 = FakeChatTransport(events: [
            .token(text: "2"),
            .done,
        ])
        let vm2 = ChatViewModel(transport: t2, initialSessionID: "sess-keep")
        vm2.inputText = "再来一条问题"
        await vm2.send()
        #expect(vm2.sessionID == "sess-keep")
    }

    @Test func voiceInputPartialTranscriptUpdatesInputText() async {
        let recognizer = FakeSpeechRecognizer()
        let vm = ChatViewModel(
            transport: FakeChatTransport(events: []),
            speechRecognizer: recognizer
        )

        await vm.startVoiceInput()
        recognizer.emit("推荐蓝牙耳机")

        #expect(vm.isListening == true)
        #expect(vm.inputText == "推荐蓝牙耳机")

        vm.stopVoiceInput()
        #expect(vm.isListening == false)
        #expect(recognizer.stopCount == 1)
    }

    @Test func voiceInputFailureSetsNoticeAndStopsListening() async {
        let recognizer = FakeSpeechRecognizer()
        recognizer.startError = SpeechRecognitionError.permissionDenied
        let vm = ChatViewModel(
            transport: FakeChatTransport(events: []),
            speechRecognizer: recognizer
        )

        await vm.startVoiceInput()

        #expect(vm.isListening == false)
        #expect(vm.voiceNotice?.contains("语音识别权限") == true)
    }

    @Test func manualStopAllowsRemoteAsrCompletionToUpdateInput() async {
        let recognizer = FakeSpeechRecognizer()
        let vm = ChatViewModel(
            transport: FakeChatTransport(events: []),
            speechRecognizer: recognizer
        )

        await vm.startVoiceInput()
        vm.stopVoiceInput()
        recognizer.emit("推荐蓝牙耳机")
        recognizer.complete()

        #expect(vm.isListening == false)
        #expect(vm.inputText == "推荐蓝牙耳机")
    }

    @Test func voiceInputCompletionStopsListening() async {
        let recognizer = FakeSpeechRecognizer()
        let vm = ChatViewModel(
            transport: FakeChatTransport(events: []),
            speechRecognizer: recognizer
        )

        await vm.startVoiceInput()
        recognizer.complete()

        #expect(vm.isListening == false)
        #expect(vm.voiceNotice == nil)
    }

    @Test func emptyAsrTextCompletionDoesNotShowNotice() async {
        let recognizer = FakeSpeechRecognizer()
        let vm = ChatViewModel(
            transport: FakeChatTransport(events: []),
            speechRecognizer: recognizer
        )

        await vm.startVoiceInput()
        recognizer.complete(.recognitionFailed(message: "ASR 返回空文本"))

        #expect(vm.isListening == false)
        #expect(vm.voiceNotice == nil)
        #expect(vm.inputText == "")
    }

    @Test func staleVoiceCompletionAfterResetDoesNotMutateNewSession() async {
        let recognizer = FakeSpeechRecognizer()
        let vm = ChatViewModel(
            transport: FakeChatTransport(events: []),
            speechRecognizer: recognizer
        )

        await vm.startVoiceInput()
        vm.resetSession()
        recognizer.complete(.recognitionFailed(message: "旧语音错误"))

        #expect(vm.isListening == false)
        #expect(vm.voiceNotice == nil)
        #expect(vm.inputText == "")
    }

    @Test func sendStopsActiveVoiceInputBeforeStreaming() async {
        let recognizer = FakeSpeechRecognizer()
        let vm = ChatViewModel(
            transport: FakeChatTransport(events: [.token(text: "好的"), .done]),
            speechRecognizer: recognizer
        )

        await vm.startVoiceInput()
        recognizer.emit("推荐手机")
        await vm.send()

        #expect(recognizer.stopCount == 1)
        #expect(vm.isListening == false)
        #expect(vm.messages.last?.text == "好的")
    }

    @Test func resetSessionStopsVoiceAndSpeechOutput() async {
        let recognizer = FakeSpeechRecognizer()
        let speaker = FakeSpeechSpeaker()
        let vm = ChatViewModel(
            transport: FakeChatTransport(events: []),
            speechRecognizer: recognizer,
            speechSpeaker: speaker
        )

        await vm.startVoiceInput()
        vm.speakAssistantText("正在播报的旧回复")
        vm.resetSession()

        #expect(recognizer.stopCount == 1)
        #expect(speaker.stopCount == 1)
        #expect(vm.isListening == false)
    }

    @Test func autoSpeakReadsAssistantReplyAfterDone() async {
        let speaker = FakeSpeechSpeaker()
        let vm = ChatViewModel(
            transport: FakeChatTransport(events: [.token(text: "为你推荐"), .done]),
            speechSpeaker: speaker
        )
        vm.autoSpeakEnabled = true
        vm.inputText = "推荐一款洗面奶"

        await vm.send()

        #expect(speaker.spokenItems.map(\.text) == ["为你推荐"])
        #expect(speaker.spokenItems.map(\.voice) == [.default])
    }

    @Test func ttsUsesSelectedVoice() async {
        let speaker = FakeSpeechSpeaker()
        let vm = ChatViewModel(
            transport: FakeChatTransport(events: []),
            speechSpeaker: speaker
        )
        vm.selectedVoice = SpeechVoice.all.first { $0.id == "saturn_zh_male_shuanglangshaonian_tob" }!

        vm.speakAssistantText("请听这段回复")

        #expect(speaker.spokenItems.map(\.text) == ["请听这段回复"])
        #expect(speaker.spokenItems.map(\.voice) == [vm.selectedVoice])
    }

    @Test func speakingShowsPreparingNoticeAndStopClearsIt() async {
        let speaker = FakeSpeechSpeaker()
        let vm = ChatViewModel(
            transport: FakeChatTransport(events: []),
            speechSpeaker: speaker
        )

        vm.speakAssistantText("请听这段回复")

        #expect(vm.speechNotice == "正在准备朗读...")
        #expect(speaker.spokenItems.map(\.text) == ["请听这段回复"])

        vm.stopSpeaking()

        #expect(vm.speechNotice == nil)
        #expect(speaker.stopCount == 1)
    }

    @Test func cachedSpeechSkipsPreparingNotice() async {
        let speaker = FakeSpeechSpeaker()
        let vm = ChatViewModel(
            transport: FakeChatTransport(events: []),
            speechSpeaker: speaker
        )
        speaker.cachedItems.insert(SpeechCacheKey(text: "请听这段回复", voiceID: vm.selectedVoice.id))

        vm.speakAssistantText("请听这段回复")

        #expect(vm.speechNotice == nil)
        #expect(speaker.spokenItems.map(\.text) == ["请听这段回复"])
    }
}
