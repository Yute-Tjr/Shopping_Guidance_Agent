"""商品数据仓库：把 MySQL Product / SKU 翻译成 API 用的 view dict。

Orchestrator hydrate 卡片、/products/{id} 详情页都走这里。
价格使用 DECIMAL → float 转换；image_url 直接拼 settings.static_base_url。
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.db.mysql_models import Product, SKU
from app.db.mysql_session import AsyncSessionLocal
from app.utils.logger import get_logger

logger = get_logger(__name__)


_STATIC_BASE_PLACEHOLDERS = {
    "your-ip-address",
    "http://your-ip-address",
    "https://your-ip-address",
}


def _normalize_static_base_url(base: str | None, *, port: int = 8000) -> str:
    """归一化商品图 host，避免本地联调时把 .env 占位值发给客户端。"""
    fallback = f"http://127.0.0.1:{port}"
    cleaned = (base or "").strip().rstrip("/")
    if not cleaned:
        return fallback
    if cleaned.lower() in _STATIC_BASE_PLACEHOLDERS:
        return fallback
    if "://" not in cleaned:
        cleaned = f"http://{cleaned}"
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return fallback
    if (parsed.hostname or "").lower() == "your-ip-address":
        return fallback
    return cleaned


def _image_url(image_path: str, base: str) -> str:
    """拼成可被 iOS 直接 GET 的完整地址。

    image_path 形如 '1_美妆护肤/images/p_beauty_001_live.jpg'，
    会被挂载在 /static 下；base 是 settings.static_base_url（含协议+host+port）。
    """
    cleaned = (image_path or "").lstrip("/")
    return f"{base.rstrip('/')}/static/{cleaned}"


def _serialize_skus(skus: list[SKU]) -> list[dict]:
    return [
        {
            "sku_id": s.sku_id,
            "properties": s.properties or {},
            "price": float(s.price),
        }
        for s in skus
    ]


class ProductRepository:
    """SSE / REST 共享的商品查询入口。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        *,
        static_base_url: str = "http://127.0.0.1:8000",
    ) -> None:
        self._sf = session_factory or AsyncSessionLocal
        self._base_url = static_base_url

    async def get_card_view(self, product_id: str) -> dict[str, Any] | None:
        """SSE event:product_card 用的精简视图，只包含卡片必须字段。"""
        async with self._sf() as session:
            stmt = (
                select(Product)
                .options(selectinload(Product.skus))
                .where(Product.product_id == product_id)
            )
            result = await session.execute(stmt)
            product = result.scalar_one_or_none()
            if product is None:
                return None
            skus = list(product.skus)
            prices = [float(s.price) for s in skus] or [float(product.base_price)]
            return {
                "product_id": product.product_id,
                "title": product.title,
                "brand": product.brand,
                "category": product.category,
                "image_url": _image_url(product.image_path, self._base_url),
                "price_range": {"min": min(prices), "max": max(prices)},
                "skus": _serialize_skus(skus),
                "reason": "",  # 由 orchestrator 拼上 LLM 给的理由
            }

    async def list_brands(self) -> list[str]:
        """库内所有品牌（去重，按字母序），给 QueryRewriter 注入 LLM 白名单用。

        100 条数据规模下一次查询 ~25 行，启动期跑一次缓存即可，不必走 Redis。
        """
        async with self._sf() as session:
            result = await session.execute(select(Product.brand).distinct())
            rows = [r[0] for r in result.all() if r[0]]
        return sorted(set(rows))

    async def get_detail(self, product_id: str) -> dict[str, Any] | None:
        """详情页用，比 card_view 多一份 raw_json + sub_category。"""
        async with self._sf() as session:
            stmt = (
                select(Product)
                .options(selectinload(Product.skus))
                .where(Product.product_id == product_id)
            )
            result = await session.execute(stmt)
            product = result.scalar_one_or_none()
            if product is None:
                return None
            import json
            try:
                raw = json.loads(product.raw_json) if product.raw_json else None
            except json.JSONDecodeError:
                raw = None
            return {
                "product_id": product.product_id,
                "title": product.title,
                "brand": product.brand,
                "category": product.category,
                "sub_category": product.sub_category,
                "base_price": float(product.base_price),
                "image_url": _image_url(product.image_path, self._base_url),
                "skus": _serialize_skus(list(product.skus)),
                "raw": raw,
            }


_repo_singleton: ProductRepository | None = None


def get_product_repository() -> ProductRepository:
    """FastAPI dependency；按 settings 单例化。"""
    global _repo_singleton
    if _repo_singleton is None:
        from app.config import settings

        base = _normalize_static_base_url(
            getattr(settings, "static_base_url", None),
            port=settings.port,
        )
        _repo_singleton = ProductRepository(static_base_url=base)
    return _repo_singleton
