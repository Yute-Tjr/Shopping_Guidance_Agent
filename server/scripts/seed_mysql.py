"""把数据集里的 100 件商品灌进 MySQL。

用法（先确保 docker compose up -d 已起，并执行过 python -m app.db.init_db）：

    cd server
    python -m scripts.seed_mysql                  # 增量：跳过已存在的 product_id
    python -m scripts.seed_mysql --truncate       # 先清空 products/skus 再灌
    python -m scripts.seed_mysql --dataset <path> # 用自定义数据集路径

幂等：靠 product_id 主键 upsert，重复跑只会更新内容、不会重复插入。
"""
from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.db.mysql_models import Product, SKU
from app.db.mysql_session import AsyncSessionLocal, engine
from app.rag.chunker import iter_product_files, load_product
from app.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "ecommerce_agent_dataset"


def _product_row(product: dict) -> dict:
    return {
        "product_id": product["product_id"],
        "title": product["title"],
        "brand": product["brand"],
        "category": product["category"],
        "sub_category": product["sub_category"],
        "base_price": Decimal(str(product["base_price"])),
        "image_path": product["image_path"],
        "raw_json": json.dumps(product, ensure_ascii=False),
    }


def _sku_rows(product: dict) -> list[dict]:
    rows: list[dict] = []
    for sku in product.get("skus", []):
        rows.append(
            {
                "sku_id": sku["sku_id"],
                "product_id": product["product_id"],
                "properties": sku.get("properties", {}),
                "price": Decimal(str(sku["price"])),
            }
        )
    return rows


async def _truncate_all() -> None:
    """清空 skus / products（按外键顺序）。"""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            await session.execute(delete(SKU))
            await session.execute(delete(Product))
    logger.info("已清空 products / skus")


async def _upsert_product(session, product: dict) -> None:
    """MySQL 风格的 ON DUPLICATE KEY UPDATE，跑 N 次结果一致。"""
    product_payload = _product_row(product)
    update_cols = {k: product_payload[k] for k in product_payload if k != "product_id"}
    stmt = mysql_insert(Product).values(**product_payload)
    stmt = stmt.on_duplicate_key_update(**update_cols)
    await session.execute(stmt)

    sku_rows = _sku_rows(product)
    if not sku_rows:
        return
    sku_stmt = mysql_insert(SKU).values(sku_rows)
    sku_stmt = sku_stmt.on_duplicate_key_update(
        product_id=sku_stmt.inserted.product_id,
        properties=sku_stmt.inserted.properties,
        price=sku_stmt.inserted.price,
    )
    await session.execute(sku_stmt)


async def seed(dataset_root: Path, truncate: bool) -> tuple[int, int]:
    """灌库主流程；返回 (写入商品数, 写入 SKU 数)。"""
    if truncate:
        await _truncate_all()

    product_count = 0
    sku_count = 0
    async with AsyncSessionLocal() as session:
        async with session.begin():
            for json_path in iter_product_files(dataset_root):
                product = load_product(json_path)
                await _upsert_product(session, product)
                product_count += 1
                sku_count += len(product.get("skus", []))
                if product_count % 20 == 0:
                    logger.info("已处理 %d 件商品...", product_count)

    # 写入完成后再核对一次 row count
    async with AsyncSessionLocal() as session:
        db_products = (await session.execute(select(Product))).scalars().all()
        db_skus = (await session.execute(select(SKU))).scalars().all()

    logger.info(
        "灌库完成：处理 %d 件商品 / %d 个 SKU；DB 当前 products=%d, skus=%d",
        product_count,
        sku_count,
        len(db_products),
        len(db_skus),
    )
    return len(db_products), len(db_skus)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="把数据集灌入 MySQL 的 products / skus 表")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help="数据集根目录（默认指向仓库内的 ecommerce_agent_dataset/）",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="灌库前先清空 products / skus（默认不清，按 product_id upsert）",
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    if not args.dataset.exists():
        raise SystemExit(f"数据集目录不存在：{args.dataset}")
    try:
        await seed(args.dataset, truncate=args.truncate)
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
