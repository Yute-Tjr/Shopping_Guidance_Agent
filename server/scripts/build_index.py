"""把数据集 chunk 化 → Doubao Embedding → 写 Milvus Lite。

用法：

    cd server
    python -m scripts.build_index                     # 增量：仅当 collection 不存在时建
    python -m scripts.build_index --rebuild           # 先 drop 再灌（典型用法）
    python -m scripts.build_index --limit 5           # 只跑前 5 件商品（联调用）
    python -m scripts.build_index --dry-run           # 只跑 chunker，不调 API、不写 Milvus

幂等：默认行为不重复插入（增量模式假设 collection 不存在；要更新数据请用 --rebuild）。
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from app.config import settings
from app.rag.chunker import Chunk, chunk_product, iter_product_files, load_product
from app.rag.embedder import build_embedder_from_settings
from app.rag.milvus_store import COLLECTION_NAME, ProductTextStore
from app.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "ecommerce_agent_dataset"


def collect_chunks(dataset_root: Path, limit: int | None) -> list[Chunk]:
    """读取数据集，返回扁平 chunk 列表。"""
    all_chunks: list[Chunk] = []
    product_count = 0
    for json_path in iter_product_files(dataset_root):
        if limit is not None and product_count >= limit:
            break
        product = load_product(json_path)
        all_chunks.extend(chunk_product(product))
        product_count += 1
    logger.info("读取 %d 件商品，切出 %d 条 chunk", product_count, len(all_chunks))
    return all_chunks


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="一键灌 Milvus Lite 向量索引")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--rebuild", action="store_true", help="先 drop 旧 collection 再灌")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 件商品（联调）")
    parser.add_argument("--dry-run", action="store_true", help="只跑 chunker，不调 API、不写 Milvus")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if not args.dataset.exists():
        raise SystemExit(f"数据集目录不存在：{args.dataset}")

    chunks = collect_chunks(args.dataset, args.limit)
    if args.dry_run:
        logger.info("--dry-run：跳过 Embedding / Milvus 步骤。chunk_type 分布：%s", _chunk_type_histogram(chunks))
        return
    if not chunks:
        logger.warning("没有 chunk 可处理，退出。")
        return

    embedder = build_embedder_from_settings()
    # 主动触发一次 dim 探测，提早暴露模型 / 鉴权问题，避免错误延后到批量调用时
    dim = embedder.dim
    logger.info("Embedding model=%s, dim=%d", embedder.model, dim)

    store = ProductTextStore(db_path=settings.milvus_db_path, dim=dim)
    store.ensure_collection(rebuild=args.rebuild)

    started = time.time()
    texts = [c.text for c in chunks]
    logger.info("开始 embedding：%d 条文本（并发 %d 路）...", len(texts), embedder.concurrency)
    vectors = embedder.embed_batch(texts)
    logger.info("Embedding 完成，耗时 %.1fs", time.time() - started)

    inserted = store.insert_chunks(chunks, vectors)
    logger.info("写入 Milvus 完成：%d 条 → collection=%s", inserted, COLLECTION_NAME)

    # 写完做一次 sanity check：跑一个最简单的查询，确认能查回来
    probe_vec = vectors[0]
    hits = store.search(probe_vec, top_k=3)
    logger.info("Sanity search Top-3：")
    for hit in hits:
        entity = hit.get("entity", {})
        logger.info(
            "  score=%.4f  pid=%s  type=%s  text=%s",
            hit.get("distance", 0.0),
            entity.get("product_id"),
            entity.get("chunk_type"),
            (entity.get("text") or "")[:50],
        )


def _chunk_type_histogram(chunks: list[Chunk]) -> dict[str, int]:
    hist: dict[str, int] = {}
    for c in chunks:
        t = c.metadata["chunk_type"]
        hist[t] = hist.get(t, 0) + 1
    return hist


if __name__ == "__main__":
    main()
