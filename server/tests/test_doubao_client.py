"""Doubao LLM 流式客户端单测（不打真实网络）。

DoubaoChatClient 内部依赖 AsyncArk，测试时用 fake stream 注入
（通过把底层 create 替换成返回伪 AsyncIterator 的 monkeypatch），
验证：
1. 多个 delta token 顺序 yield
2. 空 delta 不会被 yield（避免下游浪费一帧 SSE）
3. tool_calls 等非内容 chunk 不影响（Phase 2 暂未用 tool，留容错）
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import pytest

from app.llm.doubao_client import DoubaoChatClient


@dataclass
class _Delta:
    content: Optional[str] = None


@dataclass
class _Choice:
    delta: Optional[_Delta] = None


@dataclass
class _Chunk:
    choices: list[_Choice]


async def _aiter(items):
    for it in items:
        yield it


def _make_stream(token_texts: list[Optional[str]]) -> AsyncIterator[_Chunk]:
    items = []
    for t in token_texts:
        items.append(_Chunk(choices=[_Choice(delta=_Delta(content=t))]))
    return _aiter(items)


@pytest.mark.asyncio
async def test_chat_stream_yields_tokens_in_order(monkeypatch):
    client = DoubaoChatClient.__new__(DoubaoChatClient)
    client.model = "ep-test"

    async def fake_create(**_kwargs):
        return _make_stream(["你好", "，", "我是"])

    fake_completions = type("C", (), {"create": staticmethod(fake_create)})()
    fake_chat = type("Chat", (), {"completions": fake_completions})()
    client._client = type("ArkLike", (), {"chat": fake_chat})()

    out = []
    async for tok in client.chat_stream(messages=[{"role": "user", "content": "hi"}]):
        out.append(tok)
    assert out == ["你好", "，", "我是"]


@pytest.mark.asyncio
async def test_chat_stream_skips_empty_delta(monkeypatch):
    client = DoubaoChatClient.__new__(DoubaoChatClient)
    client.model = "ep-test"

    async def fake_create(**_kwargs):
        return _make_stream(["开始", None, "", "结束"])

    client._client = type(
        "ArkLike",
        (),
        {"chat": type("Chat", (), {"completions": type("C", (), {"create": staticmethod(fake_create)})()})()},
    )()

    out = [t async for t in client.chat_stream(messages=[{"role": "user", "content": "hi"}])]
    assert out == ["开始", "结束"]


@pytest.mark.asyncio
async def test_chat_stream_tolerates_chunk_without_choices(monkeypatch):
    client = DoubaoChatClient.__new__(DoubaoChatClient)
    client.model = "ep-test"

    async def fake_create(**_kwargs):
        async def stream():
            yield _Chunk(choices=[])  # 心跳 / usage-only chunk
            yield _Chunk(choices=[_Choice(delta=_Delta(content="hi"))])
        return stream()

    client._client = type(
        "ArkLike",
        (),
        {"chat": type("Chat", (), {"completions": type("C", (), {"create": staticmethod(fake_create)})()})()},
    )()

    out = [t async for t in client.chat_stream(messages=[{"role": "user", "content": "hi"}])]
    assert out == ["hi"]
