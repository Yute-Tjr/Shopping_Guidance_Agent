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


class _FakeCompareExtractor:
    """Fake CompareTargetExtractor，记录调用次数 + 返回固定 targets。"""

    def __init__(self, targets):
        from app.agent.compare_planner import ComparePlan
        self._plan = ComparePlan(targets=list(targets), raw_segments=list(targets))
        self.calls = 0

    async def plan(self, message: str):
        self.calls += 1
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
