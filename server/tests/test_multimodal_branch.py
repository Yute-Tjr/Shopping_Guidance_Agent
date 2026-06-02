"""MultimodalBranch：图+文 → query_vector + filter_expr + retrieve 聚合。

不调真实 vision API / 真实 Milvus：注入 fake embedder + fake retriever + fake cache。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.agent.multimodal_branch import MultimodalBranch, rerank_multimodal_products
from app.agent.query_rewriter import ParsedQuery
from app.rag.retriever import RetrievedProduct


class _StubCache:
    def __init__(self, store: dict | None = None):
        self._store = store or {}
    async def get(self, image_id: str):
        return self._store.get(image_id)
    async def put(self, image_id: str, vec, path):
        self._store[image_id] = (vec, path)


class _StubEmbedder:
    def __init__(self):
        self.called_with: dict = {}
    def embed_multimodal(self, *, text=None, image_path=None):
        self.called_with = {"text": text, "image_path": image_path}
        return [0.5] * 4
    def embed_image(self, image_path: str):
        self.called_with = {"image_path": image_path}
        return [0.6] * 4


class _StubRewriter:
    def __init__(self, parsed: ParsedQuery):
        self._parsed = parsed
    async def parse(self, msg, *, history=None, summary=None) -> ParsedQuery:
        return self._parsed


def _mk_hit(pid: str, score: float = 0.9, chunk_type: str = "image", brand: str = "X") -> dict:
    """模拟 milvus store.search 返回的命中格式（list[dict]）。"""
    return {
        "id": hash(pid) & 0xFFFFFFFF,
        "distance": score,
        "entity": {
            "product_id": pid,
            "chunk_type": chunk_type,
            "text": f"{pid} 占位",
            "category": "美妆",
            "sub_category": "精华",
            "brand": brand,
            "base_price": 100.0,
            "min_sku_price": 90.0,
            "max_sku_price": 110.0,
            "rating": 5,
            "source_id": f"{pid}#{chunk_type}#0",
        },
    }


def _mk_product(
    pid: str,
    *,
    score: float,
    brand: str,
    sub_category: str,
    base_price: float,
) -> RetrievedProduct:
    return RetrievedProduct(
        product_id=pid,
        score=score,
        brand=brand,
        category="数码电子",
        sub_category=sub_category,
        base_price=base_price,
        min_sku_price=base_price,
        max_sku_price=base_price,
    )


class _StubStore:
    """模拟 ProductTextStore.search：MultimodalBranch 实际调用 retriever.store.search。"""
    def __init__(self, hits: list[dict]):
        self._hits = hits
        self.search_calls: list[dict] = []
    def search(self, *, query_vector, top_k=20, filter_expr=None):
        self.search_calls.append({"filter_expr": filter_expr, "top_k": top_k})
        return list(self._hits)


class _StubRetriever:
    """MultimodalBranch 通过 retriever.store.search 走底层，所以 stub 只需 expose .store。"""
    def __init__(self, hits: list[dict]):
        self.store = _StubStore(hits)
    @property
    def search_calls(self) -> list[dict]:
        return self.store.search_calls


@pytest.mark.asyncio
async def test_branch_uses_cached_vec_when_available():
    cached_vec = [0.1] * 4
    cache = _StubCache({"img1": (cached_vec, "/tmp/img1.jpg")})
    embedder = _StubEmbedder()
    rewriter = _StubRewriter(ParsedQuery(search_query="这个"))
    retriever = _StubRetriever([_mk_hit("p_a")])

    branch = MultimodalBranch(
        embedder=embedder, retriever=retriever, cache=cache,
        query_rewriter=rewriter, structured_retriever=None,
    )
    result = await branch.handle(
        message="这个", image_id="img1", history=None, summary=None,
    )

    assert result.query_vector == cached_vec
    # 没有再调 embed_multimodal（缓存命中）
    assert embedder.called_with == {}
    assert [p.product_id for p in result.retrieved] == ["p_a"]


@pytest.mark.asyncio
async def test_branch_recomputes_when_cache_miss():
    cache = _StubCache({})  # 全空
    embedder = _StubEmbedder()
    rewriter = _StubRewriter(ParsedQuery(search_query="便宜的"))
    retriever = _StubRetriever([_mk_hit("p_a")])

    branch = MultimodalBranch(
        embedder=embedder, retriever=retriever, cache=cache,
        query_rewriter=rewriter, structured_retriever=None,
        fallback_image_path_resolver=lambda iid: f"/tmp/{iid}.jpg",
    )
    result = await branch.handle(
        message="便宜的", image_id="miss", history=None, summary=None,
    )

    # 应该 fallback 调 embed_multimodal
    assert embedder.called_with["image_path"] == "/tmp/miss.jpg"
    assert embedder.called_with["text"] == "便宜的"
    assert len(result.query_vector) == 4
    # 重算结果应被回填到缓存
    cached = await cache.get("miss")
    assert cached is not None


@pytest.mark.asyncio
async def test_branch_attaches_chunk_type_filter_for_image_and_title():
    cache = _StubCache({"x": ([0.0] * 4, "/tmp/x.jpg")})
    embedder = _StubEmbedder()
    rewriter = _StubRewriter(ParsedQuery(search_query="同款"))
    retriever = _StubRetriever([_mk_hit("p_a")])

    branch = MultimodalBranch(
        embedder=embedder, retriever=retriever, cache=cache,
        query_rewriter=rewriter, structured_retriever=None,
    )
    await branch.handle(message="同款", image_id="x", history=None, summary=None)

    assert len(retriever.search_calls) == 1
    fexpr = retriever.search_calls[0]["filter_expr"]
    assert 'chunk_type in ["image", "title"]' in fexpr


@pytest.mark.asyncio
async def test_branch_combines_structural_filter_with_chunk_type():
    cache = _StubCache({"x": ([0.0] * 4, "/tmp/x.jpg")})
    embedder = _StubEmbedder()
    parsed = ParsedQuery(
        search_query="同款",
        price_max=1000.0,
        categories=["服饰运动"],
        sub_categories=["跑步鞋"],
        brands_exclude=["耐克"],
    )
    rewriter = _StubRewriter(parsed)
    retriever = _StubRetriever([_mk_hit("p_a")])

    branch = MultimodalBranch(
        embedder=embedder, retriever=retriever, cache=cache,
        query_rewriter=rewriter, structured_retriever=None,
    )
    await branch.handle(
        message="同款 1000 以下不要耐克", image_id="x",
        history=None, summary=None,
    )

    fexpr = retriever.search_calls[0]["filter_expr"]
    # 同时包含价格 + brand_exclude + chunk_type
    assert "1000" in fexpr  # price_max
    assert 'sub_category in ["跑步鞋"]' in fexpr
    assert "耐克" in fexpr
    assert "chunk_type" in fexpr


@pytest.mark.asyncio
async def test_branch_falls_back_to_text_only_when_image_missing_on_disk():
    """缓存 miss + 落盘图也找不到 → 退化到纯文本 embed，emit warning。"""
    cache = _StubCache({})

    class _EmbedderRaising(_StubEmbedder):
        def embed_multimodal(self, *, text=None, image_path=None):
            if image_path:
                raise FileNotFoundError(image_path)
            return [0.7] * 4

    embedder = _EmbedderRaising()
    rewriter = _StubRewriter(ParsedQuery(search_query="跑步鞋"))
    retriever = _StubRetriever([_mk_hit("p_b")])

    branch = MultimodalBranch(
        embedder=embedder, retriever=retriever, cache=cache,
        query_rewriter=rewriter, structured_retriever=None,
        fallback_image_path_resolver=lambda iid: f"/nope/{iid}.jpg",
    )
    result = await branch.handle(
        message="跑步鞋", image_id="lost", history=None, summary=None,
    )

    assert result.image_lost is True
    assert len(result.retrieved) == 1


def test_rerank_promotes_same_tier_candidate_for_brand_exclude():
    parsed = ParsedQuery(
        search_query="手机",
        sub_categories=["智能手机"],
        brands_exclude=["Apple 苹果", "苹果"],
    )
    source = _mk_product(
        "p_digital_001",
        score=0.99,
        brand="Apple 苹果",
        sub_category="智能手机",
        base_price=8999,
    )
    products = [
        _mk_product("p_digital_009", score=0.95, brand="小米", sub_category="智能手机", base_price=6499),
        _mk_product("p_digital_014", score=0.94, brand="OPPO", sub_category="智能手机", base_price=9699),
        _mk_product("p_digital_002", score=0.93, brand="华为", sub_category="智能手机", base_price=6999),
        _mk_product("p_digital_008", score=0.90, brand="小米", sub_category="智能手机", base_price=7499),
    ]

    ranked = rerank_multimodal_products(products, parsed=parsed, source_hint=source)

    assert "p_digital_008" in [p.product_id for p in ranked[:3]]
