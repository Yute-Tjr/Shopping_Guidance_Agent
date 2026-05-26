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
from app.agent.clarify_detector import ClarifyDetector
from app.agent.compare_planner import CompareTargetExtractor
from app.agent.intent import IntentRouter
from app.agent.memory import ConversationMemory
from app.agent.memory_summarizer import MemorySummarizer
from app.agent.prompts import build_compare_messages, build_recommend_messages
from app.agent.query_rewriter import ParsedQuery, QueryRewriter
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
        query_rewriter: QueryRewriter | None = None,
        compare_extractor: CompareTargetExtractor | None = None,
        clarify_detector: ClarifyDetector | None = None,
        memory_summarizer: MemorySummarizer | None = None,
    ) -> None:
        self.retriever = retriever
        self.llm = llm
        self.product_repo = product_repo
        self.memory = memory
        self.intent_router = intent_router or IntentRouter()
        # query_rewriter 缺省时按"identity rewriter"用：保留 search_query 原文、不抽 filter，
        # 便于 phase 2 旧测试不变更直接通过。
        self.query_rewriter = query_rewriter
        # compare_extractor 缺省时 compare 分支退化为整句一次性检索（phase 2 行为）。
        self.compare_extractor = compare_extractor
        # clarify_detector 缺省时直接跳过主动澄清判定（保 phase 2 行为）。
        self.clarify_detector = clarify_detector
        # memory_summarizer 缺省时 history 走 FIFO 截断（保 phase 2 行为）。
        self.memory_summarizer = memory_summarizer

    async def orchestrate(self, req: ChatRequest) -> AsyncIterator[dict]:
        session = self.memory.get_or_create(req.session_id)
        yield {"event": "session", "data": {"session_id": session.id}}

        # 0) Phase 4-4：进入新一轮前，看看上一轮 save_turn 后是否需要摘要
        # 触发条件：history ≥ summary_after_turns 轮。await summarizer 同步阻塞，
        # 多等 1-2s LLM 调用换取后续多轮上下文不丢失。
        if self.memory_summarizer is not None and self.memory.needs_summary(session):
            try:
                older = self.memory.get_history_to_summarize(session)
                new_summary = await self.memory_summarizer.summarize(
                    previous_summary=session.summary,
                    older_history=older,
                )
                if new_summary:
                    self.memory.apply_summary(session.id, new_summary)
                    logger.info(
                        "session %s 历史已摘要，summary=%s（保留最近 %d 轮原文）",
                        session.id, new_summary[:60], self.memory.keep_recent_turns,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("memory 摘要失败，本轮带完整 history 继续：%s", exc)

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

        # 2) 检索（Phase 4：先 query_rewriter 抽干净 query + filter，再走向量召回 + metadata filter）
        yield {"event": "status", "data": {"stage": "retrieving"}}
        parsed = await self._rewrite(intent.search_query, history=session.history)
        filter_expr = parsed.to_filter_expr()
        if filter_expr:
            logger.info("Milvus filter_expr=%s, search_query=%s", filter_expr, parsed.search_query)

        # 2.5) Phase 4-3 主动澄清：recommend 意图信息不足时短路 emit clarify + done，
        # 不再走 LLM 推一台"瞎猜的"商品出来。
        if intent.intent == "recommend" and self.clarify_detector is not None:
            decision = self.clarify_detector.assess(
                intent_name=intent.intent,
                message=req.message,
                parsed=parsed,
            )
            if decision is not None and decision.should_clarify:
                yield {
                    "event": "clarify",
                    "data": {"question": decision.question, "options": decision.options},
                }
                # 这一轮不进 retrieve，也不算一次完整推荐，但要把 user message 写进 memory
                # 方便下一轮用户点 chip 后能续上下文
                self.memory.save_turn(session.id, req.message, "", [])
                yield {"event": "done", "data": {"finish_reason": "stop"}}
                return

        if intent.intent == "compare":
            # Phase 4-2：compare 分支拆 targets 并行检索，保证 2-3 件代表商品都进 prompt
            retrieved = await self._compare_retrieve(
                original_query=intent.search_query,
                rewritten=parsed.search_query,
                filter_expr=filter_expr,
            )
        else:
            retrieved = self.retriever.search(parsed.search_query, filter_expr=filter_expr)
            # 命中为空时降级：去掉 filter 再来一次，避免"啥也搜不到"的死路（用户体验优先）
            if not retrieved and filter_expr:
                logger.info("filter 过严命中 0 条，去除过滤兜底再检索")
                retrieved = self.retriever.search(parsed.search_query)

        # 3) 生成
        yield {"event": "status", "data": {"stage": "generating"}}
        if intent.intent == "compare":
            messages = build_compare_messages(
                user_message=req.message, retrieved=retrieved,
                history=session.history, summary=session.summary,
            )
        else:
            messages = build_recommend_messages(
                user_message=req.message, retrieved=retrieved,
                history=session.history, summary=session.summary,
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

    async def _compare_retrieve(
        self,
        *,
        original_query: str,
        rewritten: str,
        filter_expr: str | None,
    ) -> list[RetrievedProduct]:
        """compare 分支专用：每个 target 各 Top-2，合并去重最多 3 件塞 prompt。

        没接 extractor 或拆不出 ≥2 个 target → 退化成整句一次性检索。
        合并后 < 2 件时去掉 filter 兜底再来一次（避免 filter 太严卡住对比）。
        """
        # 没接 extractor 直接走整句
        if self.compare_extractor is None:
            retrieved = self.retriever.search(rewritten, filter_expr=filter_expr)
            if not retrieved and filter_expr:
                retrieved = self.retriever.search(rewritten)
            return retrieved

        try:
            plan = await self.compare_extractor.plan(original_query)
        except Exception as exc:  # noqa: BLE001
            logger.warning("compare 拆 target 异常，整句兜底：%s", exc)
            plan = None

        targets: list[str] = []
        if plan is not None and len(plan.targets) >= 2:
            targets = plan.targets[:3]
            logger.info("compare targets=%s", targets)
        if not targets:
            retrieved = self.retriever.search(rewritten, filter_expr=filter_expr)
            if not retrieved and filter_expr:
                retrieved = self.retriever.search(rewritten)
            return retrieved

        # 对每个 target 各 retrieve，按命中顺序保留首次出现的 product
        merged: dict[str, RetrievedProduct] = {}
        for tgt in targets:
            hits = self.retriever.search(tgt, top_n_products=2, filter_expr=filter_expr)
            for p in hits:
                merged.setdefault(p.product_id, p)
        # 合起来不足 2 件时去掉 filter 重试，避免对比一边查不到
        if len(merged) < 2 and filter_expr:
            for tgt in targets:
                for p in self.retriever.search(tgt, top_n_products=2):
                    merged.setdefault(p.product_id, p)
        # 仍不足 → 整句兜底
        if len(merged) < 2:
            for p in self.retriever.search(rewritten):
                merged.setdefault(p.product_id, p)
                if len(merged) >= 3:
                    break
        # 最多 3 件，保证 prompt 不爆 + LLM 表格不超宽
        return list(merged.values())[:3]

    async def _rewrite(
        self,
        search_query: str,
        *,
        history: list[dict] | None = None,
    ) -> ParsedQuery:
        """没接 rewriter 就返回 identity ParsedQuery（保 Phase 2 行为）。"""
        if self.query_rewriter is None:
            return ParsedQuery(search_query=search_query)
        try:
            return await self.query_rewriter.parse(search_query, history=history)
        except Exception as exc:  # noqa: BLE001
            logger.warning("QueryRewriter.parse 异常，走原文检索：%s", exc)
            return ParsedQuery(search_query=search_query)

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
