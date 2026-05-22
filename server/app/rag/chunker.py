"""按字段切 chunk 的 RAG 入库前置处理。

设计依据 docs/02 §2.1：不做固定 token 切分，按语义字段切——
description / faq / review / title 四类（image 在 Phase 5B 再加）。
每条 chunk 必带 metadata，价格字段强制 float 以便 Milvus 范围过滤。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


CHUNK_TYPE_DESCRIPTION = "description"
CHUNK_TYPE_FAQ = "faq"
CHUNK_TYPE_REVIEW = "review"
CHUNK_TYPE_TITLE = "title"


@dataclass
class Chunk:
    """单条待嵌入的文本片段。"""

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def source_id(self) -> str:
        return self.metadata["source_id"]


def _sku_price_range(product: dict[str, Any]) -> tuple[float, float]:
    """从 skus 列表里取最低/最高价；若无 SKU 退回 base_price。"""
    prices = [float(sku["price"]) for sku in product.get("skus", []) if "price" in sku]
    base = float(product.get("base_price", 0.0))
    if not prices:
        return base, base
    return min(prices), max(prices)


def _common_metadata(product: dict[str, Any]) -> dict[str, Any]:
    """所有 chunk 共用的 metadata 部分（不含 chunk_type / source_id / rating）。"""
    min_p, max_p = _sku_price_range(product)
    return {
        "product_id": product["product_id"],
        "category": product["category"],
        "sub_category": product["sub_category"],
        "brand": product["brand"],
        "base_price": float(product["base_price"]),
        "min_sku_price": min_p,
        "max_sku_price": max_p,
    }


def chunk_product(product: dict[str, Any]) -> list[Chunk]:
    """把一个商品 JSON 切成多条 chunk。

    返回顺序：title → description → faq[0..] → review[0..]
    review 类 chunk 在 metadata 里带 rating，其它类型 rating=0。
    """
    base = _common_metadata(product)
    pid = product["product_id"]
    chunks: list[Chunk] = []

    # 1) title：拼上 brand 和 sub_category，类目召回时更稳
    title_text = f"{product['title']} | 品牌：{product['brand']} | 类目：{product['sub_category']}"
    chunks.append(
        Chunk(
            text=title_text,
            metadata={
                **base,
                "chunk_type": CHUNK_TYPE_TITLE,
                "rating": 0,
                "source_id": f"{pid}#title#0",
            },
        )
    )

    # 2) description：完整保留卖点段落
    knowledge = product.get("rag_knowledge", {}) or {}
    description = (knowledge.get("marketing_description") or "").strip()
    if description:
        # 卖点段落前缀拼一遍 title，缓解中文向量召回偏向标题的问题（docs/02 §10.5）
        chunks.append(
            Chunk(
                text=f"{product['title']}。{description}",
                metadata={
                    **base,
                    "chunk_type": CHUNK_TYPE_DESCRIPTION,
                    "rating": 0,
                    "source_id": f"{pid}#description#0",
                },
            )
        )

    # 3) faq：一条 Q/A 一向量
    for idx, qa in enumerate(knowledge.get("official_faq") or []):
        q = (qa.get("question") or "").strip()
        a = (qa.get("answer") or "").strip()
        if not q and not a:
            continue
        chunks.append(
            Chunk(
                text=f"Q: {q}\nA: {a}",
                metadata={
                    **base,
                    "chunk_type": CHUNK_TYPE_FAQ,
                    "rating": 0,
                    "source_id": f"{pid}#faq#{idx}",
                },
            )
        )

    # 4) review：保留评分用于后续过滤
    for idx, review in enumerate(knowledge.get("user_reviews") or []):
        content = (review.get("content") or "").strip()
        if not content:
            continue
        rating = int(review.get("rating") or 0)
        chunks.append(
            Chunk(
                text=f"评分 {rating}/5：{content}",
                metadata={
                    **base,
                    "chunk_type": CHUNK_TYPE_REVIEW,
                    "rating": rating,
                    "source_id": f"{pid}#review#{idx}",
                },
            )
        )

    return chunks


def iter_product_files(dataset_root: Path) -> Iterable[Path]:
    """遍历数据集目录下所有商品 JSON。"""
    for json_path in sorted(dataset_root.glob("*/data/*.json")):
        yield json_path


def load_product(json_path: Path) -> dict[str, Any]:
    """读取单个商品 JSON。"""
    with json_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def chunk_dataset(dataset_root: Path) -> list[Chunk]:
    """读取整个数据集，返回所有 chunk。仅供脚本和评测使用。"""
    all_chunks: list[Chunk] = []
    for json_path in iter_product_files(dataset_root):
        product = load_product(json_path)
        all_chunks.extend(chunk_product(product))
    return all_chunks
