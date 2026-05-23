"""Milvus Lite 文本向量存储。

依据 docs/02 §4：
- collection 名 `products_text`，主键 id 自增。
- 文本向量字段 + 9 个 metadata 字段；价格字段保持 FLOAT 以便 Milvus 范围过滤。
- 100 件商品 / ~1000 chunk 的规模，索引用 `FLAT`（暴搜，召回精度天花板，省去 IVF 训练）。
  规模上 10k+ 再切到 IVF_FLAT。
- metric `IP`（内积），配合 Embedder 强制 L2 归一化等价余弦相似度。
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from pymilvus import DataType, MilvusClient

from app.rag.chunker import Chunk
from app.utils.logger import get_logger

logger = get_logger(__name__)

COLLECTION_NAME = "products_text"

# 与 docs/02 §4.2 schema 对齐
_VARCHAR_PRODUCT_ID = 64
_VARCHAR_CHUNK_TYPE = 16
_VARCHAR_TEXT = 2000
_VARCHAR_CATEGORY = 32
_VARCHAR_SUB_CATEGORY = 32
_VARCHAR_BRAND = 64
_VARCHAR_SOURCE_ID = 128


class ProductTextStore:
    """对 Milvus Lite 的 products_text collection 做最小封装。"""

    def __init__(self, db_path: str | Path, dim: int) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.dim = dim
        self.client = MilvusClient(uri=str(self.db_path))

    # ---------- schema / lifecycle ----------

    def _build_schema(self):
        schema = self.client.create_schema(auto_id=True, enable_dynamic_field=False)
        schema.add_field("id", DataType.INT64, is_primary=True)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=self.dim)
        schema.add_field("product_id", DataType.VARCHAR, max_length=_VARCHAR_PRODUCT_ID)
        schema.add_field("chunk_type", DataType.VARCHAR, max_length=_VARCHAR_CHUNK_TYPE)
        schema.add_field("text", DataType.VARCHAR, max_length=_VARCHAR_TEXT)
        schema.add_field("category", DataType.VARCHAR, max_length=_VARCHAR_CATEGORY)
        schema.add_field("sub_category", DataType.VARCHAR, max_length=_VARCHAR_SUB_CATEGORY)
        schema.add_field("brand", DataType.VARCHAR, max_length=_VARCHAR_BRAND)
        schema.add_field("base_price", DataType.FLOAT)
        schema.add_field("min_sku_price", DataType.FLOAT)
        schema.add_field("max_sku_price", DataType.FLOAT)
        schema.add_field("rating", DataType.INT8)
        schema.add_field("source_id", DataType.VARCHAR, max_length=_VARCHAR_SOURCE_ID)
        return schema

    def _build_index_params(self):
        params = self.client.prepare_index_params()
        params.add_index(
            field_name="vector",
            index_type="FLAT",
            metric_type="IP",
        )
        return params

    def ensure_collection(self, rebuild: bool = False) -> None:
        """幂等创建 collection。rebuild=True 时先 drop 旧的。"""
        exists = self.client.has_collection(COLLECTION_NAME)
        if exists and rebuild:
            self.client.drop_collection(COLLECTION_NAME)
            logger.info("已 drop 旧 collection：%s", COLLECTION_NAME)
            exists = False
        if not exists:
            self.client.create_collection(
                collection_name=COLLECTION_NAME,
                schema=self._build_schema(),
                index_params=self._build_index_params(),
            )
            logger.info("已创建 collection：%s（dim=%d, metric=IP, index=FLAT）", COLLECTION_NAME, self.dim)
        self.client.load_collection(COLLECTION_NAME)

    def drop(self) -> None:
        if self.client.has_collection(COLLECTION_NAME):
            self.client.drop_collection(COLLECTION_NAME)

    # ---------- insert ----------

    def insert_chunks(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> int:
        """成对插入 chunk 与向量。返回写入条数。"""
        if len(chunks) != len(vectors):
            raise ValueError(f"chunks 与 vectors 数量不匹配：{len(chunks)} vs {len(vectors)}")
        if not chunks:
            return 0

        rows: list[dict[str, Any]] = []
        for chunk, vec in zip(chunks, vectors):
            meta = chunk.metadata
            rows.append(
                {
                    "vector": list(vec),
                    "product_id": meta["product_id"],
                    "chunk_type": meta["chunk_type"],
                    "text": chunk.text[:_VARCHAR_TEXT],
                    "category": meta["category"],
                    "sub_category": meta["sub_category"],
                    "brand": meta["brand"],
                    "base_price": float(meta["base_price"]),
                    "min_sku_price": float(meta["min_sku_price"]),
                    "max_sku_price": float(meta["max_sku_price"]),
                    "rating": int(meta.get("rating", 0)),
                    "source_id": meta["source_id"],
                }
            )

        result = self.client.insert(collection_name=COLLECTION_NAME, data=rows)
        return result.get("insert_count", len(rows))

    # ---------- query ----------

    def count(self) -> int:
        stats = self.client.get_collection_stats(COLLECTION_NAME)
        return int(stats.get("row_count", 0))

    def search(
        self,
        query_vector: Sequence[float],
        top_k: int = 20,
        filter_expr: str | None = None,
        output_fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """向量检索；output_fields 默认带出所有 metadata 字段。"""
        if output_fields is None:
            output_fields = [
                "product_id",
                "chunk_type",
                "text",
                "category",
                "sub_category",
                "brand",
                "base_price",
                "min_sku_price",
                "max_sku_price",
                "rating",
                "source_id",
            ]
        kwargs: dict[str, Any] = {
            "collection_name": COLLECTION_NAME,
            "data": [list(query_vector)],
            "limit": top_k,
            "output_fields": output_fields,
            "search_params": {"metric_type": "IP"},
        }
        if filter_expr:
            kwargs["filter"] = filter_expr
        results = self.client.search(**kwargs)
        # MilvusClient.search 返回 list[list[hit]]，我们只发了 1 个 query，取第 0 个
        return list(results[0]) if results else []


def chunk_to_row(chunk: Chunk) -> dict[str, Any]:
    """工具函数：单独把一个 Chunk 转成 Milvus 行（便于调试 / 测试）。"""
    return {"text": chunk.text, **asdict(chunk).get("metadata", chunk.metadata)}
