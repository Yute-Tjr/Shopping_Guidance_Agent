"""RAG 检索高层封装：query 文本 → 一组聚合好的商品候选。

设计点：
- Phase 2 不做 metadata filter（价格 / 品牌排除），Phase 4 IntentRouter 升级到
  抽 filters 后再传 filter_expr；接口已预留参数；
- Milvus 一条商品会进来多条 chunk（title / desc / faq / review），
  这里按 product_id 聚合，保留**该商品命中的最高分**作为排序依据，
  并把命中的所有 chunk 文本一并带回，Prompt Builder 可用来给 LLM 更丰富上下文；
- RetrievedProduct 故意做成最小数据结构，下游若要 image_url / sku 详情，
  由 orchestrator 通过 MySQL Product 表二次查询补全（防幻觉链路里 MySQL 是真相源）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from app.utils.logger import get_logger

logger = get_logger(__name__)

# 一次 Milvus search 取多少 chunk；100 件 / 1100 chunk 的规模下 50 够用
DEFAULT_TOP_K_CHUNKS = 30
# 聚合后保留多少件商品塞 Prompt；Top-3 是首 token 延迟与召回的折中
DEFAULT_TOP_N_PRODUCTS = 5


@dataclass
class RetrievedProduct:
    """聚合后的商品候选。"""

    product_id: str
    score: float                        # 该商品命中的最高 chunk 相似度
    brand: str
    category: str
    sub_category: str
    base_price: float
    best_chunk_text: str = ""           # 得分最高的 chunk 文本
    supporting_chunks: list[str] = field(default_factory=list)  # 全部命中 chunk 文本
    min_sku_price: float = 0.0
    max_sku_price: float = 0.0
    best_chunk_type: str = ""


class RagRetriever:
    """串联 embedder + vector store + 聚合。"""

    def __init__(self, embedder: Any, store: Any) -> None:
        self.embedder = embedder
        self.store = store

    def search(
        self,
        query: str,
        *,
        top_k_chunks: int = DEFAULT_TOP_K_CHUNKS,
        top_n_products: int = DEFAULT_TOP_N_PRODUCTS,
        filter_expr: Optional[str] = None,
    ) -> list[RetrievedProduct]:
        if not query or not query.strip():
            return []
        qvec = self.embedder.embed_one(query)
        hits = self.store.search(
            query_vector=qvec,
            top_k=top_k_chunks,
            filter_expr=filter_expr,
        )
        if not hits:
            return []
        return _aggregate(hits, top_n_products)


def _entity_of(hit: dict) -> dict[str, Any]:
    """兼容 MilvusClient 不同版本：有的把字段平铺，有的塞在 entity 子 dict 里。"""
    if isinstance(hit, dict) and isinstance(hit.get("entity"), dict):
        return hit["entity"]
    return hit  # 平铺情况


def _score_of(hit: dict) -> float:
    """IP metric 下 distance 字段即是内积值（越大越相似）。"""
    if "distance" in hit:
        return float(hit["distance"])
    if "score" in hit:
        return float(hit["score"])
    return 0.0


def _aggregate(hits: Sequence[dict], top_n_products: int) -> list[RetrievedProduct]:
    """把 chunk 命中按 product_id 聚合。

    Milvus search 输出已按分数从高到低；首次见到的 chunk 即"最佳 chunk"。
    """
    by_pid: dict[str, RetrievedProduct] = {}
    for hit in hits:
        ent = _entity_of(hit)
        pid = ent.get("product_id")
        if not pid:
            continue
        score = _score_of(hit)
        text = ent.get("text", "") or ""
        chunk_type = ent.get("chunk_type", "") or ""

        if pid not in by_pid:
            by_pid[pid] = RetrievedProduct(
                product_id=pid,
                score=score,
                brand=ent.get("brand", "") or "",
                category=ent.get("category", "") or "",
                sub_category=ent.get("sub_category", "") or "",
                base_price=float(ent.get("base_price", 0.0) or 0.0),
                min_sku_price=float(ent.get("min_sku_price", 0.0) or 0.0),
                max_sku_price=float(ent.get("max_sku_price", 0.0) or 0.0),
                best_chunk_text=text,
                best_chunk_type=chunk_type,
                supporting_chunks=[text] if text else [],
            )
        else:
            prod = by_pid[pid]
            if text and text not in prod.supporting_chunks:
                prod.supporting_chunks.append(text)
            # score 已经是降序进来，这里只记当前商品额外出现，不更新 score

    # by_pid 的插入顺序即按分数降序（dict 保序）
    products = list(by_pid.values())[:top_n_products]
    return products


def build_retriever_from_settings() -> RagRetriever:
    """脚本 / API 复用入口。"""
    from app.config import settings
    from app.rag.embedder import build_embedder_from_settings
    from app.rag.milvus_store import ProductTextStore

    embedder = build_embedder_from_settings()
    store = ProductTextStore(db_path=settings.milvus_db_path, dim=embedder.dim)
    store.ensure_collection()
    return RagRetriever(embedder=embedder, store=store)
