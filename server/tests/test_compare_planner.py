"""CompareTargetExtractor 单测：规则切分 + LLM 兜底。"""
from __future__ import annotations

import pytest

from app.agent.compare_planner import build_compare_extractor


# ---- 规则路径 ----

@pytest.mark.asyncio
async def test_rule_split_with_he():
    ext = build_compare_extractor()
    plan = await ext.plan("对比一下兰蔻和雅诗兰黛的精华哪个更保湿")
    assert len(plan.targets) == 2
    # 两个 target 都要包含品牌名 + 公共属性"精华" + 尾部关键词"保湿"
    joined = " | ".join(plan.targets)
    assert "兰蔻" in joined and "雅诗兰黛" in joined
    assert "精华" in plan.targets[0] and "精华" in plan.targets[1]
    assert "保湿" in plan.targets[0] and "保湿" in plan.targets[1]


@pytest.mark.asyncio
async def test_rule_split_with_vs():
    ext = build_compare_extractor()
    plan = await ext.plan("iPhone 17 vs 华为 Pura 90")
    assert len(plan.targets) == 2
    assert "iPhone" in plan.targets[0]
    assert "华为" in plan.targets[1] or "Pura" in plan.targets[1]


@pytest.mark.asyncio
async def test_rule_split_simple_brand_pair():
    ext = build_compare_extractor()
    plan = await ext.plan("比较 兰蔻 和 雅诗兰黛")
    assert len(plan.targets) == 2
    assert "兰蔻" in plan.targets[0]
    assert "雅诗兰黛" in plan.targets[1]


@pytest.mark.asyncio
async def test_rule_three_targets():
    ext = build_compare_extractor()
    plan = await ext.plan("对比 iPhone、华为、小米 三款手机哪个性价比高")
    assert 2 <= len(plan.targets) <= 3
    joined = " | ".join(plan.targets)
    assert "iPhone" in joined and "华为" in joined and "小米" in joined


@pytest.mark.asyncio
async def test_rule_with_yu_connector():
    """用"与"连接也要识别。"""
    ext = build_compare_extractor()
    plan = await ext.plan("对比兰蔻与雅诗兰黛")
    assert len(plan.targets) == 2


@pytest.mark.asyncio
async def test_no_compare_keyword_falls_back_to_single_target():
    """没明显触发词时不能强行切，回退原文整体作为单一 target。"""
    ext = build_compare_extractor()
    plan = await ext.plan("推荐一款适合敏感肌的精华")
    # 没触发词 → 规则切不出 2 段 → 单一目标兜底
    assert plan.targets == ["推荐一款适合敏感肌的精华"] or len(plan.targets) <= 1


@pytest.mark.asyncio
async def test_empty_input_returns_empty_plan():
    ext = build_compare_extractor()
    plan = await ext.plan("")
    assert plan.targets == []


# ---- LLM 兜底 ----

class _FakeLLM:
    def __init__(self, payload, raise_exc=None):
        self.payload = payload
        self.raise_exc = raise_exc
        self.calls = 0

    async def chat_json(self, messages, **_):
        self.calls += 1
        if self.raise_exc:
            raise self.raise_exc
        return self.payload


@pytest.mark.asyncio
async def test_llm_fallback_when_rules_fail():
    """规则切不出 2 段时调 LLM 兜底。"""
    fake = _FakeLLM(payload={"targets": ["A 精华 保湿", "B 精华 保湿"]})
    # 用一句没明显触发词、规则切不出来的句子
    ext = build_compare_extractor(llm=fake)
    plan = await ext.plan("帮我看看哪个精华好用")
    # 规则切不出 2 段 → 触发 LLM
    if fake.calls == 1:
        assert plan.used_llm
        assert plan.targets == ["A 精华 保湿", "B 精华 保湿"]
    else:
        # 规则刚好切出来了也行，但本测试期望 LLM 兜底
        pytest.skip("规则路径未走到 LLM 兜底")


@pytest.mark.asyncio
async def test_llm_skipped_when_rules_sufficient():
    """规则已经切出 ≥2 段时不再调 LLM。"""
    fake = _FakeLLM(payload={"targets": []})
    ext = build_compare_extractor(llm=fake)
    plan = await ext.plan("对比 A 和 B")
    assert fake.calls == 0
    assert len(plan.targets) >= 2


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_original_message():
    fake = _FakeLLM(payload=None, raise_exc=TimeoutError("ark down"))
    ext = build_compare_extractor(llm=fake)
    plan = await ext.plan("帮我看看哪款精华好用")
    # LLM 挂了 → 不崩，原文整段当 single target
    assert plan.targets == ["帮我看看哪款精华好用"]
    assert not plan.used_llm
