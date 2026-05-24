"""规则版 IntentRouter 单测。

Phase 2 先用关键词规则跑通最小闭环：
- 默认走商品推荐（recommend）
- 含「对比/比较/哪个更…」类 → compare
- 含「加购/加入购物车/下单/购物车」类 → cart_op（Phase 2 不实操，仅识别）
- 信息明显不足（仅 1-2 字泛指）→ clarify_needed
Phase 4 再 fallback 走 LLM JSON 抽取。
"""
from __future__ import annotations

import pytest

from app.agent.intent import Intent, IntentRouter


@pytest.fixture
def router() -> IntentRouter:
    return IntentRouter()


@pytest.mark.parametrize(
    "text",
    [
        "推荐一款适合油皮的洗面奶",
        "200 元以下的蓝牙耳机有哪些？",
        "推荐防晒霜，但我不要含酒精的",
        "有什么好用的跑鞋",
    ],
)
def test_recommend_default(router: IntentRouter, text: str) -> None:
    intent = router.parse(text)
    assert intent.intent == "recommend"
    assert intent.search_query  # 不为空


def test_compare_intent(router: IntentRouter) -> None:
    intent = router.parse("对比一下兰蔻和雅诗兰黛的精华哪个更保湿")
    assert intent.intent == "compare"


def test_compare_intent_alt_phrasing(router: IntentRouter) -> None:
    intent = router.parse("A 和 B 哪个更好")
    assert intent.intent == "compare"


def test_cart_op_add(router: IntentRouter) -> None:
    intent = router.parse("把这个加入购物车")
    assert intent.intent == "cart_op"


def test_cart_op_order(router: IntentRouter) -> None:
    intent = router.parse("帮我下单这件")
    assert intent.intent == "cart_op"


def test_clarify_needed_for_too_short(router: IntentRouter) -> None:
    intent = router.parse("手机")
    assert intent.intent == "clarify_needed"
    assert intent.clarify_payload is not None
    assert intent.clarify_payload["question"]
    assert isinstance(intent.clarify_payload["options"], list)


def test_clarify_needed_for_one_char(router: IntentRouter) -> None:
    intent = router.parse("？")
    assert intent.intent == "clarify_needed"


def test_search_query_is_original_text(router: IntentRouter) -> None:
    """Phase 2 不做 query rewriting，原文直接当向量检索 query。"""
    msg = "推荐一款 200 元以下的蓝牙耳机"
    intent = router.parse(msg)
    assert intent.intent == "recommend"
    assert intent.search_query == msg
