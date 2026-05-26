"""MemorySummarizer 单测：mock LLM chat_json 验证 prompt 拼装与失败兜底。"""
from __future__ import annotations

import pytest

from app.agent.memory_summarizer import build_memory_summarizer


class _FakeLLM:
    def __init__(self, payload: dict | None = None, raise_exc: Exception | None = None):
        self.payload = payload or {}
        self.raise_exc = raise_exc
        self.last_messages: list[dict] | None = None
        self.calls = 0

    async def chat_json(self, messages, **_kw):
        self.calls += 1
        self.last_messages = list(messages)
        if self.raise_exc:
            raise self.raise_exc
        return self.payload


@pytest.mark.asyncio
async def test_summarize_returns_text_from_llm():
    fake = _FakeLLM(payload={"summary": "用户油皮预算 100 内，已推荐 p_beauty_011"})
    summarizer = build_memory_summarizer(llm=fake)
    older = [
        {"role": "user", "content": "推荐一款洗面奶"},
        {"role": "assistant", "content": "为你推荐 p_beauty_011，泡沫绵密"},
        {"role": "user", "content": "我是油皮"},
        {"role": "assistant", "content": "好的，p_beauty_011 适合油皮"},
    ]
    summary = await summarizer.summarize(previous_summary=None, older_history=older)
    assert summary == "用户油皮预算 100 内，已推荐 p_beauty_011"
    assert fake.calls == 1
    # prompt 里必须包含对话稿
    user_msg = fake.last_messages[-1]["content"]
    assert "推荐一款洗面奶" in user_msg
    assert "油皮" in user_msg


@pytest.mark.asyncio
async def test_summarize_includes_previous_summary_when_present():
    fake = _FakeLLM(payload={"summary": "增量摘要"})
    summarizer = build_memory_summarizer(llm=fake)
    await summarizer.summarize(
        previous_summary="此前摘要：用户喜欢国产手机",
        older_history=[{"role": "user", "content": "再推荐一个"}],
    )
    user_msg = fake.last_messages[-1]["content"]
    assert "此前已摘要" in user_msg
    assert "用户喜欢国产手机" in user_msg


@pytest.mark.asyncio
async def test_summarize_empty_history_returns_previous():
    fake = _FakeLLM(payload={"summary": "不应被调用"})
    summarizer = build_memory_summarizer(llm=fake)
    summary = await summarizer.summarize(
        previous_summary="保留",
        older_history=[],
    )
    assert summary == "保留"
    assert fake.calls == 0


@pytest.mark.asyncio
async def test_summarize_llm_failure_falls_back_to_previous():
    fake = _FakeLLM(raise_exc=TimeoutError("ark down"))
    summarizer = build_memory_summarizer(llm=fake)
    summary = await summarizer.summarize(
        previous_summary="原有摘要",
        older_history=[{"role": "user", "content": "测试"}],
    )
    # LLM 挂了 → 不崩，回退到原有 summary
    assert summary == "原有摘要"


@pytest.mark.asyncio
async def test_summarize_missing_summary_field_returns_previous():
    """LLM 返回非预期 JSON（缺 summary 字段）时不应吃掉旧 summary。"""
    fake = _FakeLLM(payload={"text": "wrong key"})
    summarizer = build_memory_summarizer(llm=fake)
    summary = await summarizer.summarize(
        previous_summary="备份",
        older_history=[{"role": "user", "content": "x"}],
    )
    assert summary == "备份"


@pytest.mark.asyncio
async def test_summarize_truncates_oversized_transcript():
    """超过 4000 字符的 transcript 应该被截断（保留后段）。"""
    fake = _FakeLLM(payload={"summary": "ok"})
    summarizer = build_memory_summarizer(llm=fake)
    long_history = [
        {"role": "user", "content": "x" * 3000},
        {"role": "assistant", "content": "y" * 3000},
    ]
    await summarizer.summarize(previous_summary=None, older_history=long_history)
    user_msg = fake.last_messages[-1]["content"]
    assert len(user_msg) <= 4000
