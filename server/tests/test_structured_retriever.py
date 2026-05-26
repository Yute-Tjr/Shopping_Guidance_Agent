"""StructuredRetriever 纯逻辑层单测：is_search_query_degraded。

SQL 路径需要真实 MySQL + 跨 event loop session 共享，受限于 asyncmy + pytest-asyncio
的协同问题，集成层验证统一走 smoke_chat.sh（端到端跑 8 轮场景，覆盖率比单测高）。
"""
from __future__ import annotations

import pytest

from app.rag.structured_retriever import (
    StructuredRetriever,
    is_search_query_degraded,
)


@pytest.mark.parametrize(
    "sq,degraded",
    [
        ("", True),
        ("   ", True),
        ("之间", True),
        ("以下", True),
        ("以上", True),
        ("便宜一点", True),
        ("改成", True),
        ("的", True),
        ("100-200", True),                     # 纯数字 + 连字符
        ("100-200 之间", True),                # 数字 + 退化 token
        ("油皮的洗面奶", False),                # 有主体词
        ("适合慢跑的跑鞋", False),
        ("控油精华", False),
        ("推荐一款", False),                    # 仅 4 字符但 embedding 仍有意义
    ],
)
def test_is_search_query_degraded(sq, degraded):
    assert is_search_query_degraded(sq) == degraded


@pytest.mark.asyncio
async def test_search_with_no_filter_returns_empty():
    """无任何 filter 时拒绝执行，避免误拉整表。纯逻辑断言，不打 DB。"""
    rt = StructuredRetriever()
    hits = await rt.search()
    assert hits == []
