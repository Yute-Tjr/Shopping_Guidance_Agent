"""ImageEmbedCache：LRU + TTL + 并发安全的图 embedding 内存缓存。"""
from __future__ import annotations

import asyncio

import pytest

from app.rag.image_embed_cache import ImageEmbedCache


@pytest.mark.asyncio
async def test_put_and_get_roundtrip():
    cache = ImageEmbedCache(capacity=2)
    await cache.put("a", [1.0, 2.0, 3.0], "/tmp/a.jpg")
    got = await cache.get("a")
    assert got == ([1.0, 2.0, 3.0], "/tmp/a.jpg")


@pytest.mark.asyncio
async def test_get_returns_none_for_missing_key():
    cache = ImageEmbedCache()
    assert await cache.get("never-existed") is None


@pytest.mark.asyncio
async def test_get_returns_none_after_ttl_expires():
    cache = ImageEmbedCache(ttl_seconds=0.05)
    await cache.put("a", [1.0], "/tmp/a.jpg")
    await asyncio.sleep(0.1)
    assert await cache.get("a") is None


@pytest.mark.asyncio
async def test_lru_capacity_evicts_least_recently_used():
    cache = ImageEmbedCache(capacity=2)
    await cache.put("a", [1.0], "/a")
    await cache.put("b", [2.0], "/b")
    # 访问 a 使其变 most recently used
    await cache.get("a")
    # 现在写入 c：应驱逐 b（LRU），而非 a
    await cache.put("c", [3.0], "/c")
    assert await cache.get("a") is not None
    assert await cache.get("b") is None
    assert await cache.get("c") is not None


@pytest.mark.asyncio
async def test_concurrent_puts_do_not_tear():
    cache = ImageEmbedCache(capacity=200)

    async def write(i: int) -> None:
        await cache.put(f"k{i}", [float(i)], f"/p{i}")

    await asyncio.gather(*(write(i) for i in range(50)))
    assert len(cache) == 50
    for i in range(50):
        got = await cache.get(f"k{i}")
        assert got == ([float(i)], f"/p{i}")
