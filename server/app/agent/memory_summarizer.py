"""把前段对话历史压成一段用户偏好概述（Phase 4-4）。

设计：
- 复用 DoubaoChatClient.chat_json（Phase 4-1 已经做了容错解析）；
- 输入：前一段被淘汰的 history list（OpenAI messages 形态）+ 上一次的 summary（如果有）；
- 输出：纯文本 summary（≤100 字）。
- 防爆：history 拼出来的对话稿超过 _MAX_INPUT_CHARS 时做尾部截断（保留更靠后的对话）。

写在独立文件而不是塞 memory.py 里，是为了把"摘要策略"（什么进 prompt）与
"摘要存储"（history 怎么截断）解耦：策略以后想换 LLM、改 prompt、加结构化字段
都只动这个文件，memory.py 保持稳定。
"""
from __future__ import annotations

from typing import Any, Iterable, Protocol

from app.utils.logger import get_logger

logger = get_logger(__name__)

# 一段对话稿喂给 LLM 的字符上限：保护 prompt 不爆 + 控成本
_MAX_INPUT_CHARS = 4000


class _LLMJSON(Protocol):
    async def chat_json(self, messages: list[dict[str, Any]], **kw: Any) -> dict[str, Any]: ...


_SYSTEM_PROMPT = (
    "你是电商导购对话历史摘要器。把下面的多轮对话压成一段用户偏好概述（≤100 字）。\n"
    "要点优先级：\n"
    "1. 用户表达过的明确约束：品类 / 价格 / 品牌偏好或排除 / 场景 / 肤质 / 痛点；\n"
    "2. 已经被推荐过的商品 product_id 列表（保留原文，方便下一轮不要重复推）；\n"
    "3. 用户的反馈倾向（喜欢 / 不要 / 想再看看）。\n"
    "严格禁止保留的内容：\n"
    "- 助手的「抱歉未找到匹配商品」「暂未找到」等否定结论——这是某一轮的临时结果，"
    "下一轮检索结果会变，摘要里写这种结论会让 LLM 误以为后续都找不到；\n"
    "- 客套话、空洞描述（如「很适合」「性价比高」等）、商品营销文案。\n"
    '只输出 JSON 对象 {"summary": "..."}，纯文本概述，不要 markdown，不要解释。'
)


class MemorySummarizer:
    """LLM 摘要器：单一职责，只负责"老对话稿 → 偏好概述字符串"。"""

    def __init__(self, *, llm: _LLMJSON) -> None:
        self.llm = llm

    async def summarize(
        self,
        *,
        previous_summary: str | None,
        older_history: Iterable[dict[str, Any]],
    ) -> str:
        transcript = _format_history(older_history)
        if not transcript.strip():
            return previous_summary or ""

        user_parts: list[str] = []
        if previous_summary:
            user_parts.append(f"[此前已摘要]\n{previous_summary}\n")
        user_parts.append("[本次新增对话片段]\n" + transcript)
        user_content = "\n".join(user_parts)
        if len(user_content) > _MAX_INPUT_CHARS:
            # 末尾对话比开头重要（更靠近现在的偏好），保留后段
            user_content = user_content[-_MAX_INPUT_CHARS:]

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        try:
            result = await self.llm.chat_json(messages)
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory summarize 调 LLM 失败：%s", exc)
            return previous_summary or ""
        summary = result.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return previous_summary or ""
        return summary.strip()


def _format_history(history: Iterable[dict[str, Any]]) -> str:
    """把 list[dict] 拼成"用户：...\\n助手：..."形态的对话稿。"""
    lines: list[str] = []
    for m in history:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            lines.append(f"用户：{content}")
        elif role == "assistant":
            lines.append(f"助手：{content}")
        # 其它 role（system / tool）跳过
    return "\n".join(lines)


def build_memory_summarizer(*, llm: _LLMJSON) -> MemorySummarizer:
    return MemorySummarizer(llm=llm)
