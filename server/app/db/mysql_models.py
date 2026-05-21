"""SQLAlchemy 2.0 异步 ORM 模型。

设计要点：
- 所有表统一 InnoDB + utf8mb4，避免与 emoji/中文场景下的乱码与排序问题。
- 金额一律 DECIMAL(10, 2)，禁止使用 FLOAT —— FLOAT 序列化往返时会出现 720.0000001
  这类精度漂移，被截图发到群里就会被认为是「AI 编造价格」。
- products / skus 是「商品事实库」，作为防幻觉的唯一真相源；
  cart_items / orders 是 Phase 5A 业务闭环要用到的表，Phase 0 先把表结构建好。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DECIMAL, JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


COMMON_TABLE_ARGS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


class Product(Base):
    __tablename__ = "products"
    __table_args__ = COMMON_TABLE_ARGS

    product_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    brand: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    sub_category: Mapped[str] = mapped_column(String(32), index=True)
    base_price: Mapped[Decimal] = mapped_column(DECIMAL(10, 2))
    image_path: Mapped[str] = mapped_column(String(255))
    raw_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    skus: Mapped[list["SKU"]] = relationship(back_populates="product", cascade="all, delete")


class SKU(Base):
    __tablename__ = "skus"
    __table_args__ = COMMON_TABLE_ARGS

    sku_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    product_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("products.product_id", ondelete="CASCADE"),
        index=True,
    )
    properties: Mapped[dict] = mapped_column(JSON)
    price: Mapped[Decimal] = mapped_column(DECIMAL(10, 2))

    product: Mapped["Product"] = relationship(back_populates="skus")


class CartItem(Base):
    __tablename__ = "cart_items"
    __table_args__ = COMMON_TABLE_ARGS

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[str] = mapped_column(String(64), ForeignKey("products.product_id"))
    sku_id: Mapped[str] = mapped_column(String(64), ForeignKey("skus.sku_id"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = COMMON_TABLE_ARGS

    order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    items_json: Mapped[dict] = mapped_column(JSON)
    address: Mapped[str] = mapped_column(String(255))
    total_price: Mapped[Decimal] = mapped_column(DECIMAL(10, 2))
    status: Mapped[str] = mapped_column(String(16), default="created")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
