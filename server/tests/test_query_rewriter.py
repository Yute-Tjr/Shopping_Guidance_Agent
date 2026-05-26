"""QueryRewriter 单测：规则路径、LLM 兜底、filter expr 拼接。

不打真实 Ark API；用 fake LLM stub 替换 chat_json 模拟 JSON 抽取结果。
"""
from __future__ import annotations

import pytest

from app.agent.query_rewriter import (
    QueryRewriter,
    ParsedQuery,
    build_query_rewriter,
)


# ---------- to_filter_expr ----------


def test_filter_expr_none_when_empty():
    pq = ParsedQuery(search_query="洗面奶")
    assert pq.to_filter_expr() is None


def test_filter_expr_price_only():
    pq = ParsedQuery(search_query="耳机", price_max=200.0)
    assert pq.to_filter_expr() == "min_sku_price <= 200"


def test_filter_expr_price_range():
    pq = ParsedQuery(search_query="洁面", price_min=50, price_max=120)
    expr = pq.to_filter_expr()
    assert expr is not None
    assert "min_sku_price <= 120" in expr
    assert "max_sku_price >= 50" in expr


def test_filter_expr_category_and_brand_exclude():
    pq = ParsedQuery(
        search_query="跑鞋",
        categories=["服饰运动"],
        brands_exclude=["耐克"],
    )
    expr = pq.to_filter_expr()
    assert expr == 'category in ["服饰运动"] and brand not in ["耐克"]'


def test_filter_expr_escapes_double_quote_in_brand():
    pq = ParsedQuery(search_query="x", brands_exclude=['Anomaly"Brand'])
    expr = pq.to_filter_expr() or ""
    assert '\\"' in expr  # 内部双引号被转义


# ---------- 规则路径 ----------


@pytest.mark.asyncio
async def test_rules_price_max_with_yuan_suffix():
    rw = build_query_rewriter()  # 无 LLM、无品牌
    pq = await rw.parse("300元以下的防晒霜")
    assert pq.price_max == 300
    assert pq.price_min is None
    assert "防晒霜" in pq.search_query
    # 价格关键词应该从 search_query 里被剥掉
    assert "300" not in pq.search_query


@pytest.mark.asyncio
async def test_rules_price_max_budget_prefix():
    rw = build_query_rewriter()
    pq = await rw.parse("预算500 跑鞋")
    assert pq.price_max == 500


@pytest.mark.asyncio
async def test_rules_price_range_dash():
    rw = build_query_rewriter()
    pq = await rw.parse("100-200元的运动T恤")
    assert pq.price_min == 100
    assert pq.price_max == 200


@pytest.mark.asyncio
async def test_rules_price_range_to():
    rw = build_query_rewriter()
    pq = await rw.parse("100到200元的零食")
    assert pq.price_min == 100
    assert pq.price_max == 200


@pytest.mark.asyncio
async def test_rules_brand_exclude_with_whitelist():
    rw = build_query_rewriter(known_brands=["Apple", "耐克", "可口可乐"])
    pq = await rw.parse("不要 Apple 的手机")
    assert "Apple" in pq.brands_exclude


@pytest.mark.asyncio
async def test_rules_brand_exclude_chinese_brand():
    rw = build_query_rewriter(known_brands=["耐克", "Adidas"])
    pq = await rw.parse("不是耐克的跑鞋")
    assert "耐克" in pq.brands_exclude


@pytest.mark.asyncio
async def test_rules_brand_exclude_ignored_when_not_in_whitelist():
    """规则匹配到「不要 XXX」但 XXX 不在已知品牌里，不能误填 brands_exclude。"""
    rw = build_query_rewriter(known_brands=["Apple"])
    pq = await rw.parse("不要含酒精的防晒霜")
    assert pq.brands_exclude == []


@pytest.mark.asyncio
async def test_rules_categories_alias_hit():
    rw = build_query_rewriter()
    pq = await rw.parse("推荐一款笔记本")
    assert "数码电子" in pq.categories


# ---------- LLM 兜底 ----------


class _FakeLLM:
    def __init__(self, payload: dict | None = None, raise_exc: Exception | None = None) -> None:
        self.payload = payload or {}
        self.raise_exc = raise_exc
        self.calls = 0

    async def chat_json(self, messages, **_kw):
        self.calls += 1
        if self.raise_exc:
            raise self.raise_exc
        return self.payload


@pytest.mark.asyncio
async def test_llm_called_for_geographic_hints():
    """「国产」/「日系」等地域语义必须走 LLM 抽取具体品牌列表。"""
    fake = _FakeLLM(payload={
        "search_query": "旗舰手机",
        "price_min": None,
        "price_max": None,
        "categories": ["数码电子"],
        "brands_include": [],
        "brands_exclude": ["Apple", "Sony"],
    })
    rw = build_query_rewriter(llm=fake, known_brands=["Apple", "Sony", "华为", "小米"])
    pq = await rw.parse("国产旗舰手机推荐")
    assert fake.calls == 1
    assert "Apple" in pq.brands_exclude
    assert "Sony" in pq.brands_exclude


@pytest.mark.asyncio
async def test_llm_skipped_when_rules_enough():
    """普通推荐 query 规则就够，不再调 LLM。"""
    fake = _FakeLLM(payload={})
    rw = build_query_rewriter(llm=fake, known_brands=["Apple"])
    await rw.parse("推荐一款洗面奶")
    assert fake.calls == 0


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_rules():
    """LLM 抛异常不能让 rewriter 挂；规则结果照样返回。"""
    fake = _FakeLLM(raise_exc=TimeoutError("ark down"))
    rw = build_query_rewriter(llm=fake, known_brands=["Apple"])
    pq = await rw.parse("300元以下的国产手机")
    # 价格规则能拿
    assert pq.price_max == 300
    # LLM 挂了，brands_exclude 为空，但服务不崩
    assert isinstance(pq.brands_exclude, list)


@pytest.mark.asyncio
async def test_llm_brand_outside_whitelist_dropped():
    """LLM 编造的品牌（不在 known_brands 里）必须被丢弃，防幻觉。"""
    fake = _FakeLLM(payload={
        "search_query": "手机",
        "price_min": None, "price_max": None,
        "categories": [], "brands_include": [],
        "brands_exclude": ["FakeBrandX", "Apple"],  # FakeBrandX 不在白名单
    })
    rw = build_query_rewriter(llm=fake, known_brands=["Apple", "华为"])
    pq = await rw.parse("国产手机")
    assert "Apple" in pq.brands_exclude
    assert "FakeBrandX" not in pq.brands_exclude


@pytest.mark.asyncio
async def test_empty_input_returns_empty_parse():
    rw = build_query_rewriter()
    pq = await rw.parse("")
    assert pq.search_query == ""
    assert pq.to_filter_expr() is None


@pytest.mark.asyncio
async def test_compound_brand_name_matches_partial_token():
    """库里品牌「Apple 苹果」复合写法时，仅写 Apple 或 苹果 都要能命中。"""
    rw = build_query_rewriter(known_brands=["Apple 苹果", "华为"])
    pq1 = await rw.parse("非 Apple 品牌的轻薄笔记本")
    pq2 = await rw.parse("不要苹果手机")
    assert "Apple 苹果" in pq1.brands_exclude
    assert "Apple 苹果" in pq2.brands_exclude


@pytest.mark.asyncio
async def test_rules_strip_keeps_following_semantic():
    """剥词时只剥触发词 + canonical 品牌，保留 「的XXX」给向量召回。"""
    rw = build_query_rewriter(known_brands=["耐克"])
    pq = await rw.parse("不是耐克的专业跑鞋")
    # 关键语义"专业跑鞋"必须保留
    assert "专业跑鞋" in pq.search_query
    assert "耐克" not in pq.search_query
    assert pq.brands_exclude == ["耐克"]
