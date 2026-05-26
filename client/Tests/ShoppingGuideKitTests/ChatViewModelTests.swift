import Foundation
import Testing
@testable import ShoppingGuideKit

/// 注入 fake stream 工厂，避免打真实网络。
struct FakeChatTransport: ChatTransport {
    let events: [SSEEvent]

    func stream(message: String, sessionID: String?) -> AsyncStream<SSEEvent> {
        let captured = events
        return AsyncStream { continuation in
            for e in captured { continuation.yield(e) }
            continuation.finish()
        }
    }
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
}
