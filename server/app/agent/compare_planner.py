"""对比意图的检索规划：把单句对比 query 拆成多个 self-contained 检索 target。

为什么要这层：
- 用户写"对比一下兰蔻和雅诗兰黛的精华哪个更保湿"，整句 embedding 召回会偏向
  语义最强的那一边（通常是"保湿"），第二个品牌往往拉不进 Top-3。
- 把它拆成两个独立 query "兰蔻 精华 保湿" / "雅诗兰黛 精华 保湿" 各自检索 Top-2，
  再合并去重塞 prompt，能保证 LLM 拿到两边的代表商品做横向对比。

设计：
1. **规则 fast-path**：识别「对比/比较/比一比/vs/VS」触发词 → 去掉「哪个更/哪款更/呢/吗」
   尾巴 → 按「和/与/跟/、/,/，/vs/VS」切两段；切完每段补回公共修饰（"的精华"
   "跑鞋"等）和疑问尾部的关键词（"保湿"）；
2. **LLM JSON 兜底**：规则切不出 ≥2 段时调 chat_json 抽 ``{"targets": [...]}``；
3. **失败兜底**：返回 ``[original_message]``，orchestrator 退化为整句一次性检索。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from app.utils.logger import get_logger

logger = get_logger(__name__)


# 触发词：意图已经被 IntentRouter 判定为 compare，这里只用它定位 body 起点。
_COMPARE_TRIGGERS = ("对比一下", "对比", "比一比", "比较", "比 ")
# 连接词：用来切两段对比对象
_CONJ = ("和", "与", "跟", "对比", "、")
# 中英文 vs：单独处理避免误切
_VS_PAT = re.compile(r"\s*(?:vs|VS|对|对比)\s*")
# 疑问尾部：要从 body 末尾砍掉，避免把"哪个更保湿"当成第二个目标
_TAIL_PAT = re.compile(
    r"(?:哪个更|哪款更|哪个|哪款|哪种|哪一款|谁更|哪|更)?([^?？!！]*?)[?？!！]*$"
)
# 助词去除（开头）
_LEADING_FILLER = ("一下", "下", "请", "麻烦", "帮我", "帮忙")


@dataclass
class ComparePlan:
    targets: list[str]                # 用于检索的独立 query 列表
    raw_segments: list[str]           # 切出来的原始片段（保留供调试）
    used_llm: bool = False            # 是否触发 LLM 兜底


class _LLMJSON(Protocol):
    async def chat_json(self, messages: list[dict[str, Any]], **kw: Any) -> dict[str, Any]: ...


class CompareTargetExtractor:
    """规则 + LLM 双路径，无 LLM 时退化成纯规则。"""

    _SYSTEM_PROMPT = (
        "你是电商导购对比意图的解析器。用户的一句话里包含 2-3 个要对比的商品 / 品牌，"
        "请把它拆成同样数量的独立检索 query。每个 query 必须 self-contained：包含品牌/商品名"
        "+ 用户关心的属性（如保湿、跑步、降噪 等）。\n"
        '只输出 JSON 对象 {"targets": ["...", "..."]}，不要 markdown，不要解释。\n'
        "示例：\n"
        '  输入：对比一下兰蔻和雅诗兰黛的精华哪个更保湿\n'
        '  输出：{"targets": ["兰蔻 精华 保湿", "雅诗兰黛 精华 保湿"]}\n'
        '  输入：iPhone 17 vs 华为 Pura 90\n'
        '  输出：{"targets": ["iPhone 17", "华为 Pura 90"]}'
    )

    def __init__(self, *, llm: _LLMJSON | None = None) -> None:
        self.llm = llm

    async def plan(self, message: str) -> ComparePlan:
        text = (message or "").strip()
        if not text:
            return ComparePlan(targets=[], raw_segments=[])

        rule = self._rule_plan(text)
        if len(rule.targets) >= 2:
            return rule

        # 规则拆不出 ≥2 段 → 调 LLM
        if self.llm is None:
            return ComparePlan(targets=[text], raw_segments=rule.raw_segments)

        try:
            llm_targets = await self._llm_split(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("CompareTargetExtractor LLM 兜底失败：%s", exc)
            return ComparePlan(targets=[text], raw_segments=rule.raw_segments)

        targets = [t for t in llm_targets if isinstance(t, str) and t.strip()]
        if len(targets) >= 2:
            return ComparePlan(targets=targets, raw_segments=rule.raw_segments, used_llm=True)
        return ComparePlan(targets=[text], raw_segments=rule.raw_segments)

    # ---- rule path ----

    def _rule_plan(self, text: str) -> ComparePlan:
        # 1) VS 切：vs/VS 优先（明确无歧义）
        if _VS_PAT_STRICT.search(text):
            parts = [s.strip() for s in _VS_PAT_STRICT.split(text)]
            parts = [p for p in parts if p]
            if len(parts) >= 2:
                return ComparePlan(
                    targets=[self._clean_segment(p) for p in parts][:3],
                    raw_segments=parts,
                )

        # 2) 触发词切：找到「对比/比较」后面的 body
        body = text
        for trig in _COMPARE_TRIGGERS:
            idx = text.find(trig)
            if idx >= 0:
                body = text[idx + len(trig):]
                break

        # 去掉开头填充词
        for fill in _LEADING_FILLER:
            if body.startswith(fill):
                body = body[len(fill):]

        # 砍掉尾部疑问句的"哪个更XXX"部分，并提取关键词补回 targets
        tail_keyword = ""
        m = re.search(r"(?:哪个更|哪款更|谁更|哪个|哪款|哪种)\s*([一-鿿]{1,8})[？?！!。]?$", body)
        if m:
            tail_keyword = m.group(1).strip()
            body = body[: m.start()].strip()
        # 单独砍掉问号
        body = body.rstrip("?？!！。")

        # 3) 按连接词切两段（保留更长的拆分组合）
        segments = _split_by_conjunctions(body)
        if len(segments) < 2:
            return ComparePlan(targets=[], raw_segments=[body])

        # 4) 公共修饰补全：把最后一段中类似"的精华"/"精华"的尾词补给前面所有段
        modifier = _extract_trailing_modifier(segments[-1])
        if modifier:
            # 最后一段已经有 modifier，前面的段如果不包含同一个名词就补进去
            for i in range(len(segments) - 1):
                if modifier not in segments[i]:
                    segments[i] = (segments[i] + " " + modifier).strip()

        # 5) 拼回尾部关键词（保湿 / 拍照 等）
        if tail_keyword:
            segments = [(s + " " + tail_keyword).strip() for s in segments]

        targets = [self._clean_segment(s) for s in segments if self._clean_segment(s)]
        return ComparePlan(targets=targets[:3], raw_segments=segments)

    @staticmethod
    def _clean_segment(s: str) -> str:
        t = s.strip(" 的，。、,!！?？")
        # 多空格归一
        t = re.sub(r"\s+", " ", t)
        return t

    # ---- LLM path ----

    async def _llm_split(self, text: str) -> list[str]:
        messages = [
            {"role": "system", "content": self._SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]
        result = await self.llm.chat_json(messages)  # type: ignore[union-attr]
        raw = result.get("targets") or []
        return raw if isinstance(raw, list) else []


# ---- 模块级 helpers ----

# 严格的 VS 模式：要求 vs/VS 两侧有空格，避免误切像「Visual Studio」这种
_VS_PAT_STRICT = re.compile(r"\s+(?:vs|VS)\s+")


def _split_by_conjunctions(body: str) -> list[str]:
    """按「和/与/跟/、」切片；优先选切出 2-3 段且每段非空的方案。"""
    # 同时尝试多种切法，选切出最多有效段（2-3 段）的那种
    candidates: list[list[str]] = []
    for conj in _CONJ:
        if conj not in body:
            continue
        parts = [p.strip() for p in body.split(conj)]
        parts = [p for p in parts if p]
        if 2 <= len(parts) <= 4:
            candidates.append(parts)
    if not candidates:
        return [body.strip()] if body.strip() else []
    # 取段数最接近 2-3 的；段数相同时取段长平均值最大的（信息更密集）
    candidates.sort(key=lambda parts: (abs(len(parts) - 2), -sum(len(p) for p in parts) / len(parts)))
    return candidates[0]


def _extract_trailing_modifier(segment: str) -> str:
    """从一段里抽末尾的修饰名词，如"雅诗兰黛的精华" → "的精华"。"""
    m = re.search(r"(的?[一-鿿]{1,4})$", segment)
    if not m:
        return ""
    return m.group(1)


def build_compare_extractor(*, llm: _LLMJSON | None = None) -> CompareTargetExtractor:
    return CompareTargetExtractor(llm=llm)
