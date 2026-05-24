"""商品详情 / 列表的响应模型（用于 /api/v1/products 路由）。"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class SkuDetail(BaseModel):
    sku_id: str
    properties: dict[str, Any]
    price: Decimal


class ProductDetail(BaseModel):
    """详情页用：含完整 SKU + 拼好的 image_url。"""

    product_id: str
    title: str
    brand: str
    category: str
    sub_category: str
    base_price: Decimal
    image_url: str
    skus: list[SkuDetail] = Field(default_factory=list)
    # 详情页可能要展示 marketing_description / faq / review，
    # 保留 raw_json 让前端按需取（Phase 2 不强制使用）。
    raw: dict[str, Any] | None = None


class ProductListItem(BaseModel):
    product_id: str
    title: str
    brand: str
    category: str
    base_price: Decimal
    image_url: str
