"""Prompt 构造单测：保证字段插值正确 + 防幻觉硬约束在 system prompt 里。

不调 LLM，只验文本层。
"""
from __future__ import annotations

from app.agent.prompts import build_recommend_messages, build_compare_messages
from app.rag.retriever import RetrievedProduct


def _mk_product(pid: str, brand: str = "Test", title_chunk: str = "示例标题") -> RetrievedProduct:
    return RetrievedProduct(
        product_id=pid,
        score=0.9,
        brand=brand,
        category="美妆",
        sub_category="洁面",
        base_price=99.0,
        min_sku_price=79.0,
        max_sku_price=129.0,
        best_chunk_text=title_chunk,
        best_chunk_type="title",
        supporting_chunks=[title_chunk, "示例卖点段落"],
    )


def test_recommend_messages_have_system_anti_hallucination():
    msgs = build_recommend_messages(
        user_message="推荐一款洗面奶",
        retrieved=[_mk_product("p_a"), _mk_product("p_b")],
        history=[],
    )
    assert msgs[0]["role"] == "system"
    sys = msgs[0]["content"]
    # 防幻觉的关键短语必须出现
    assert "product_id" in sys
    assert any(kw in sys for kw in ("禁止编造", "不得编造", "严禁编造"))
    # 围栏卡片协议要在 system prompt 里说清楚
    assert "product_cards" in sys


def test_recommend_messages_include_retrieved_block():
    p = _mk_product("p_beauty_001", brand="兰蔻")
    msgs = build_recommend_messages(
        user_message="推荐一款洗面奶",
        retrieved=[p],
        history=[],
    )
    # 检索结果通常拼进 system 或 user：两者皆可，但 product_id 必须出现
    joined = "\n".join(m["content"] for m in msgs)
    assert "p_beauty_001" in joined
    assert "兰蔻" in joined


def test_recommend_messages_carry_history():
    msgs = build_recommend_messages(
        user_message="再推荐一款便宜的",
        retrieved=[_mk_product("p_a")],
        history=[
            {"role": "user", "content": "之前推荐了什么"},
            {"role": "assistant", "content": "推荐了 p_a"},
        ],
    )
    roles = [m["role"] for m in msgs]
    # 顺序应该是 system → history → user
    assert roles[0] == "system"
    assert roles[-1] == "user"
    assert msgs[-1]["content"] == "再推荐一款便宜的"
    assert "之前推荐了什么" in "\n".join(m["content"] for m in msgs)


def test_recommend_messages_handle_empty_retrieval():
    msgs = build_recommend_messages(
        user_message="推荐宇宙飞船",
        retrieved=[],
        history=[],
    )
    sys = msgs[0]["content"]
    # 在没有命中商品时，要让模型明确回退到"抱歉未找到"话术
    assert "未找到" in sys or "抱歉" in sys


def test_compare_messages_include_two_products():
    msgs = build_compare_messages(
        user_message="对比 A 和 B",
        retrieved=[_mk_product("p_a", brand="A"), _mk_product("p_b", brand="B")],
        history=[],
    )
    joined = "\n".join(m["content"] for m in msgs)
    assert "p_a" in joined
    assert "p_b" in joined
    # 对比 Prompt 至少要点名结构化输出（表格 / 维度对比）
    assert "对比" in msgs[0]["content"]
