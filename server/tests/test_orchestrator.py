"""AgentOrchestrator 集成单测：用 fake retriever / llm / product_repo 注入。

不打真实网络/数据库；验证：
1. recommend 主流程产生正确 SSE 事件序列
2. clarify_needed 直接 emit clarify + done
3. cart_op 在 Phase 2 给固定占位话术 + done（不真执行）
4. 检索空 → 提示"未找到匹配商品"，不产卡片
5. LLM 异常 → emit error + 兜底 Top-3 卡片 + done
6. 卡片字段从 MySQL 仓库 hydrate，绝不来自 LLM
"""
from __future__ import annotations

from typing import AsyncIterator

import pytest

from app.agent.orchestrator import AgentOrchestrator
from app.agent.memory import ConversationMemory
from app.rag.retriever import RetrievedProduct
from app.schemas.chat import ChatRequest


def _product(pid: str, title: str = "示例商品") -> RetrievedProduct:
    return RetrievedProduct(
        product_id=pid,
        score=0.9,
        brand="Test",
        category="美妆",
        sub_category="洁面",
        base_price=99.0,
        min_sku_price=79.0,
        max_sku_price=129.0,
        best_chunk_text=title,
        best_chunk_type="title",
        supporting_chunks=[title],
    )


class _FakeRetriever:
    def __init__(self, products: list[RetrievedProduct]) -> None:
        self._products = products
        self.calls: list[str] = []

    def search(self, query: str, **_kw):
        self.calls.append(query)
        return list(self._products)


class _FakeProductRepo:
    """伪 MySQL 商品仓库。"""

    def __init__(self) -> None:
        self.lookups: list[str] = []

    async def get_card_view(self, product_id: str):
        self.lookups.append(product_id)
        return {
            "product_id": product_id,
            "title": f"标题-{product_id}",
            "brand": "兰蔻",
            "category": "美妆",
            "image_url": f"http://localhost:8000/static/{product_id}_live.jpg",
            "price_range": {"min": 79.0, "max": 129.0},
            "skus": [{"sku_id": f"s_{product_id}_1", "properties": {"容量": "50ml"}, "price": 99.0}],
        }


class _FakeLLM:
    def __init__(self, tokens: list[str], raise_exc: Exception | None = None) -> None:
        self._tokens = tokens
        self._raise = raise_exc

    async def chat_stream(self, messages, **_kw) -> AsyncIterator[str]:
        if self._raise is not None:
            raise self._raise
        for t in self._tokens:
            yield t


@pytest.mark.asyncio
async def test_recommend_happy_path_emits_expected_events():
    retr = _FakeRetriever([_product("p_a"), _product("p_b")])
    repo = _FakeProductRepo()
    llm_tokens = [
        "为你推荐这款。\n",
        "```product_cards\n",
        '[{"product_id":"p_a","reason":"控油温和"}]\n',
        "```",
    ]
    orch = AgentOrchestrator(
        retriever=retr,
        llm=_FakeLLM(llm_tokens),
        product_repo=repo,
        memory=ConversationMemory(),
    )

    events: list[dict] = []
    async for e in orch.orchestrate(ChatRequest(session_id=None, message="推荐一款洗面奶")):
        events.append(e)

    kinds = [e["event"] for e in events]
    assert kinds[0] == "session"
    assert "token" in kinds
    assert "product_card" in kinds
    assert kinds[-1] == "done"

    # token 事件文本里不能漏出围栏内容
    token_payloads = [e["data"]["text"] for e in events if e["event"] == "token"]
    joined = "".join(token_payloads)
    assert "product_cards" not in joined
    assert "p_a" not in joined

    # product_card payload 必须是从 repo hydrate 出来的完整字段
    card_evt = next(e for e in events if e["event"] == "product_card")
    card = card_evt["data"]
    assert card["product_id"] == "p_a"
    assert card["title"] == "标题-p_a"
    assert card["image_url"].startswith("http")
    assert card["price_range"]["min"] == 79.0
    assert card["reason"] == "控油温和"
    assert repo.lookups == ["p_a"]


@pytest.mark.asyncio
async def test_clarify_intent_short_circuits():
    orch = AgentOrchestrator(
        retriever=_FakeRetriever([_product("p_a")]),
        llm=_FakeLLM(["不应该被调用"]),
        product_repo=_FakeProductRepo(),
        memory=ConversationMemory(),
    )
    events = [e async for e in orch.orchestrate(ChatRequest(session_id=None, message="手机"))]
    kinds = [e["event"] for e in events]
    assert "clarify" in kinds
    assert kinds[-1] == "done"
    # 澄清路径不应调 LLM 或 retriever
    assert "token" not in kinds
    assert "product_card" not in kinds


@pytest.mark.asyncio
async def test_cart_op_returns_placeholder_in_phase2():
    orch = AgentOrchestrator(
        retriever=_FakeRetriever([]),
        llm=_FakeLLM([]),
        product_repo=_FakeProductRepo(),
        memory=ConversationMemory(),
    )
    events = [e async for e in orch.orchestrate(ChatRequest(session_id=None, message="把这个加入购物车"))]
    kinds = [e["event"] for e in events]
    assert "token" in kinds   # 给一段占位话术
    assert "product_card" not in kinds
    assert kinds[-1] == "done"


@pytest.mark.asyncio
async def test_no_retrieval_no_card_but_token_still_flows():
    """检索为空时：仍允许 LLM 出一句"未找到"，不能 hydrate 卡片。"""
    llm_tokens = ["抱歉，库内暂未找到匹配的商品。"]
    orch = AgentOrchestrator(
        retriever=_FakeRetriever([]),
        llm=_FakeLLM(llm_tokens),
        product_repo=_FakeProductRepo(),
        memory=ConversationMemory(),
    )
    events = [e async for e in orch.orchestrate(ChatRequest(session_id=None, message="推荐宇宙飞船"))]
    kinds = [e["event"] for e in events]
    assert "product_card" not in kinds
    assert kinds[-1] == "done"
    token_text = "".join(e["data"]["text"] for e in events if e["event"] == "token")
    assert "未找到" in token_text or "抱歉" in token_text


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_top_products():
    retr = _FakeRetriever([_product("p_a"), _product("p_b"), _product("p_c")])
    repo = _FakeProductRepo()
    orch = AgentOrchestrator(
        retriever=retr,
        llm=_FakeLLM([], raise_exc=TimeoutError("LLM gone")),
        product_repo=repo,
        memory=ConversationMemory(),
    )
    events = [e async for e in orch.orchestrate(ChatRequest(session_id=None, message="推荐洗面奶"))]
    kinds = [e["event"] for e in events]
    # LLM 失败也要保证用户看到检索 Top-3 卡片 + 兜底话术 + done
    assert "error" in kinds
    assert kinds.count("product_card") >= 1
    assert kinds[-1] == "done"
    card_pids = [e["data"]["product_id"] for e in events if e["event"] == "product_card"]
    # 兜底卡片必须来自 retriever 结果（防幻觉）
    for pid in card_pids:
        assert pid in {"p_a", "p_b", "p_c"}


class _FakeSummarizer:
    """fake memory_summarizer：记录调用，返回固定 summary。"""
    def __init__(self, summary: str = "[摘要] 用户油皮预算 100 内"):
        self._summary = summary
        self.calls = 0
        self.last_older = None

    async def summarize(self, *, previous_summary, older_history):
        self.calls += 1
        self.last_older = list(older_history)
        return self._summary


@pytest.mark.asyncio
async def test_memory_summarize_triggered_on_long_history():
    """Phase 4-4：超过 summary_after_turns 时入口 await summarizer，history 被压缩。"""
    from app.agent.memory import ConversationMemory

    # 阈值降到 2 轮（4 条 message）方便构造
    mem = ConversationMemory(summary_after_turns=2, keep_recent_turns=1)
    sid = mem.get_or_create(None).id
    # 预先塞满 history（3 轮）让 needs_summary=True
    for i in range(3):
        mem.save_turn(sid, f"u{i}", f"a{i}", [])

    retr = _FakeRetriever([_product("p_a")])
    summarizer = _FakeSummarizer()
    orch = AgentOrchestrator(
        retriever=retr,
        llm=_FakeLLM(["回复"]),
        product_repo=_FakeProductRepo(),
        memory=mem,
        memory_summarizer=summarizer,
    )

    events = [
        e async for e in orch.orchestrate(
            ChatRequest(session_id=sid, message="推荐一款适合油皮的洗面奶")
        )
    ]
    # summarizer 应被调用恰好 1 次（本轮入口）
    assert summarizer.calls == 1
    # 摘要后 session 应带上 summary
    s = mem.get_or_create(sid)
    assert s.summary
    assert "油皮" in s.summary
    # history 应被截断到 keep_recent_turns=1 轮 + 本轮新增 = 4 条
    assert len(s.history) == 4
    # 验证流程正常跑完
    assert events[-1]["event"] == "done"


@pytest.mark.asyncio
async def test_memory_summarize_skipped_when_under_threshold():
    """history 没到阈值时不应调 summarizer。"""
    from app.agent.memory import ConversationMemory

    mem = ConversationMemory(summary_after_turns=6, keep_recent_turns=3)
    sid = mem.get_or_create(None).id
    mem.save_turn(sid, "u1", "a1", [])  # 只 1 轮

    summarizer = _FakeSummarizer()
    orch = AgentOrchestrator(
        retriever=_FakeRetriever([_product("p_a")]),
        llm=_FakeLLM(["回复"]),
        product_repo=_FakeProductRepo(),
        memory=mem,
        memory_summarizer=summarizer,
    )
    [_ async for _ in orch.orchestrate(ChatRequest(session_id=sid, message="再推荐"))]
    assert summarizer.calls == 0


@pytest.mark.asyncio
async def test_recommend_with_clarify_detector_short_circuits_chips():
    """Phase 4-3：「推荐一款手机」走 recommend 意图但信息不足，应短路 emit clarify。"""
    from app.agent.clarify_detector import build_clarify_detector

    retr = _FakeRetriever([_product("p_a")])
    repo = _FakeProductRepo()
    orch = AgentOrchestrator(
        retriever=retr,
        llm=_FakeLLM(["不该被调用"]),
        product_repo=repo,
        memory=ConversationMemory(),
        clarify_detector=build_clarify_detector(),
    )

    events = [
        e async for e in orch.orchestrate(
            ChatRequest(session_id=None, message="推荐一款手机")
        )
    ]
    kinds = [e["event"] for e in events]
    assert "clarify" in kinds, f"应触发 clarify，实际事件流: {kinds}"
    assert "token" not in kinds, "clarify 短路时不应进 LLM 流"
    assert "product_card" not in kinds
    assert kinds[-1] == "done"
    # 不应调到 retriever
    assert retr.calls == []
    # clarify payload 必须给出 question + options
    clarify = next(e for e in events if e["event"] == "clarify")
    assert clarify["data"]["question"]
    assert isinstance(clarify["data"]["options"], list)
    assert len(clarify["data"]["options"]) >= 3


class _FakeCompareExtractor:
    """Fake CompareTargetExtractor，记录调用次数 + 返回固定 targets。"""

    def __init__(self, targets):
        from app.agent.compare_planner import ComparePlan
        self._plan = ComparePlan(targets=list(targets), raw_segments=list(targets))
        self.calls = 0
        self.last_history: list[dict] | None = None
        self.last_summary: str | None = None

    async def plan(self, message: str, *, history=None, summary=None):
        self.calls += 1
        self.last_history = history
        self.last_summary = summary
        return self._plan


@pytest.mark.asyncio
async def test_compare_intent_runs_per_target_retrieval():
    """compare 意图：拆出 N 个 target，每个 target 各 retrieve 一次，合并去重塞 prompt。"""
    # 三件商品，模拟两边对比能各拉到 1 件
    retr = _FakeRetriever([_product("p_a"), _product("p_b"), _product("p_c")])
    repo = _FakeProductRepo()
    extractor = _FakeCompareExtractor(targets=["兰蔻 精华 保湿", "雅诗兰黛 精华 保湿"])
    llm_tokens = [
        "| 对比维度 | p_a | p_b |\n",
        "| --- | --- | --- |\n",
        "| 价格 | ￥79-129 | ￥79-129 |\n\n",
        "p_a 更适合敏感肌。\n",
        "```product_cards\n",
        '[{"product_id":"p_a","reason":"控油温和"},'
        '{"product_id":"p_b","reason":"长效保湿"}]\n',
        "```",
    ]
    orch = AgentOrchestrator(
        retriever=retr,
        llm=_FakeLLM(llm_tokens),
        product_repo=repo,
        memory=ConversationMemory(),
        compare_extractor=extractor,
    )
    events = [
        e async for e in orch.orchestrate(
            ChatRequest(session_id=None, message="对比一下兰蔻和雅诗兰黛的精华哪个更保湿")
        )
    ]
    kinds = [e["event"] for e in events]
    # extractor 必须被调用过一次（拆 targets）
    assert extractor.calls == 1
    # retriever 至少被调 2 次（每个 target 一次）
    assert len(retr.calls) >= 2
    assert "兰蔻 精华 保湿" in retr.calls and "雅诗兰黛 精华 保湿" in retr.calls
    # 输出至少含 product_card 与 done
    assert "product_card" in kinds
    assert kinds[-1] == "done"
    # token 流里能看到 markdown 表格头
    joined = "".join(e["data"]["text"] for e in events if e["event"] == "token")
    assert "对比维度" in joined


@pytest.mark.asyncio
async def test_llm_hallucinated_product_id_dropped():
    """LLM 卡片输出了不在检索结果里的 product_id，必须被丢弃。"""
    retr = _FakeRetriever([_product("p_a")])
    repo = _FakeProductRepo()
    llm_tokens = [
        "推荐：\n```product_cards\n",
        '[{"product_id":"p_fake_999","reason":"幻觉"},'
        '{"product_id":"p_a","reason":"正解"}]\n```',
    ]
    orch = AgentOrchestrator(
        retriever=retr,
        llm=_FakeLLM(llm_tokens),
        product_repo=repo,
        memory=ConversationMemory(),
    )
    events = [e async for e in orch.orchestrate(ChatRequest(session_id=None, message="推荐一款洗面奶"))]
    card_pids = [e["data"]["product_id"] for e in events if e["event"] == "product_card"]
    assert card_pids == ["p_a"]
    # 仓库也不应被请求 p_fake_999
    assert "p_fake_999" not in repo.lookups


@pytest.mark.asyncio
async def test_orchestrator_routes_to_multimodal_when_image_id_present(monkeypatch):
    """image_id 非空 → 走 MultimodalBranch；image_id 为空 → 走原 recommend 分支。"""
    from app.agent.multimodal_branch import MultimodalBranch, MultimodalResult
    from app.agent.query_rewriter import ParsedQuery
    from app.rag.retriever import RetrievedProduct
    from app.schemas.chat import ChatRequest

    called: dict = {"mm": 0, "rec": 0}

    async def fake_handle(self, *, message, image_id, history, summary):
        called["mm"] += 1
        return MultimodalResult(
            query_vector=[0.1] * 4,
            retrieved=[RetrievedProduct(
                product_id="p_x", score=0.9, brand="X", category="美妆",
                sub_category="精华", base_price=100.0,
                best_chunk_text="", supporting_chunks=[], title="X 商品",
            )],
            parsed=ParsedQuery(search_query=message),
            image_lost=False,
        )

    monkeypatch.setattr(MultimodalBranch, "handle", fake_handle)

    orch = _make_orchestrator_with_multimodal()

    # image_id 非空 → mm 分支
    req = ChatRequest(session_id=None, message="找同款", image_id="img-abc")
    events = [e async for e in orch.orchestrate(req)]
    assert called["mm"] == 1
    assert any(e["event"] == "product_card" for e in events) or any(e["event"] == "token" for e in events)

    # image_id 为空 → 不走 mm 分支
    called["mm"] = 0
    req2 = ChatRequest(session_id=None, message="推荐手机", image_id=None)
    events2 = [e async for e in orch.orchestrate(req2)]
    assert called["mm"] == 0


def _make_orchestrator_with_multimodal():
    """构造一个挂载真实 MultimodalBranch 类实例（handle 由 monkeypatch 替换）+ stub 的 orchestrator。

    注意：必须用真实的 MultimodalBranch 实例，因为测试是通过
    `monkeypatch.setattr(MultimodalBranch, "handle", ...)` 替换实现的——
    用 stub 类型会绕过这次替换。
    """
    from app.agent.memory import ConversationMemory
    from app.agent.multimodal_branch import MultimodalBranch
    from app.agent.orchestrator import AgentOrchestrator
    from app.rag.retriever import RetrievedProduct

    class _StubLLM:
        async def chat_stream(self, messages, **kw):
            yield "推荐 p_x 一款。\n```product_cards\n[{\"product_id\":\"p_x\",\"reason\":\"匹配\"}]\n```"

    class _StubRetriever:
        def search(self, q, **kw):
            return [RetrievedProduct(
                product_id="p_x", score=0.9, brand="X", category="美妆",
                sub_category="精华", base_price=100.0,
                best_chunk_text="", supporting_chunks=[], title="X 商品",
            )]

    class _StubProductRepo:
        async def get_card_view(self, pid):
            return {
                "product_id": pid, "title": "X 商品", "brand": "X",
                "category": "美妆", "image_url": "/static/x.jpg",
                "price_range": {"min": 100, "max": 200}, "skus": [],
            }

    # 真实 MultimodalBranch 实例，深底 stub 全部传 None / 简单 fake
    mm_branch = MultimodalBranch(
        embedder=None,   # handle 已被替换不会触达
        retriever=None,
        cache=None,
        query_rewriter=None,
        structured_retriever=None,
    )

    return AgentOrchestrator(
        retriever=_StubRetriever(),
        llm=_StubLLM(),
        product_repo=_StubProductRepo(),
        memory=ConversationMemory(),
        multimodal_branch=mm_branch,
    )
