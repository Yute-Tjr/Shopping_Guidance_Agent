"""POST /api/v1/upload/image 单测：MIME / 大小 / 解码 / 限流降级 / 缓存复用。"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api.deps import (
    get_image_embed_cache,
    get_retriever,
    get_upload_dir,
)
from app.main import app
from app.rag.image_embed_cache import ImageEmbedCache


def _png_bytes(color: tuple[int, int, int] = (10, 20, 30), size: int = 32) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, "PNG")
    return buf.getvalue()


def _jpg_bytes(color: tuple[int, int, int] = (10, 20, 30), size: int = 32) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, "JPEG", quality=85)
    return buf.getvalue()


class _StubEmbedder:
    """假 embedder：返回固定向量，不走真实 API。"""
    def __init__(self, *, fail: bool = False):
        self._fail = fail
    def embed_image(self, path: str) -> list[float]:
        if self._fail:
            raise RuntimeError("vision API 限流")
        return [0.1] * 8


class _StubRetriever:
    def __init__(self, *, fail: bool = False):
        self.embedder = _StubEmbedder(fail=fail)


@pytest.fixture
def client(tmp_path: Path):
    cache = ImageEmbedCache(capacity=10, ttl_seconds=300)
    app.dependency_overrides[get_image_embed_cache] = lambda: cache
    app.dependency_overrides[get_upload_dir] = lambda: tmp_path
    app.dependency_overrides[get_retriever] = lambda: _StubRetriever()
    with TestClient(app) as c:
        yield c, cache, tmp_path
    app.dependency_overrides.clear()


def test_upload_happy_path_returns_image_id_and_caches_vec(client):
    c, cache, tmp_path = client
    body = _jpg_bytes()
    resp = c.post(
        "/api/v1/upload/image",
        files={"file": ("test.jpg", body, "image/jpeg")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "image_id" in data
    image_id = data["image_id"]
    # 落盘
    saved = list(tmp_path.glob(f"{image_id}.*"))
    assert len(saved) == 1
    # 缓存命中
    import asyncio
    got = asyncio.get_event_loop().run_until_complete(cache.get(image_id))
    assert got is not None
    vec, path = got
    assert len(vec) == 8
    assert path == str(saved[0])


def test_upload_rejects_unsupported_mime(client):
    c, _, _ = client
    resp = c.post(
        "/api/v1/upload/image",
        files={"file": ("test.gif", b"GIF89a", "image/gif")},
    )
    assert resp.status_code == 415


def test_upload_rejects_oversize(client):
    c, _, _ = client
    big = b"\xff\xd8\xff\xe0" + b"x" * (2 * 1024 * 1024)  # >1MB
    resp = c.post(
        "/api/v1/upload/image",
        files={"file": ("big.jpg", big, "image/jpeg")},
    )
    assert resp.status_code == 413


def test_upload_rejects_corrupted_image(client):
    c, _, _ = client
    resp = c.post(
        "/api/v1/upload/image",
        files={"file": ("broken.jpg", b"not-an-image", "image/jpeg")},
    )
    assert resp.status_code == 422


def test_upload_rejects_empty_file(client):
    c, _, _ = client
    resp = c.post(
        "/api/v1/upload/image",
        files={"file": ("empty.jpg", b"", "image/jpeg")},
    )
    assert resp.status_code == 400


def test_upload_returns_503_when_vision_api_fails(client, tmp_path: Path):
    c, _, _ = client
    # 把 retriever override 改成 fail 版本
    app.dependency_overrides[get_retriever] = lambda: _StubRetriever(fail=True)
    body = _jpg_bytes()
    resp = c.post(
        "/api/v1/upload/image",
        files={"file": ("test.jpg", body, "image/jpeg")},
    )
    assert resp.status_code == 503
    body_json = resp.json()
    # FastAPI 把 detail dict 整体放在 "detail" 字段下
    assert body_json["detail"]["fallback_text_only"] is True
