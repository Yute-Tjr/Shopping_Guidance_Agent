"""ClarifyDetector 单测：触发场景 + 反例（已有约束 / 已有修饰词 不应触发）。"""
from __future__ import annotations

import pytest

from app.agent.clarify_detector import ClarifyDetector, build_clarify_detector
from app.agent.query_rewriter import ParsedQuery


@pytest.fixture
def det() -> ClarifyDetector:
    return build_clarify_detector()


# ---------- 应触发 ----------

@pytest.mark.parametrize(
    "msg,expected_key",
    [
        ("推荐一款手机", "手机"),
        ("推荐手机", "手机"),
        ("帮我推荐一款笔记本", "笔记本"),
        ("有什么耳机推荐", "耳机"),
        ("来一双跑鞋", "跑鞋"),
        ("想买洗面奶", "洗面奶"),
        ("推荐一款防晒霜", "防晒"),
        ("买点零食", "零食"),
        ("有什么咖啡", "咖啡"),
        ("推荐个背包", "背包"),
    ],
)
def test_triggers_clarify_on_bare_category(det, msg, expected_key):
    decision = det.assess(intent_name="recommend", message=msg, parsed=ParsedQuery(search_query=msg))
    assert decision is not None, f"应触发 clarify 的 query 没触发: {msg}"
    assert decision.should_clarify is True
    assert decision.category_key == expected_key
    assert decision.question
    assert len(decision.options) >= 3


# ---------- 不应触发：已有具体修饰 ----------

@pytest.mark.parametrize(
    "msg",
    [
        "推荐一款拍照好的手机",
        "推荐一款适合油皮的洗面奶",
        "推荐一款长续航的耳机",
        "300 元以下的防晒霜",
        "买点无糖饮料",
        "降噪耳机",
        "马拉松竞速跑鞋",
        "推荐高倍防晒",
    ],
)
def test_skipped_when_query_has_specific_hint(det, msg):
    decision = det.assess(intent_name="recommend", message=msg, parsed=ParsedQuery(search_query=msg))
    assert decision is None, f"不该触发的 query 触发了: {msg}"


# ---------- 不应触发：ParsedQuery 已有结构化约束 ----------

def test_skipped_when_price_filter_present(det):
    parsed = ParsedQuery(search_query="手机", price_max=3000)
    decision = det.assess(intent_name="recommend", message="3000 以内的手机", parsed=parsed)
    assert decision is None


def test_skipped_when_brand_filter_present(det):
    parsed = ParsedQuery(search_query="手机", brands_exclude=["Apple 苹果"])
    decision = det.assess(intent_name="recommend", message="国产手机推荐", parsed=parsed)
    assert decision is None


def test_skipped_when_brand_include(det):
    parsed = ParsedQuery(search_query="耳机", brands_include=["华为"])
    decision = det.assess(intent_name="recommend", message="华为耳机", parsed=parsed)
    assert decision is None


# ---------- 不应触发：意图本身不是 recommend ----------

@pytest.mark.parametrize(
    "intent_name",
    ["compare", "cart_op", "clarify_needed", "chitchat"],
)
def test_skipped_for_non_recommend_intents(det, intent_name):
    decision = det.assess(intent_name=intent_name, message="推荐一款手机", parsed=ParsedQuery(search_query="手机"))
    assert decision is None


# ---------- 边界 ----------

def test_skipped_for_empty_message(det):
    assert det.assess(intent_name="recommend", message="", parsed=ParsedQuery(search_query="")) is None
    assert det.assess(intent_name="recommend", message="   ", parsed=ParsedQuery(search_query="")) is None


def test_skipped_when_no_category_match(det):
    """剥掉通用词后剩 "汽车"，不在模板里 → 不触发。"""
    decision = det.assess(intent_name="recommend", message="推荐一辆汽车", parsed=ParsedQuery(search_query="汽车"))
    assert decision is None


def test_works_without_parsed_query(det):
    """parsed=None 时也要能跑（兼容没接 rewriter 的 orchestrator）。"""
    decision = det.assess(intent_name="recommend", message="推荐一款手机", parsed=None)
    assert decision is not None
    assert decision.category_key == "手机"


def test_options_are_mutually_distinct(det):
    """同一个类目的 chips 选项之间必须互斥，避免出现 ['长续航','续航久'] 这种重复语义。"""
    decision = det.assess(intent_name="recommend", message="推荐一款手机", parsed=None)
    assert decision is not None
    assert len(set(decision.options)) == len(decision.options)
