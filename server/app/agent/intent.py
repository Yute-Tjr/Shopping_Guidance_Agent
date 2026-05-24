"""意图识别（Phase 2 规则版）。

设计依据 docs/03 §5.1：
- 100 条数据 + 规则关键词足够覆盖 80% 场景，省一次 LLM 调用，首 token 更快；
- 命中不到再 fallback 走 LLM JSON 抽取（Phase 4 再做，本文件留扩展位）；
- 输出统一 Intent dataclass，方便 orchestrator 分发。

Phase 2 不做 query rewriting / filter 抽取，search_query 直接复用原文。
filters / tool_call / clarify_payload 留位置，下一 Phase 自然演进。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

IntentName = Literal["recommend", "compare", "cart_op", "clarify_needed", "chitchat"]


# Phase 2 用纯关键词，避免 NLP 引入新依赖。中文匹配用「包含」语义即可。
_COMPARE_KEYWORDS = ("对比", "比较", "比一比", "哪个更", "哪款更", "vs", " VS ")
_CART_KEYWORDS = ("加入购物车", "加购", "加到购物车", "购物车", "下单", "结算", "买单", "下个单")
# 信息不足判定：去掉空格/标点后长度 < 3 视为太泛
_TRIVIAL_TAILS = "？?！!。，,. 　"


@dataclass
class Intent:
    intent: IntentName
    search_query: str = ""
    filters: dict[str, Any] = field(default_factory=dict)
    tool_call: Optional[dict[str, Any]] = None
    clarify_payload: Optional[dict[str, Any]] = None


class IntentRouter:
    """规则前置 + （Phase 4 预留）LLM fallback。"""

    def parse(self, message: str, history: Optional[list[dict]] = None) -> Intent:
        text = (message or "").strip()
        stripped = text.strip(_TRIVIAL_TAILS).strip()

        # 1) 信息明显不足 → 主动澄清（docs/01 Phase 4 加分点的雏形）
        if len(stripped) < 3:
            return Intent(
                intent="clarify_needed",
                search_query=text,
                clarify_payload={
                    "question": "可以再具体一点吗？比如想要的品类、预算或使用场景。",
                    "options": ["看品类推荐", "按预算筛选", "按场景推荐"],
                },
            )

        # 2) 购物车 / 下单（Phase 2 仅识别，不真执行，由 orchestrator 出占位话术）
        if any(kw in text for kw in _CART_KEYWORDS):
            return Intent(intent="cart_op", search_query=text)

        # 3) 对比类
        if any(kw in text for kw in _COMPARE_KEYWORDS):
            return Intent(intent="compare", search_query=text)

        # 4) 兜底：商品推荐
        return Intent(intent="recommend", search_query=text)
