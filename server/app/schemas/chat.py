"""Chat 接口的 Pydantic 模型与 SSE 事件契约。

字段与 docs/03 §3.2 严格对齐：iOS 端用 JSONDecoder.keyDecodingStrategy
= .convertFromSnakeCase 直接解析，禁止后续随意改字段名。
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """POST /api/v1/chat/stream 入参。"""

    session_id: Optional[str] = Field(default=None, description="null 表示新会话")
    message: str = Field(min_length=1, max_length=2000)
    image_id: Optional[str] = None
    user_id: Optional[str] = None


# ---- SSE 事件 payload schemas ----
# 这些 model 只是为了在生产代码里做类型提示和单测断言，
# 实际 SSE 帧用 EventSourceResponse 序列化 dict 即可。


class SessionEvent(BaseModel):
    session_id: str


class StatusEvent(BaseModel):
    stage: Literal["parsing", "retrieving", "generating", "done"]


class TokenEvent(BaseModel):
    text: str


class PriceRange(BaseModel):
    min: float
    max: float


class SkuView(BaseModel):
    """SSE 卡片里嵌的精简 SKU。"""

    sku_id: str
    properties: dict[str, Any]
    price: float


class ProductCardEvent(BaseModel):
    """event: product_card 的 data；product_id 必须来自检索结果。"""

    product_id: str
    title: str
    brand: str
    category: str
    image_url: str
    price_range: PriceRange
    skus: list[SkuView] = Field(default_factory=list)
    reason: str = Field(max_length=120, description="≤ 30 字推荐理由，超出截断")


class ClarifyEvent(BaseModel):
    question: str
    options: list[str] = Field(default_factory=list)


class ErrorEvent(BaseModel):
    code: str
    message: str


class DoneEvent(BaseModel):
    finish_reason: Literal["stop", "length", "error"] = "stop"
    tokens: Optional[dict[str, int]] = None
