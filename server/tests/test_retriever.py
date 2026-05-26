"""RAG Retriever 单测：聚合 chunk → product 的去重与排序逻辑。

不依赖真 Milvus / Doubao；注入 fake embedder + fake store。
"""
from __future__ import annotations

from typing import Sequence

import pytest

from app.rag.retriever import RagRetriever, RetrievedProduct


class _FakeEmbedder:
    dim = 4

    def embed_one(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0, 0.0]


class _FakeStore:
    """伪装 Milvus search 输出格式 (list[dict])，按 distance 已降序。"""

    def __init__(self, hits: list[dict]) -> None:
        self._hits = hits

    def search(self, query_vector, top_k=20, filter_expr=None, output_fields=None):
        return self._hits[:top_k]


def _mk_hit(pid: str, score: float, chunk_type: str = "title", text: str = "T") -> dict:
    return {
        "id": hash((pid, chunk_type, text)) & 0xFFFFFFFF,
        "distance": score,
        "entity": {
            "product_id": pid,
            "chunk_type": chunk_type,
            "text": text,
            "category": "美妆",
            "sub_category": "洁面",
            "brand": "Test",
            "base_price": 99.0,
            "min_sku_price": 89.0,
            "max_sku_price": 129.0,
            "rating": 5,
            "source_id": f"{pid}#{chunk_type}#0",
        },
    }


def test_aggregates_chunks_by_product_id_keep_best_score():
    store = _FakeStore(
        [
            _mk_hit("p_a", 0.92, "title", "A标题"),
            _mk_hit("p_a", 0.88, "description", "A详情"),  # 同商品多 chunk
            _mk_hit("p_b", 0.85, "title", "B标题"),
            _mk_hit("p_a", 0.81, "review", "A评论"),
        ]
    )
    retriever = RagRetriever(embedder=_FakeEmbedder(), store=store)
    products = retriever.search("query", top_k_chunks=20, top_n_products=5)
    assert [p.product_id for p in products] == ["p_a", "p_b"]
    a = products[0]
    assert a.score == pytest.approx(0.92)
    # 命中的 chunk 文本要保留下来给 Prompt 用
    assert "A标题" in a.best_chunk_text
    # 同商品命中的其它 chunk 也要带回，方便 Prompt 拼上下文
    assert len(a.supporting_chunks) >= 2


def test_top_n_products_truncates():
    store = _FakeStore(
        [
            _mk_hit("p_a", 0.9),
            _mk_hit("p_b", 0.8),
            _mk_hit("p_c", 0.7),
            _mk_hit("p_d", 0.6),
        ]
    )
    retriever = RagRetriever(embedder=_FakeEmbedder(), store=store)
    products = retriever.search("query", top_n_products=2)
    assert [p.product_id for p in products] == ["p_a", "p_b"]


def test_empty_search_returns_empty_list():
    store = _FakeStore([])
    retriever = RagRetriever(embedder=_FakeEmbedder(), store=store)
    products = retriever.search("query")
    assert products == []


def test_metadata_passed_through():
    store = _FakeStore([_mk_hit("p_a", 0.9, "title", "A")])
    retriever = RagRetriever(embedder=_FakeEmbedder(), store=store)
    p = retriever.search("query")[0]
    assert p.brand == "Test"
    assert p.category == "美妆"
    assert p.base_price == pytest.approx(99.0)


def test_retrieved_product_is_dataclass():
    """提示型测试：RetrievedProduct 字段稳定，orchestrator 依赖它。"""
    p = RetrievedProduct(
        product_id="p_x", score=1.0, brand="b", category="c", sub_category="sc",
        base_price=10.0, best_chunk_text="t", supporting_chunks=["t"],
    )
    assert p.product_id == "p_x"
    assert p.title == ""  # 默认空，title chunk 命中后会填


def test_title_extracted_from_title_chunk():
    """Phase 4 收尾：对比表头不再用 product_id，需要从 title chunk 抽商品名。"""
    store = _FakeStore([
        _mk_hit(
            "p_a", 0.95, "title",
            "兰蔻小黑瓶全新精华肌底液修护维稳细腻毛孔提亮肤色30ml | 品牌：兰蔻 | 类目：精华",
        ),
    ])
    retriever = RagRetriever(embedder=_FakeEmbedder(), store=store)
    products = retriever.search("query")
    assert products[0].title.startswith("兰蔻小黑瓶")
    # 不能包含分隔符后的部分（防止把"品牌：兰蔻 | 类目：..."也算进 title）
    assert " | 品牌：" not in products[0].title


def test_title_empty_when_only_description_chunk():
    """只命中 description chunk 时 title 为空，prompt 端用 brand 兜底。"""
    store = _FakeStore([
        _mk_hit("p_a", 0.9, "description", "这是一段商品描述..."),
    ])
    retriever = RagRetriever(embedder=_FakeEmbedder(), store=store)
    products = retriever.search("query")
    assert products[0].title == ""


def test_title_filled_by_later_title_chunk_when_best_is_other_type():
    """best chunk 是 description，但后续 title chunk 也要把 title 字段补上。"""
    store = _FakeStore([
        _mk_hit("p_a", 0.95, "description", "描述文本"),
        _mk_hit("p_a", 0.80, "title", "雅诗兰黛特润修护肌活精华露30ml | 品牌：雅诗兰黛 | 类目：精华"),
    ])
    retriever = RagRetriever(embedder=_FakeEmbedder(), store=store)
    p = retriever.search("query")[0]
    assert p.best_chunk_type == "description"
    assert p.title.startswith("雅诗兰黛")
