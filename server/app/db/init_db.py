"""一次性建表脚本：

用法：
    cd server
    python -m app.db.init_db

会按 mysql_models.py 中的模型定义在 MYSQL_DSN 指向的库里创建：
products / skus / cart_items / orders 四张表。已存在则跳过。
"""
from __future__ import annotations

import asyncio

from app.db.mysql_models import Base
from app.db.mysql_session import engine
from app.utils.logger import get_logger

logger = get_logger(__name__)


async def create_all() -> None:
    logger.info("connecting to MySQL: %s", engine.url.render_as_string(hide_password=True))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("all tables created (or already existed): %s", list(Base.metadata.tables.keys()))


def main() -> None:
    asyncio.run(create_all())


if __name__ == "__main__":
    main()
