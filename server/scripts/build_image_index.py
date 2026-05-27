"""Phase 5 增量索引：把数据集每件商品的 _live.jpg 编码成 image chunk 写入 Milvus。

幂等策略：先按 source_id 删旧 image chunk 再插，支持多次跑。
chunk_type='image'，text 字段存图片相对路径（占位，prompt 不消费）。

用法：

    cd server
    python -m scripts.build_image_index                    # 全量
    python -m scripts.build_image_index --limit 3          # 调试用
    python -m scripts.build_image_index --rebuild          # 等价于「先清空全部 image chunk 再插」
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Iterable

from app.config import settings
from app.rag.chunker import iter_product_files, load_product
from app.rag.embedder import build_embedder_from_settings
from app.rag.milvus_store import COLLECTION_NAME, ProductTextStore
from app.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "ecommerce_agent_dataset"


def find_image_path(dataset_root: Path, product_id: str, category_dir: Path) -> Path | None:
    """从分类目录的 images/ 下找 <pid>_live.jpg。"""
    p = category_dir / "images" / f"{product_id}_live.jpg"
    return p if p.exists() else None


def collect_image_rows(dataset_root: Path, limit: int | None) -> list[dict]:
    """遍历数据集，每件商品产出一个 image chunk row（不含向量）。"""
    rows: list[dict] = []
    count = 0
    for json_path in iter_product_files(dataset_root):
        if limit is not None and count >= limit:
            break
        product = load_product(json_path)
        category_dir = json_path.parent.parent  # data/p_xxx.json → 分类根目录
        img = find_image_path(dataset_root, product["product_id"], category_dir)
        if img is None:
            logger.warning("跳过：商品 %s 找不到 _live.jpg", product["product_id"])
            continue
        # 与现有 Chunk metadata 对齐
        skus = product.get("skus", []) or []
        sku_prices = [float(s.get("price", 0.0)) for s in skus if s.get("price")]
        base_price = float(product.get("base_price", 0.0) or (sku_prices[0] if sku_prices else 0.0))
        rows.append({
            "product_id": product["product_id"],
            "image_path": str(img),
            "text": str(img.relative_to(dataset_root)),  # 占位文本：相对路径方便人肉 debug
            "chunk_type": "image",
            "category": product.get("category", "") or "",
            "sub_category": product.get("sub_category", "") or "",
            "brand": product.get("brand", "") or "",
            "base_price": base_price,
            "min_sku_price": min(sku_prices) if sku_prices else base_price,
            "max_sku_price": max(sku_prices) if sku_prices else base_price,
            "rating": int(product.get("rating", 0) or 0),
            "source_id": f"{product['product_id']}#image#0",
        })
        count += 1
    logger.info("收集 image chunk %d 条", len(rows))
    return rows


def delete_existing_image_chunks(store: ProductTextStore, source_ids: Iterable[str]) -> int:
    """删旧 image chunk（按 source_id），返回删除条数。

    Milvus Lite 支持按 filter 删，但不支持 IN 大列表传太多 ID。这里分 50 一批。
    """
    ids = list(source_ids)
    if not ids:
        return 0
    total = 0
    batch = 50
    for i in range(0, len(ids), batch):
        sub = ids[i:i + batch]
        quoted = ", ".join(f'"{sid}"' for sid in sub)
        filter_expr = f'source_id in [{quoted}]'
        result = store.client.delete(collection_name=COLLECTION_NAME, filter=filter_expr)
        total += result.get("delete_count", 0) if isinstance(result, dict) else 0
    return total


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 5 增量灌入 image chunk")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 件（调试用）")
    parser.add_argument("--rebuild", action="store_true", help="先清空所有 image chunk 再灌")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if not args.dataset.exists():
        raise SystemExit(f"数据集目录不存在：{args.dataset}")

    rows = collect_image_rows(args.dataset, args.limit)
    if not rows:
        logger.warning("没有图片可入库，退出。")
        return

    embedder = build_embedder_from_settings()
    dim = embedder.dim
    logger.info("Embedding model=%s, dim=%d", embedder.model, dim)

    store = ProductTextStore(db_path=settings.milvus_db_path, dim=dim)
    if not store.client.has_collection(COLLECTION_NAME):
        raise SystemExit(f"collection={COLLECTION_NAME} 不存在，请先跑 build_index.py 建文本索引")
    store.client.load_collection(COLLECTION_NAME)

    # 删旧 image chunk
    if args.rebuild:
        deleted = delete_existing_image_chunks(
            store, source_ids=(r["source_id"] for r in rows),
        )
        logger.info("--rebuild：已删除旧 image chunk %d 条", deleted)
    else:
        # 默认行为：按 source_id 删该 row 对应的旧条目，让脚本幂等
        deleted = delete_existing_image_chunks(
            store, source_ids=(r["source_id"] for r in rows),
        )
        if deleted:
            logger.info("幂等删除旧 image chunk %d 条（按 source_id 对应）", deleted)

    # 串行调 embed_image（多线程并发要复用 embedder 的 ThreadPoolExecutor，
    # 但 build_image_index 是离线脚本，等几分钟可接受）
    started = time.time()
    vectors: list[list[float]] = []
    for i, row in enumerate(rows, 1):
        try:
            vec = embedder.embed_image(row["image_path"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("商品 %s 图编码失败：%s（跳过）", row["product_id"], exc)
            continue
        vectors.append(vec)
        if i % 10 == 0:
            logger.info("  ...已编码 %d / %d", i, len(rows))
    logger.info("图 embedding 完成，耗时 %.1fs，成功 %d / %d", time.time() - started, len(vectors), len(rows))

    # 写 Milvus（直接构造 row 字典，不走 chunker 的 Chunk 对象）
    if len(vectors) != len(rows):
        # 跳过失败的；同步对齐 rows
        rows = [r for r, v in zip(rows, vectors + [None] * (len(rows) - len(vectors))) if v is not None][:len(vectors)]

    insert_rows: list[dict] = []
    for row, vec in zip(rows, vectors):
        insert_rows.append({
            "vector": list(vec),
            "product_id": row["product_id"],
            "chunk_type": row["chunk_type"],
            "text": row["text"][:2000],
            "category": row["category"],
            "sub_category": row["sub_category"],
            "brand": row["brand"],
            "base_price": row["base_price"],
            "min_sku_price": row["min_sku_price"],
            "max_sku_price": row["max_sku_price"],
            "rating": row["rating"],
            "source_id": row["source_id"],
        })
    result = store.client.insert(collection_name=COLLECTION_NAME, data=insert_rows)
    inserted = result.get("insert_count", len(insert_rows)) if isinstance(result, dict) else len(insert_rows)
    logger.info("写入 Milvus 完成：%d 条 image chunk → collection=%s", inserted, COLLECTION_NAME)

    # Sanity check：用第一张图自己当 query，应该 Top-1 命中自身
    probe = store.search(vectors[0], top_k=3, filter_expr='chunk_type == "image"')
    logger.info("Sanity search Top-3（chunk_type=image）：")
    for hit in probe:
        ent = hit.get("entity", {})
        logger.info(
            "  score=%.4f  pid=%s  text=%s",
            hit.get("distance", 0.0),
            ent.get("product_id"),
            (ent.get("text") or "")[:60],
        )


if __name__ == "__main__":
    main()
