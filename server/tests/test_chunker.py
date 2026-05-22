"""Chunker 单元测试：用真实数据集里的样品验证切分行为。

不依赖外部服务（无 Doubao、无 MySQL、无 Milvus），可在 CI 直接跑。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.rag.chunker import (
    CHUNK_TYPE_DESCRIPTION,
    CHUNK_TYPE_FAQ,
    CHUNK_TYPE_REVIEW,
    CHUNK_TYPE_TITLE,
    Chunk,
    chunk_dataset,
    chunk_product,
)


DATASET_ROOT = Path(__file__).resolve().parents[2] / "ecommerce_agent_dataset"


def _load(category_dir: str, filename: str) -> dict:
    with (DATASET_ROOT / category_dir / "data" / filename).open("r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def beauty_product() -> dict:
    return _load("1_美妆护肤", "p_beauty_001.json")


def test_chunk_types_present(beauty_product: dict) -> None:
    chunks = chunk_product(beauty_product)
    types = {c.metadata["chunk_type"] for c in chunks}
    assert CHUNK_TYPE_TITLE in types
    assert CHUNK_TYPE_DESCRIPTION in types
    assert CHUNK_TYPE_FAQ in types
    assert CHUNK_TYPE_REVIEW in types


def test_chunk_counts_match_source(beauty_product: dict) -> None:
    """p_beauty_001 有 3 条 FAQ + 5 条 review + 1 描述 + 1 标题 = 10 条 chunk。"""
    chunks = chunk_product(beauty_product)
    faq_count = sum(1 for c in chunks if c.metadata["chunk_type"] == CHUNK_TYPE_FAQ)
    review_count = sum(1 for c in chunks if c.metadata["chunk_type"] == CHUNK_TYPE_REVIEW)
    assert faq_count == len(beauty_product["rag_knowledge"]["official_faq"])
    assert review_count == len(beauty_product["rag_knowledge"]["user_reviews"])
    assert len(chunks) == 1 + 1 + faq_count + review_count


def test_metadata_contains_price_range(beauty_product: dict) -> None:
    chunks = chunk_product(beauty_product)
    title_chunk = next(c for c in chunks if c.metadata["chunk_type"] == CHUNK_TYPE_TITLE)
    # p_beauty_001 SKU 价格 720 / 980 / 1260
    assert title_chunk.metadata["base_price"] == 720.0
    assert title_chunk.metadata["min_sku_price"] == 720.0
    assert title_chunk.metadata["max_sku_price"] == 1260.0
    assert isinstance(title_chunk.metadata["base_price"], float)


def test_review_rating_propagated(beauty_product: dict) -> None:
    chunks = chunk_product(beauty_product)
    reviews = [c for c in chunks if c.metadata["chunk_type"] == CHUNK_TYPE_REVIEW]
    expected_ratings = [r["rating"] for r in beauty_product["rag_knowledge"]["user_reviews"]]
    actual_ratings = [c.metadata["rating"] for c in reviews]
    assert actual_ratings == expected_ratings


def test_non_review_chunks_have_rating_zero(beauty_product: dict) -> None:
    chunks = chunk_product(beauty_product)
    for c in chunks:
        if c.metadata["chunk_type"] != CHUNK_TYPE_REVIEW:
            assert c.metadata["rating"] == 0


def test_source_ids_unique(beauty_product: dict) -> None:
    chunks = chunk_product(beauty_product)
    ids = [c.source_id for c in chunks]
    assert len(ids) == len(set(ids))


def test_description_chunk_prefixes_title(beauty_product: dict) -> None:
    """描述 chunk 前面拼了标题（缓解纯描述召回偏向）。"""
    chunks = chunk_product(beauty_product)
    desc = next(c for c in chunks if c.metadata["chunk_type"] == CHUNK_TYPE_DESCRIPTION)
    assert desc.text.startswith(beauty_product["title"])


@pytest.mark.skipif(not DATASET_ROOT.exists(), reason="数据集目录缺失")
def test_full_dataset_yields_expected_volume() -> None:
    """整数据集应该产出 ~1000 条 chunk（docs/02 §2.1 估计）。

    放宽到 [800, 1200] 区间，预留单条 FAQ/review 缺失的容忍度。
    """
    chunks = chunk_dataset(DATASET_ROOT)
    assert 800 <= len(chunks) <= 1200, f"实际产出 {len(chunks)} 条 chunk，超出预期区间"

    # 每个商品至少有 title + description
    pid_counts: dict[str, int] = {}
    for c in chunks:
        pid_counts[c.metadata["product_id"]] = pid_counts.get(c.metadata["product_id"], 0) + 1
    assert len(pid_counts) == 100, f"应覆盖 100 件商品，实际 {len(pid_counts)}"
    assert all(v >= 2 for v in pid_counts.values())


def test_empty_knowledge_handled() -> None:
    """rag_knowledge 全空时仍应至少产出 title chunk，不抛异常。"""
    fake_product = {
        "product_id": "p_test_999",
        "title": "测试商品",
        "brand": "测试品牌",
        "category": "测试类",
        "sub_category": "测试子类",
        "base_price": 100.0,
        "image_path": "x/y.jpg",
        "skus": [{"sku_id": "s_test_999_1", "properties": {}, "price": 100.0}],
        "rag_knowledge": {},
    }
    chunks = chunk_product(fake_product)
    assert len(chunks) == 1
    assert chunks[0].metadata["chunk_type"] == CHUNK_TYPE_TITLE


def test_returns_chunk_instances(beauty_product: dict) -> None:
    chunks = chunk_product(beauty_product)
    assert all(isinstance(c, Chunk) for c in chunks)
