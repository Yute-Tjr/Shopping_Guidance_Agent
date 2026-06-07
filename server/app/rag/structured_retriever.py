"""结构化检索路径（Phase 4 收尾：SQL fallback）。

为什么需要这一层：
RAG 默认路径走 embedding → Milvus 向量召回 + scalar filter。但当用户的 query
**完全是结构化筛选**（"100-200 之间" / "300 元以下" / "不要日系品牌"）时，
search_query 退化到"之间""以下"等无意义 token —— 这些 token 的 embedding
跟所有商品 chunk 都不相关，向量召回排序近似随机。即使 scalar filter 把价格
过滤对了，LLM 拿到的 Top-K 仍是品类乱序的，最终输出"未找到匹配"。

解法：当 ParsedQuery 满足"search_query 退化 + 至少一个 filter"时，**绕过向量
召回**，直接用 SQL 在 MySQL 里按结构化条件查 product_id，再走原 hydrate 链路。

走 SQL 而不是 Milvus 的优势：
- 100% 准确的范围/枚举过滤（embedding 永远做不到的）；
- 无视 query 文本质量；
- MySQL InnoDB 索引在 100-1k 规模上 < 10ms；
- 直接拿到完整 Product+SKU，hydrate 阶段省一次查询。

劣势：失去语义召回（无法识别"控油的洗面奶"中的"控油"），所以**仅在 query
退化的兜底场景启用**，正常 query 仍走向量召回。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.mysql_models import Product, SKU
from app.db.mysql_session import AsyncSessionLocal
from app.rag.retriever import RetrievedProduct
from app.utils.logger import get_logger

logger = get_logger(__name__)


class StructuredRetriever:
    """SQL 路径：按 filter 直查 MySQL，跳过向量召回。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None):
        self._sf = session_factory or AsyncSessionLocal

    async def search(
        self,
        *,
        price_min: Optional[float] = None,
        price_max: Optional[float] = None,
        categories: Optional[list[str]] = None,
        sub_categories: Optional[list[str]] = None,
        brands_include: Optional[list[str]] = None,
        brands_exclude: Optional[list[str]] = None,
        limit: int = 5,
    ) -> list[RetrievedProduct]:
        """按结构化条件查商品。任何条件都为空时返回空列表（拒绝"无差别拉取"）。

        价格判定与 Milvus 一致：
        - price_min：max(SKU.price) >= price_min（最贵 SKU 至少达到下限）
        - price_max：min(SKU.price) <= price_max（最便宜 SKU 至少不超过上限）
        """
        has_any = (
            price_min is not None or price_max is not None or
            categories or sub_categories or brands_include or brands_exclude
        )
        if not has_any:
            logger.warning("StructuredRetriever 收到空 filter，拒绝执行")
            return []

        async with self._sf() as session:
            stmt = select(Product)
            conds = []

            if categories:
                conds.append(Product.category.in_(categories))
            if sub_categories:
                conds.append(Product.sub_category.in_(sub_categories))
            if brands_include:
                conds.append(Product.brand.in_(brands_include))
            if brands_exclude:
                conds.append(~Product.brand.in_(brands_exclude))

            if conds:
                stmt = stmt.where(and_(*conds))

            # 价格条件用 JOIN SKU 来判断 min/max
            if price_min is not None or price_max is not None:
                sku_subq = select(SKU.product_id).distinct()
                if price_min is not None and price_max is not None:
                    # 100-200 区间：存在至少一个 SKU 落在 [price_min, price_max] 内
                    sku_subq = sku_subq.where(
                        SKU.price >= price_min, SKU.price <= price_max
                    )
                elif price_min is not None:
                    sku_subq = sku_subq.where(SKU.price >= price_min)
                else:
                    sku_subq = sku_subq.where(SKU.price <= price_max)
                stmt = stmt.where(Product.product_id.in_(sku_subq))

            stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            products = list(result.scalars().all())

        if not products:
            logger.info(
                "StructuredRetriever 命中 0 条 (price_min=%s, price_max=%s, "
                "categories=%s, sub_categories=%s, brands_exclude=%s)",
                price_min, price_max, categories, sub_categories, brands_exclude,
            )

        # 每件商品取出价格区间，转 RetrievedProduct
        out: list[RetrievedProduct] = []
        async with self._sf() as session:
            for p in products:
                # 拉 SKU 价格区间
                sku_result = await session.execute(
                    select(SKU.price).where(SKU.product_id == p.product_id)
                )
                prices = [float(row[0]) for row in sku_result.all()]
                min_p = min(prices) if prices else float(p.base_price)
                max_p = max(prices) if prices else float(p.base_price)
                out.append(
                    RetrievedProduct(
                        product_id=p.product_id,
                        score=1.0,  # SQL 路径无相似度，统一打满让排序保持入库顺序
                        brand=p.brand or "",
                        category=p.category or "",
                        sub_category=p.sub_category or "",
                        base_price=float(p.base_price),
                        min_sku_price=min_p,
                        max_sku_price=max_p,
                        best_chunk_text=p.title or "",
                        best_chunk_type="title",
                        supporting_chunks=[p.title or ""] if p.title else [],
                    )
                )
        return out


def build_structured_retriever() -> StructuredRetriever:
    return StructuredRetriever()


# 检测 search_query 是否退化（不值得喂 embedding）的工具函数。
# orchestrator 用它决定要不要走 SQL fallback。
_DEGRADED_TOKENS: set[str] = {
    "之间", "以下", "以上", "以内", "之内", "内", "的",
    "便宜", "贵", "便宜一点", "贵一点", "改成", "调整",
}

# original_message 含这些 token 且短句时，认为是「纯结构化承接 query」。
# 短句 + 这些 signal 一起判定，避免误伤"不要含酒精的防晒霜"（长句）。
_STRUCTURAL_SIGNALS: tuple[str, ...] = (
    "以下", "以上", "以内", "之间", "之内",
    "便宜", "贵",
    "改成", "改为", "调整成", "调成", "改到",
    "不要", "不是", "排除", "除了",
)


def is_search_query_degraded(
    search_query: str,
    *,
    original_message: str = "",
) -> bool:
    """判定是否该绕过 embedding 走 SQL fallback。

    两条独立通道任一触发就返 True：

    1. **search_query 自身退化**：剥空格/数字/连字符后 ≤ 2 字符 或落在
       _DEGRADED_TOKENS 内。覆盖首轮 `300 元以下`、`100-200 之间` 这类 query
       —— 此时 search_query 被规则剥到只剩"之间""以下"。

    2. **original_message 是结构化承接短句**：原 message ≤ 15 字符且含
       结构化信号词（"以下""不要""改成"等）。覆盖多轮场景里 history 补全
       后 search_query 看似很长但本质上还是承接 query（e.g. "改成 100-200
       之间" → search_query="想买保湿精华 300 元以下 改成 之间"——长但
       语义混乱，LLM 看到也会困惑，直接 SQL 兜底更靠谱）。
    """
    # Path 1: search_query 自身退化
    sq = (search_query or "").strip()
    if not sq:
        return True
    stripped = "".join(ch for ch in sq if not (ch.isspace() or ch.isdigit() or ch in "-~."))
    if len(stripped) <= 2:
        return True
    if stripped in _DEGRADED_TOKENS:
        return True

    # Path 2: original_message 是结构化承接短句
    msg = (original_message or "").strip()
    if msg and len(msg) <= 15:
        if any(sig in msg for sig in _STRUCTURAL_SIGNALS):
            return True

    return False
