"""MySQL 异步连接池封装。

pool_pre_ping=True 是关键：MySQL 默认 wait_timeout=28800s，但连接池里的连接被借出前
若已被服务端断开，pre_ping 会先发一次 SELECT 1 探活，自动剔除死连接，避免业务代码碰到
「MySQL server has gone away」。
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

engine = create_async_engine(
    settings.mysql_dsn,
    pool_size=settings.mysql_pool_size,
    pool_recycle=settings.mysql_pool_recycle,
    pool_pre_ping=True,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖注入：`session: AsyncSession = Depends(get_session)`。"""
    async with AsyncSessionLocal() as session:
        yield session
