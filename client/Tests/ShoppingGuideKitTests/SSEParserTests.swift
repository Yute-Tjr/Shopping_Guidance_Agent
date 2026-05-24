import Foundation
import Testing
@testable import ShoppingGuideKit

private func block(_ s: String) -> Data { Data(s.utf8) }

@Suite("SSEParser")
struct SSEParserTests {

    @Test func parsesTokenEvent() {
        let evt = SSEParser.parse(block("event: token\ndata: {\"text\":\"你好\"}"))
        guard case let .token(text) = evt else { Issue.record("not token: \(String(describing: evt))"); return }
        #expect(text == "你好")
    }

    @Test func parsesSessionEvent() {
        let evt = SSEParser.parse(block("event: session\ndata: {\"session_id\":\"abc123\"}"))
        guard case let .session(id) = evt else { Issue.record("not session"); return }
        #expect(id == "abc123")
    }

    @Test func parsesStatusEvent() {
        let evt = SSEParser.parse(block("event: status\ndata: {\"stage\":\"retrieving\"}"))
        guard case let .status(stage) = evt else { Issue.record("not status"); return }
        #expect(stage == "retrieving")
    }

    @Test func parsesProductCard() {
        let raw = """
        event: product_card
        data: {"product_id":"p_x","title":"测试洗面奶","brand":"兰蔻","category":"美妆",\
        "image_url":"http://localhost/x.jpg","price_range":{"min":79.0,"max":129.0},\
        "skus":[{"sku_id":"s_1","properties":{"容量":"50ml"},"price":99.0}],\
        "reason":"温和控油"}
        """
        let evt = SSEParser.parse(block(raw))
        guard case let .productCard(card) = evt else { Issue.record("not productCard"); return }
        #expect(card.productId == "p_x")
        #expect(card.title == "测试洗面奶")
        #expect(card.priceRange.min == 79.0)
        #expect(card.skus.first?.skuId == "s_1")
        #expect(card.reason == "温和控油")
    }

    @Test func parsesClarify() {
        let evt = SSEParser.parse(block(
            "event: clarify\ndata: {\"question\":\"请补充\",\"options\":[\"A\",\"B\"]}"
        ))
        guard case let .clarify(payload) = evt else { Issue.record("not clarify"); return }
        #expect(payload.question == "请补充")
        #expect(payload.options == ["A", "B"])
    }

    @Test func parsesError() {
        let evt = SSEParser.parse(block(
            "event: error\ndata: {\"code\":\"LLM_TIMEOUT\",\"message\":\"超时了\"}"
        ))
        guard case let .error(code, message) = evt else { Issue.record("not error"); return }
        #expect(code == "LLM_TIMEOUT")
        #expect(message == "超时了")
    }

    @Test func parsesDone() {
        let evt = SSEParser.parse(block("event: done\ndata: {\"finish_reason\":\"stop\"}"))
        guard case .done = evt else { Issue.record("not done"); return }
    }

    @Test func ignoresHeartbeatComment() {
        // SSE 心跳是注释行 ":keepalive"，没有 event/data 应当解析成 nil
        #expect(SSEParser.parse(block(": keepalive")) == nil)
    }

    @Test func unknownEventReturnsNil() {
        #expect(SSEParser.parse(block("event: weird\ndata: {}")) == nil)
    }

    @Test func malformedJSONReturnsNil() {
        #expect(SSEParser.parse(block("event: token\ndata: {bad json")) == nil)
    }

    @Test func parsesCRLFFramedEvent() {
        // sse-starlette 实际用 \r\n 作行尾；parser 必须把每行末尾的 \r 去干净
        let evt = SSEParser.parse(block("event: token\r\ndata: {\"text\":\"crlf\"}"))
        guard case let .token(text) = evt else { Issue.record("not token"); return }
        #expect(text == "crlf")
    }

    @Test func eventNameAndDataInAnyOrder() {
        // 实际服务端 data 先 event 后也合法
        let evt = SSEParser.parse(block("data: {\"text\":\"hi\"}\nevent: token"))
        guard case let .token(text) = evt else { Issue.record("not token"); return }
        #expect(text == "hi")
    }
}
