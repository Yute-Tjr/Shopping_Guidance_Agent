"""/api/v1/chat/stream SSE 端点集成 smoke。

用 FastAPI TestClient + dependency_overrides 注入 fake orchestrator/repo，
确认 SSE 序列化无误：能拿到 token / product_card / done 三种事件。
"""
from __future__ import annotations

from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from app.agent.memory import ConversationMemory
from app.agent.orchestrator import AgentOrchestrator
from app.api.deps import get_orchestrator, get_product_repo
from app.main import app
from app.rag.retriever import RetrievedProduct


class _FakeRetriever:
    def search(self, q, **_kw):
        return [
            RetrievedProduct(
                product_id="p_x", score=0.9, brand="兰蔻", category="美妆",
                sub_category="洁面", base_price=99.0, min_sku_price=79.0,
                max_sku_price=129.0, best_chunk_text="洁面", best_chunk_type="title",
                supporting_chunks=["洁面"],
            )
        ]


class _FakeLLM:
    async def chat_stream(self, messages, **_kw) -> AsyncIterator[str]:
        for t in [
            "为你推荐：",
            "\n```product_cards\n",
            '[{"product_id":"p_x","reason":"温和控油"}]\n```',
        ]:
            yield t


class _FakeRepo:
    async def get_card_view(self, pid):
        return {
            "product_id": pid,
            "title": "测试洗面奶",
            "brand": "兰蔻",
            "category": "美妆",
            "image_url": "http://localhost:8000/static/x.jpg",
            "price_range": {"min": 79.0, "max": 129.0},
            "skus": [],
        }

    async def get_detail(self, pid):
        return None


@pytest.fixture
def client():
    fake_orch = AgentOrchestrator(
        retriever=_FakeRetriever(),
        llm=_FakeLLM(),
        product_repo=_FakeRepo(),
        memory=ConversationMemory(),
    )
    app.dependency_overrides[get_orchestrator] = lambda: fake_orch
    app.dependency_overrides[get_product_repo] = lambda: _FakeRepo()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_chat_stream_emits_token_card_and_done(client: TestClient):
    with client.stream(
        "POST", "/api/v1/chat/stream",
        json={"session_id": None, "message": "推荐一款洗面奶"},
        headers={"accept": "text/event-stream"},
    ) as resp:
        assert resp.status_code == 200
        body = "".join(chunk for chunk in resp.iter_text())

    # SSE 帧形如 'event: token\ndata: {"text":"..."}\n\n'
    assert "event: session" in body
    assert "event: token" in body
    assert "event: product_card" in body
    assert "event: done" in body
    # 卡片 payload 应该被 hydrate 出 title/brand
    assert "测试洗面奶" in body
    assert "兰蔻" in body
