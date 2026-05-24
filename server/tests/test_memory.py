"""ConversationMemory 单测。

Phase 2 不持久化、不摘要——只做：
- get_or_create(session_id)：None / 空 → 生成新 id
- save_turn：追加 user/assistant 对
- history 最近 N 轮（默认 6）后 FIFO 截断
- last_recommended_ids：记录上一轮推荐的 product_id，给 Phase 4 指代消解用
"""
from __future__ import annotations

import pytest

from app.agent.memory import ConversationMemory


def test_get_or_create_with_none_generates_id():
    mem = ConversationMemory()
    session = mem.get_or_create(None)
    assert session.id  # 非空 uuid 字符串
    assert session.history == []


def test_get_or_create_returns_same_session_on_repeat_call():
    mem = ConversationMemory()
    s1 = mem.get_or_create(None)
    s2 = mem.get_or_create(s1.id)
    assert s1 is s2


def test_save_turn_appends_user_and_assistant():
    mem = ConversationMemory()
    s = mem.get_or_create(None)
    mem.save_turn(s.id, "推荐洗面奶", "为你推荐 A", ["p_a"])
    assert len(s.history) == 2
    assert s.history[0]["role"] == "user"
    assert s.history[0]["content"] == "推荐洗面奶"
    assert s.history[1]["role"] == "assistant"
    assert s.history[1]["content"] == "为你推荐 A"
    assert s.last_recommended_ids == ["p_a"]


def test_history_truncated_to_window():
    mem = ConversationMemory(max_turns=2)  # 2 轮 = 4 条 message
    s = mem.get_or_create(None)
    mem.save_turn(s.id, "u1", "a1", [])
    mem.save_turn(s.id, "u2", "a2", [])
    mem.save_turn(s.id, "u3", "a3", [])
    # 只保留最近 2 轮：u2/a2/u3/a3
    assert len(s.history) == 4
    assert s.history[0]["content"] == "u2"
    assert s.history[-1]["content"] == "a3"


def test_last_recommended_ids_replaced_each_turn():
    mem = ConversationMemory()
    s = mem.get_or_create(None)
    mem.save_turn(s.id, "u1", "a1", ["p_a", "p_b"])
    assert s.last_recommended_ids == ["p_a", "p_b"]
    mem.save_turn(s.id, "u2", "a2", ["p_c"])
    assert s.last_recommended_ids == ["p_c"]
