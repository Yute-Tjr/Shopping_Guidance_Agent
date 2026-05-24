"""GET /api/v1/products/{product_id} —— 详情页用。

iOS 端点击商品卡片 push 进 ProductDetailView 时调；
也是 Phase 2 验收要求的兜底入口。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_product_repo
from app.db.product_repo import ProductRepository

router = APIRouter(prefix="/products", tags=["products"])


@router.get("/{product_id}")
async def get_product(
    product_id: str,
    repo: ProductRepository = Depends(get_product_repo),
) -> dict:
    detail = await repo.get_detail(product_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"product not found: {product_id}")
    return detail
