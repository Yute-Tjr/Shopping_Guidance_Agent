"""Prompt 模板集中地。

docs/01 §6.1 + docs/03 §5.2 的强制约束：
1. **product_id / SKU 名 / 价格只能严格来自 <retrieved_products>**，禁止编造。
2. 找不到匹配商品时必须明确说"抱歉，库内暂未找到匹配的商品"。
3. 卡片协议：正文之后用 ```product_cards 围栏输出 JSON 数组
   [{"product_id":"...","reason":"≤30字推荐理由"}]，
   product_card_extractor.py 会在流式 token 中识别并抽出。

Phase 2 提供 2 个模板：
- build_recommend_messages：通用推荐
- build_compare_messages：多商品对比（启用结构化对比表）

变更原则：任何改 Prompt 的 PR 必须先跑 tests/test_prompts.py 与 smoke_chat.sh，
避免静默回归出现"编造商品"的事故。
"""
from __future__ import annotations

from typing import Iterable

from app.rag.retriever import RetrievedProduct


_BASE_RULES = """你是一个 iOS 电商导购 AI Agent，回答必须满足以下硬约束：

1. **严禁编造**：所有商品的 product_id / 品牌 / 价格 / SKU 名称、属性都必须严格来自下方 <retrieved_products> 中出现的字段。
   - 出现库外的 product_id，等同于事故；
   - 价格不要四舍五入或自行换算，按 <retrieved_products> 给出的区间叙述即可。
2. **结构化卡片**：在正文回答之后，**必须**用 Markdown 围栏输出一段 product_cards JSON，格式严格如下：

```product_cards
[
  {"product_id": "<必须来自 retrieved_products>", "reason": "30 字以内推荐理由"}
]
```

   - 仅这一段围栏，不要嵌套别的代码块；
   - JSON 数组里 1-3 件商品；
   - reason 控制在 30 字内，突出"为什么适合用户提的诉求"。
3. **找不到匹配商品时**：正文必须明确说一句"抱歉，库内暂未找到匹配的商品"，并且**不要输出任何 product_cards 围栏**。
4. 回答风格简短亲切，口语化中文，避免空洞夸张词；不要重复用户原话开头。"""


def _format_retrieved_block(retrieved: Iterable[RetrievedProduct]) -> str:
    """把检索结果拼成 LLM 可读的结构化片段。"""
    items = list(retrieved)
    if not items:
        return "<retrieved_products>\n(无命中)\n</retrieved_products>"
    lines = ["<retrieved_products>"]
    for i, p in enumerate(items, 1):
        # 价格区间用 SKU 范围；若没有则回退 base_price
        if p.min_sku_price and p.max_sku_price:
            price_str = f"￥{p.min_sku_price:.2f} - ￥{p.max_sku_price:.2f}"
        else:
            price_str = f"￥{p.base_price:.2f}"
        # 最多带 2 段命中文本，避免上下文爆炸
        snippets = " / ".join(s.replace("\n", " ")[:120] for s in p.supporting_chunks[:2])
        lines.append(
            f"[{i}] product_id={p.product_id} | 品牌={p.brand} | 品类={p.category}/{p.sub_category} "
            f"| 价格={price_str}"
        )
        if snippets:
            lines.append(f"    命中片段：{snippets}")
    lines.append("</retrieved_products>")
    return "\n".join(lines)


def _history_messages(history: list[dict] | None) -> list[dict]:
    """把会话历史标准化为 OpenAI messages 形式；防御性截断。"""
    if not history:
        return []
    cleaned: list[dict] = []
    for m in history:
        role = m.get("role")
        content = m.get("content", "")
        if role in ("user", "assistant") and content:
            cleaned.append({"role": role, "content": content})
    return cleaned


def build_recommend_messages(
    *,
    user_message: str,
    retrieved: Iterable[RetrievedProduct],
    history: list[dict] | None,
) -> list[dict]:
    """商品推荐 prompt。retrieved 为空时切换到「无匹配」话术。"""
    system_parts = [_BASE_RULES, "", _format_retrieved_block(retrieved)]
    msgs: list[dict] = [{"role": "system", "content": "\n".join(system_parts)}]
    msgs.extend(_history_messages(history))
    msgs.append({"role": "user", "content": user_message})
    return msgs


def build_compare_messages(
    *,
    user_message: str,
    retrieved: Iterable[RetrievedProduct],
    history: list[dict] | None,
) -> list[dict]:
    """商品对比 prompt：在 base 规则上追加表格 / 维度结构化要求。"""
    compare_rules = """\n额外的对比任务要求：
- 用一个简短的 Markdown 表格做横向对比，列 = 商品 ID，行 = 关键维度（如 价格区间 / 适用场景 / 关键卖点）；
- 维度由你结合命中片段总结，不超过 4 行；
- 表格之后给一句 ≤ 30 字的总结推荐；
- 最后照例追加 product_cards 围栏。"""
    system_parts = [_BASE_RULES + compare_rules, "", _format_retrieved_block(retrieved)]
    msgs: list[dict] = [{"role": "system", "content": "\n".join(system_parts)}]
    msgs.extend(_history_messages(history))
    msgs.append({"role": "user", "content": user_message})
    return msgs
