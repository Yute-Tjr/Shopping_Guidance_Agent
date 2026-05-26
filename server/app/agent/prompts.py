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
2. **结构化卡片**：在正文回答之后，**必须**用 Markdown 围栏输出一段 product_cards JSON，格式严格如下:

```product_cards
[
  {"product_id": "<必须来自 retrieved_products>", "reason": "30 字以内推荐理由"}
]
```

   - 仅这一段围栏，不要嵌套别的代码块；
   - JSON 数组里 1-3 件商品；
   - reason 控制在 30 字内，突出"为什么适合用户提的诉求"。
3. **找不到匹配商品时**：正文必须明确说一句"抱歉，库内暂未找到匹配的商品"，并且**不要输出任何 product_cards 围栏**。
4. 回答风格简短亲切，口语化中文，避免空洞夸张词；不要重复用户原话开头。
5. **不要使用任何 emoji 表情** —— 包括但不限于 ✅ ❌ ✨ 🔥 👉 ⭐ 💯 🎉 ❤️ 💕 🛒 💰 🚀 🌟 🎯 ✔️ ☑️ ⚠️ 📢 🆕 🔝 等。
   原因：iOS 客户端用衢线 serif 字体（New York），emoji 字符在 serif 字体下无法正确渲染会显示为方块。
   要点列表请用纯文字符号「-」或数字「1. 2. 3.」开头，不要用 emoji 当 bullet。"""


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
        # title 字段优先；缺时用 brand 兜底，给 LLM 一个用户友好的 display_name 用做对比表头
        display_name = p.title or p.brand or p.product_id
        lines.append(
            f"[{i}] product_id={p.product_id} | 名称={display_name} | 品牌={p.brand} | "
            f"品类={p.category}/{p.sub_category} | 价格={price_str}"
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


def _format_summary_block(summary: str | None) -> str:
    """Phase 4-4：把 memory summarizer 给的偏好概述拼成 system 块。"""
    if not summary or not summary.strip():
        return ""
    return (
        "<conversation_summary>\n"
        f"用户偏好概述（来自此前多轮对话）：{summary.strip()}\n"
        "回答时请参考但不要复述，回到当前用户最新一句。\n"
        "</conversation_summary>"
    )


def build_recommend_messages(
    *,
    user_message: str,
    retrieved: Iterable[RetrievedProduct],
    history: list[dict] | None,
    summary: str | None = None,
) -> list[dict]:
    """商品推荐 prompt。retrieved 为空时切换到「无匹配」话术。"""
    system_parts = [_BASE_RULES]
    summary_block = _format_summary_block(summary)
    if summary_block:
        system_parts.extend(["", summary_block])
    system_parts.extend(["", _format_retrieved_block(retrieved)])
    msgs: list[dict] = [{"role": "system", "content": "\n".join(system_parts)}]
    msgs.extend(_history_messages(history))
    msgs.append({"role": "user", "content": user_message})
    return msgs


def build_compare_messages(
    *,
    user_message: str,
    retrieved: Iterable[RetrievedProduct],
    history: list[dict] | None,
    summary: str | None = None,
) -> list[dict]:
    """商品对比 prompt：在 base 规则上追加表格 / 维度结构化要求。

    Phase 4-2：把 Markdown 表格规则进一步收紧，让 iOS 端的 MarkdownParser 一定能识别。
    Phase 4 收尾：表头改用「品牌 + 简短名称」（如"兰蔻小黑瓶"/"The Ordinary 精华"），
    product_id 仅在 product_cards 围栏 JSON 里出现——product_id 是内部主键，用户看不懂，
    放表头不友好；防幻觉锚点由围栏 JSON 兜底。
    """
    compare_rules = """\n额外的对比任务要求（必须遵守，否则前端展示会失败）：
1. 当 retrieved_products 中有 ≥2 件商品时，**必须**输出一个 Markdown 表格做横向对比；
2. 表格格式必须严格如下（GFM 标准，含分隔行）：

| 对比维度 | <商品 1 显示名> | <商品 2 显示名> |
| --- | --- | --- |
| 价格区间 | <从 retrieved 取> | <从 retrieved 取> |
| 关键卖点 | <8-15 字总结> | <8-15 字总结> |
| 适用场景 | <8-15 字总结> | <8-15 字总结> |

3. 表头第一列固定写「对比维度」，其余列**用「品牌+简短名称」做显示名**，从 retrieved_products
   的「名称=...」字段取（如"兰蔻小黑瓶""The Ordinary 精华""阿迪达斯 Ultraboost 5"等，去掉
   冗长尾缀如"30ml""男子缓震..."，保留品牌+核心型号即可，控制 ≤12 字）；
4. **严禁**在表头使用 product_id（如 p_beauty_011）—— product_id 是内部主键用户看不懂；
5. 数据行 3-4 行；价格区间直接从 retrieved 给出的「价格=￥a - ￥b」原样填；其它维度结合命中片段提炼；
6. 表格之后**空一行**，再写 ≤ 40 字的总结句（先把更适合的那款用名称点出来，不要写 product_id）；
7. 最后照例追加 product_cards 围栏，把对比涉及的所有 product_id 都列进去
   —— product_cards 围栏里**仍然用 product_id**（这是给前端代码用的，不显示给用户）。"""
    system_parts = [_BASE_RULES + compare_rules]
    summary_block = _format_summary_block(summary)
    if summary_block:
        system_parts.extend(["", summary_block])
    system_parts.extend(["", _format_retrieved_block(retrieved)])
    msgs: list[dict] = [{"role": "system", "content": "\n".join(system_parts)}]
    msgs.extend(_history_messages(history))
    msgs.append({"role": "user", "content": user_message})
    return msgs
