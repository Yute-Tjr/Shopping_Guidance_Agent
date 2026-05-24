"""ProductCardExtractor 单元测试。

抽取器是 Phase 2 防幻觉链路的关键，必须能在以下场景全部不漏不错：
1. 围栏外文本逐 token 全部可见
2. 单卡 / 多卡 JSON 围栏闭合后解析并产出
3. allowed_ids 过滤掉编造的 product_id
4. 围栏 marker 跨 token 分割（最常见 LLM 流式坑）也能识别
5. 残缺 / 坏 JSON 不能让流崩溃
6. 围栏后还有正文也要继续吐
"""
from __future__ import annotations

import pytest

from app.agent.card_extractor import ProductCardExtractor


ALLOWED = {"p_beauty_001", "p_beauty_002"}


def _drain(extractor: ProductCardExtractor, chunks):
    visible_total = ""
    cards_total: list[dict] = []
    for ch in chunks:
        vis, cards = extractor.feed(ch)
        visible_total += vis
        cards_total.extend(cards)
    final_vis, final_cards = extractor.finalize()
    visible_total += final_vis
    cards_total.extend(final_cards)
    return visible_total, cards_total


def test_plain_text_passes_through():
    ext = ProductCardExtractor(allowed_ids=ALLOWED)
    visible, cards = _drain(ext, ["你好", "，", "为你推荐"])
    assert visible == "你好，为你推荐"
    assert cards == []


def test_single_card_in_fence_emitted_once():
    ext = ProductCardExtractor(allowed_ids=ALLOWED)
    text = (
        "为你推荐一款洗面奶：\n"
        "```product_cards\n"
        '[{"product_id":"p_beauty_001","reason":"温和控油"}]\n'
        "```\n"
        "如需更多请告诉我。"
    )
    visible, cards = _drain(ext, [text])
    assert "product_cards" not in visible
    assert "p_beauty_001" not in visible
    # 围栏前的 \n 被 rstrip；围栏闭合后的 "\n如需..." 属正文新段保留；
    # 文末无 trailing 空白
    assert visible == "为你推荐一款洗面奶：\n如需更多请告诉我。"
    assert len(cards) == 1
    assert cards[0]["product_id"] == "p_beauty_001"
    assert cards[0]["reason"] == "温和控油"


def test_unknown_product_id_filtered():
    ext = ProductCardExtractor(allowed_ids=ALLOWED)
    text = (
        "```product_cards\n"
        '[{"product_id":"p_fake_999","reason":"幻觉商品"},'
        ' {"product_id":"p_beauty_002","reason":"真实商品"}]\n'
        "```"
    )
    _, cards = _drain(ext, [text])
    assert len(cards) == 1
    assert cards[0]["product_id"] == "p_beauty_002"


def test_fence_marker_split_across_chunks():
    """LLM 流式最常见坑：'```product_cards' 被切成多个 token 投递。"""
    ext = ProductCardExtractor(allowed_ids=ALLOWED)
    # 把围栏 marker 故意按 1-3 字符切碎
    chunks = [
        "推荐：",
        "``",
        "`pro",
        "duct_ca",
        "rds\n",
        '[{"prod',
        'uct_id":"p_be',
        'auty_001","reason":"控油"}]',
        "\n``",
        "`",
        "\n好的。",
    ]
    visible, cards = _drain(ext, chunks)
    assert "```" not in visible
    assert "product_cards" not in visible
    assert "product_id" not in visible
    assert visible.startswith("推荐：")
    assert visible.endswith("好的。")
    assert len(cards) == 1
    assert cards[0]["product_id"] == "p_beauty_001"


def test_multiple_cards_in_one_fence():
    ext = ProductCardExtractor(allowed_ids=ALLOWED)
    text = (
        "```product_cards\n"
        '[{"product_id":"p_beauty_001","reason":"r1"},'
        '{"product_id":"p_beauty_002","reason":"r2"}]\n'
        "```"
    )
    _, cards = _drain(ext, [text])
    assert [c["product_id"] for c in cards] == ["p_beauty_001", "p_beauty_002"]


def test_malformed_json_does_not_crash():
    ext = ProductCardExtractor(allowed_ids=ALLOWED)
    text = (
        "前面文字。\n"
        "```product_cards\n"
        "[{this is not json"
        "\n```\n"
        "后面文字。"
    )
    visible, cards = _drain(ext, [text])
    assert cards == []
    # 围栏内的坏 JSON 不能泄漏给用户
    assert "this is not json" not in visible
    assert "前面文字" in visible and "后面文字" in visible


def test_reason_field_truncated():
    """reason 字段超长要截断，避免 LLM 编造段落级'理由'破坏 UI。"""
    ext = ProductCardExtractor(allowed_ids=ALLOWED)
    long_reason = "x" * 500
    text = (
        "```product_cards\n"
        f'[{{"product_id":"p_beauty_001","reason":"{long_reason}"}}]\n'
        "```"
    )
    _, cards = _drain(ext, [text])
    assert len(cards) == 1
    # 上限 120 字符，与 schemas.chat.ProductCardEvent.reason 的 max_length 一致
    assert len(cards[0]["reason"]) <= 120


def test_unclosed_fence_dropped_silently():
    """LLM 截断/超时时围栏没闭合：内容必须丢弃，绝不能当正文吐出来。"""
    ext = ProductCardExtractor(allowed_ids=ALLOWED)
    text = (
        "推荐：\n"
        "```product_cards\n"
        '[{"product_id":"p_beauty_001","reason":"未闭合'
    )
    visible, cards = _drain(ext, [text])
    assert cards == []
    # 围栏命中时 rstrip 了围栏前文本，"推荐：\n" → "推荐："
    assert visible == "推荐："
    assert "未闭合" not in visible
    assert "product_id" not in visible


def test_trailing_whitespace_before_fence_stripped():
    """LLM 正文常以 \\n\\n 收尾后接 ```product_cards 围栏；
    可见文本不能带这两个换行，否则客户端气泡末尾会撑出空白格子。"""
    ext = ProductCardExtractor(allowed_ids=ALLOWED)
    text = (
        "推荐这款。\n\n"
        "```product_cards\n"
        '[{"product_id":"p_beauty_001","reason":"ok"}]\n'
        "```"
    )
    visible, cards = _drain(ext, [text])
    assert visible == "推荐这款。"
    assert len(cards) == 1


def test_mid_text_newlines_preserved():
    """段落中间的换行是正文一部分，绝不能被误吃。"""
    ext = ProductCardExtractor(allowed_ids=ALLOWED)
    text = (
        "第一行\n"
        "第二行\n\n"
        "第三行。\n\n"
        "```product_cards\n[{\"product_id\":\"p_beauty_001\",\"reason\":\"r\"}]\n```"
    )
    visible, _ = _drain(ext, [text])
    assert visible == "第一行\n第二行\n\n第三行。"


def test_no_fence_trailing_whitespace_dropped_on_finalize():
    """没有围栏的纯文本，末尾空白在 finalize 时丢弃（文末空白无语义）。"""
    ext = ProductCardExtractor(allowed_ids=ALLOWED)
    visible, _ = _drain(ext, ["你好，\n", "今天天气真好。\n\n"])
    assert visible == "你好，\n今天天气真好。"


def test_streamed_trailing_whitespace_then_fence_token_split():
    """流式场景：尾换行和围栏 marker 分多次 feed 来，仍要剪掉空白。"""
    ext = ProductCardExtractor(allowed_ids=ALLOWED)
    chunks = [
        "推荐这款",
        "。",
        "\n",
        "\n",
        "```product_cards\n",
        '[{"product_id":"p_beauty_001","reason":"ok"}]',
        "\n```",
    ]
    visible, cards = _drain(ext, chunks)
    assert visible == "推荐这款。"
    assert len(cards) == 1


def test_missing_product_id_skipped():
    ext = ProductCardExtractor(allowed_ids=ALLOWED)
    text = (
        "```product_cards\n"
        '[{"reason":"无 id"}, {"product_id":"p_beauty_001","reason":"ok"}]\n'
        "```"
    )
    _, cards = _drain(ext, [text])
    assert len(cards) == 1
    assert cards[0]["product_id"] == "p_beauty_001"
