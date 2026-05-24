"""Agent 主流程编排（docs/03 §4.2 落地）。

事件流（按 SSE event 名）：
    session → status(parsing) → [clarify | (status(retrieving) → status(generating) → token*+ → product_card*)] → done

错误降级：
- LLM 抛任何异常 → 不让用户看到 traceback，emit error code + Top-3 检索结果作为兜底卡片，然后 emit done；
- LLM 输出了不在检索集合里的 product_id → ProductCardExtractor 会过滤；
- Phase 2 cart_op 不真执行（业务闭环在 Phase 5A），固定话术让用户知道改成 5A 后会真加购。

每个 yield 的字典格式：{"event": <name>, "data": <dict>}。
SSE 序列化交给上层 API 层（api/chat.py 用 sse-starlette 把 data 转 JSON 字符串）。
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Protocol

from app.agent.card_extractor import ProductCardExtractor
from app.agent.intent import IntentRouter
from app.agent.memory import ConversationMemory
from app.agent.prompts import build_compare_messages, build_recommend_messages
from app.rag.retriever import RetrievedProduct
from app.schemas.chat import ChatRequest
from app.utils.logger import get_logger

logger = get_logger(__name__)


class _LLMLike(Protocol):
    def chat_stream(self, messages: list[dict], **kw) -> AsyncIterator[str]: ...


class _RetrieverLike(Protocol):
    def search(self, query: str, **kw) -> list[RetrievedProduct]: ...


class _ProductRepoLike(Protocol):
    async def get_card_view(self, product_id: str) -> dict | None: ...


# 兜底卡片最多推几张
_FALLBACK_CARDS = 3


class AgentOrchestrator:
    """串起 intent → retrieve → prompt → LLM → 卡片提取 / hydrate。"""

    def __init__(
        self,
        *,
        retriever: _RetrieverLike,
        llm: _LLMLike,
        product_repo: _ProductRepoLike,
        memory: ConversationMemory,
        intent_router: IntentRouter | None = None,
    ) -> None:
        self.retriever = retriever
        self.llm = llm
        self.product_repo = product_repo
        self.memory = memory
        self.intent_router = intent_router or IntentRouter()

    async def orchestrate(self, req: ChatRequest) -> AsyncIterator[dict]:
        session = self.memory.get_or_create(req.session_id)
        yield {"event": "session", "data": {"session_id": session.id}}

        # 1) 意图识别
        yield {"event": "status", "data": {"stage": "parsing"}}
        intent = self.intent_router.parse(req.message, history=session.history)

        if intent.intent == "clarify_needed":
            yield {"event": "clarify", "data": intent.clarify_payload or {"question": "请补充一下需求？", "options": []}}
            yield {"event": "done", "data": {"finish_reason": "stop"}}
            return

        if intent.intent == "cart_op":
            # Phase 2 不真实执行加购/下单，给固定占位话术，避免误导用户
            placeholder = "购物车和下单功能在 Phase 5A 上线，目前先帮你记下，可继续聊推荐。"
            yield {"event": "token", "data": {"text": placeholder}}
            self.memory.save_turn(session.id, req.message, placeholder, [])
            yield {"event": "done", "data": {"finish_reason": "stop"}}
            return

        # 2) 检索
        yield {"event": "status", "data": {"stage": "retrieving"}}
        retrieved = self.retriever.search(intent.search_query)

        # 3) 生成
        yield {"event": "status", "data": {"stage": "generating"}}
        if intent.intent == "compare":
            messages = build_compare_messages(
                user_message=req.message, retrieved=retrieved, history=session.history,
            )
        else:
            messages = build_recommend_messages(
                user_message=req.message, retrieved=retrieved, history=session.history,
            )

        allowed_ids = {p.product_id for p in retrieved}
        extractor = ProductCardExtractor(allowed_ids=allowed_ids)
        emitted_card_ids: list[str] = []
        full_visible = ""

        try:
            async for delta in self.llm.chat_stream(messages):
                visible, cards = extractor.feed(delta)
                if visible:
                    full_visible += visible
                    yield {"event": "token", "data": {"text": visible}}
                for card in cards:
                    hydrated = await self._hydrate_card(card)
                    if hydrated is None:
                        continue
                    emitted_card_ids.append(hydrated["product_id"])
                    yield {"event": "product_card", "data": hydrated}
            tail_visible, tail_cards = extractor.finalize()
            if tail_visible:
                full_visible += tail_visible
                yield {"event": "token", "data": {"text": tail_visible}}
            for card in tail_cards:
                hydrated = await self._hydrate_card(card)
                if hydrated is None:
                    continue
                emitted_card_ids.append(hydrated["product_id"])
                yield {"event": "product_card", "data": hydrated}
        except Exception as exc:
            # LLM 异常：emit error，再推 Top-N 检索结果作兜底卡片
            logger.exception("LLM 流式异常，走降级链路")
            code = _classify_error(exc)
            yield {"event": "error", "data": {"code": code, "message": str(exc)[:200]}}
            tip = "模型暂不可用，先给你看几款最匹配的商品："
            yield {"event": "token", "data": {"text": tip}}
            full_visible += tip
            for p in retrieved[:_FALLBACK_CARDS]:
                hydrated = await self._hydrate_card({"product_id": p.product_id, "reason": "检索 Top-K 兜底"})
                if hydrated is None:
                    continue
                emitted_card_ids.append(hydrated["product_id"])
                yield {"event": "product_card", "data": hydrated}

        # 4) 收尾：写 memory + done
        self.memory.save_turn(session.id, req.message, full_visible, emitted_card_ids)
        yield {"event": "done", "data": {"finish_reason": "stop"}}

    async def _hydrate_card(self, raw_card: dict) -> dict | None:
        """用 MySQL 仓库填字段，防止 LLM 编造价格 / 标题。"""
        pid = raw_card.get("product_id")
        if not pid:
            return None
        view = await self.product_repo.get_card_view(pid)
        if view is None:
            logger.warning("hydrate 失败：product_id=%s 不在 MySQL，丢卡片", pid)
            return None
        # reason 仍来自 LLM（已被 extractor 截断到 ≤ 120 字符）
        view = dict(view)
        view["reason"] = raw_card.get("reason", "")
        return view


def _classify_error(exc: BaseException) -> str:
    """把异常归类为 SSE error code（iOS 端按 code 决定提示文案）。"""
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return "LLM_TIMEOUT"
    if "rate" in name or "429" in str(exc):
        return "LLM_RATE_LIMIT"
    return "LLM_ERROR"
