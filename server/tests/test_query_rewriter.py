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
        sub_categories=["跑步鞋"],
        brands_exclude=["耐克"],
    )
    expr = pq.to_filter_expr()
    assert expr == (
        'category in ["服饰运动"] and sub_category in ["跑步鞋"] '
        'and brand not in ["耐克"]'
    )


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


@pytest.mark.asyncio
async def test_rules_sub_category_alias_enters_filter_expr():
    rw = build_query_rewriter()
    pq = await rw.parse("600 以下的精华")
    assert pq.price_max == 600
    assert pq.sub_categories == ["精华"]
    assert pq.to_filter_expr() == 'min_sku_price <= 600 and sub_category in ["精华"]'


@pytest.mark.asyncio
async def test_rules_running_shoe_alias_filters_exact_sub_category():
    rw = build_query_rewriter()
    pq = await rw.parse("1000 元以下的跑鞋")
    assert "服饰运动" in pq.categories
    assert pq.sub_categories == ["跑步鞋"]
    assert 'sub_category in ["跑步鞋"]' in (pq.to_filter_expr() or "")


@pytest.mark.asyncio
async def test_rules_skincare_price_uses_phase5_affordable_care_boundary():
    rw = build_query_rewriter()
    pq = await rw.parse("300 元以下的护肤")
    assert pq.price_max == 300
    assert "美妆护肤" in pq.categories
    assert pq.sub_categories == ["防晒", "洁面"]
    assert 'sub_category in ["防晒", "洁面"]' in (pq.to_filter_expr() or "")


@pytest.mark.asyncio
async def test_rules_brand_exclude_keeps_subject_sub_category():
    rw = build_query_rewriter(known_brands=["雅诗兰黛", "兰蔻"])
    pq = await rw.parse("不要雅诗兰黛的精华")
    assert pq.brands_exclude == ["雅诗兰黛"]
    assert pq.sub_categories == ["精华"]


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


@pytest.mark.parametrize(
    "msg,expected_min,expected_max",
    [
        ("一千元以上的", 1000.0, None),
        ("两千元以上", 2000.0, None),
        ("一万元以下", None, 10000.0),
        ("五百元以下的", None, 500.0),
        ("预算一千", None, 1000.0),
        ("一百到两百元", 100.0, 200.0),
        ("三千到五千元", 3000.0, 5000.0),
        ("一千五百元以上", 1500.0, None),
        ("两万元以下", None, 20000.0),
    ],
)
@pytest.mark.asyncio
async def test_chinese_number_price_filters(msg, expected_min, expected_max):
    """规则路径需要识别中文数字价格，否则用户问「一千元以上的」会全打到原文兜底。"""
    rw = build_query_rewriter()
    pq = await rw.parse(msg)
    assert pq.price_min == expected_min, f"{msg} expected price_min={expected_min} got {pq.price_min}"
    assert pq.price_max == expected_max, f"{msg} expected price_max={expected_max} got {pq.price_max}"


@pytest.mark.asyncio
async def test_chinese_numbers_do_not_misfire_on_filler_words():
    """中文数字 regex 不能把"一款""两只"误判成价格。"""
    rw = build_query_rewriter()
    pq = await rw.parse("推荐一款适合油皮的洗面奶")
    assert pq.price_min is None
    assert pq.price_max is None
    pq2 = await rw.parse("来一双跑鞋")
    assert pq2.price_min is None and pq2.price_max is None


@pytest.mark.asyncio
async def test_history_completes_followup_with_only_price():
    """Phase 4 多轮指代消解：「1000 元以上的」单独看缺品类，靠 history 补'跑鞋'。"""
    rw = build_query_rewriter()
    history = [
        {"role": "user", "content": "推荐适合慢跑的跑鞋"},
        {"role": "assistant", "content": "为你推荐..."},
    ]
    pq = await rw.parse("1000 元以上的", history=history)
    assert pq.price_min == 1000
    # search_query 必须含主体词"跑鞋"，否则向量召回会打偏
    assert "跑鞋" in pq.search_query


@pytest.mark.asyncio
async def test_history_completes_followup_with_only_brand():
    """「不要日系」单独看缺品类。"""
    rw = build_query_rewriter(known_brands=["资生堂", "SK-II", "兰蔻"])
    history = [
        {"role": "user", "content": "推荐保湿精华"},
        {"role": "assistant", "content": "..."},
    ]
    pq = await rw.parse("不要资生堂", history=history)
    assert "资生堂" in pq.brands_exclude
    assert "精华" in pq.search_query


@pytest.mark.asyncio
async def test_history_not_used_when_self_contained():
    """完整句子不要被 history 干扰污染。"""
    rw = build_query_rewriter()
    history = [{"role": "user", "content": "我之前问了笔记本"}]
    pq = await rw.parse("推荐一款适合油皮的洗面奶", history=history)
    # search_query 应保持原意，不应被 "笔记本" 污染
    assert "洗面奶" in pq.search_query
    assert "笔记本" not in pq.search_query


@pytest.mark.asyncio
async def test_history_completes_followup_with_price_softener():
    """「价格再便宜一点」是典型承接 query，没有显式 filter 但必须从 history 补主体。"""
    rw = build_query_rewriter()
    history = [
        {"role": "user", "content": "推荐一款适合油皮的洗面奶"},
        {"role": "assistant", "content": "为你推荐珊珂..."},
    ]
    pq = await rw.parse("价格再便宜一点", history=history)
    # 关键：search_query 必须含"洗面奶"主体，否则向量召回打偏
    assert "洗面奶" in pq.search_query
    assert "再便宜一点" in pq.search_query or "便宜" in pq.search_query


@pytest.mark.asyncio
async def test_history_completes_followup_with_negation_only():
    """「不要日系品牌」开头是承接词，需要从 history 补品类主体。"""
    rw = build_query_rewriter()
    history = [
        {"role": "user", "content": "推荐一款适合油皮的洗面奶"},
        {"role": "assistant", "content": "..."},
    ]
    pq = await rw.parse("不要日系品牌", history=history)
    assert "洗面奶" in pq.search_query


@pytest.mark.asyncio
async def test_followup_keywords_dont_misfire_on_descriptive_phrases():
    """承接关键词不能误伤普通描述句：「换季敏感肌的精华」里的"换"不是承接词。"""
    rw = build_query_rewriter()
    history = [{"role": "user", "content": "推荐一款适合油皮的洗面奶"}]
    pq = await rw.parse("换季敏感肌的精华", history=history)
    # 不应被 history 污染：search_query 不能出现"洗面奶"
    assert "洗面奶" not in pq.search_query
    assert "精华" in pq.search_query


@pytest.mark.asyncio
async def test_summary_used_when_history_lacks_subject():
    """phase 4-4 实测轮 7 挂的根因：memory 摘要后 history 最近 3 轮没主体词，
    必须从 session.summary 抠回品类。"""
    rw = build_query_rewriter()
    # 模拟 phase 4-4 实际场景：summary 保留了主体，history 最近几轮都是承接句
    summary = "用户需要保湿精华，预算 100-200 元，排除日系品牌，已推荐 The Ordinary"
    history = [
        {"role": "user", "content": "300 元以下"},
        {"role": "assistant", "content": "..."},
        {"role": "user", "content": "改成 100-200 之间"},
        {"role": "assistant", "content": "..."},
    ]
    pq = await rw.parse("再换一个清爽型的", history=history, summary=summary)
    # search_query 必须含"精华"主体——summary 里有，history 里没有
    assert "精华" in pq.search_query


@pytest.mark.asyncio
async def test_summary_takes_priority_over_history():
    """summary 比 history 信息密度高，应优先采用。"""
    rw = build_query_rewriter()
    summary = "用户已经在选跑鞋"
    history = [
        {"role": "user", "content": "之前推荐了洗面奶"},  # 干扰项
        {"role": "assistant", "content": "..."},
    ]
    pq = await rw.parse("再便宜一点", history=history, summary=summary)
    # summary 里的"跑鞋"应被优先采用
    assert "跑鞋" in pq.search_query
    assert "洗面奶" not in pq.search_query


@pytest.mark.asyncio
async def test_followup_hint_change_pattern():
    """phase 4-4 实测轮 6：'改成 100-200 之间' 之前不触发补全。"""
    rw = build_query_rewriter()
    history = [{"role": "user", "content": "想买保湿精华"}]
    pq = await rw.parse("改成 100-200 之间", history=history)
    assert pq.price_min == 100
    assert pq.price_max == 200
    # 必须从 history 补主体词
    assert "精华" in pq.search_query


@pytest.mark.asyncio
async def test_history_ignored_when_history_empty():
    """history 为空或 None 时按原行为返回。"""
    rw = build_query_rewriter()
    pq = await rw.parse("1000 元以上的", history=None)
    assert pq.price_min == 1000
    # 没有 history 兜底，search_query 退回原文
    assert pq.search_query  # 不崩，至少有 search_query


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
async def test_brand_exclude_apple_alias_excludes_compound_and_plain_brand():
    """数据里同时有「Apple 苹果」和「苹果」时，中文别名应同时排除两者。"""
    rw = build_query_rewriter(known_brands=["Apple 苹果", "苹果", "小米"])
    pq = await rw.parse("不要苹果的手机")
    assert pq.brands_exclude == ["Apple 苹果", "苹果"]


@pytest.mark.asyncio
async def test_rules_strip_keeps_following_semantic():
    """剥词时只剥触发词 + canonical 品牌，保留 「的XXX」给向量召回。"""
    rw = build_query_rewriter(known_brands=["耐克"])
    pq = await rw.parse("不是耐克的专业跑鞋")
    # 关键语义"专业跑鞋"必须保留
    assert "专业跑鞋" in pq.search_query
    assert "耐克" not in pq.search_query
    assert pq.brands_exclude == ["耐克"]
