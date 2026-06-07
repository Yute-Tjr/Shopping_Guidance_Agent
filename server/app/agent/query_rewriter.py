"""Query 改写与结构化筛选条件抽取（Phase 4 落地）。

目的：把用户的自然语言查询拆成「干净的语义检索 query + 结构化过滤条件」。
干净的 query 喂 Embedding 走向量召回；结构化条件转 Milvus filter expr 做
metadata 过滤（价格区间、类目白名单、品牌排除）。

为什么要这层：
- Phase 1 评测里 brand_exclude 意图 Top-1=0%，向量模型不擅长否定语义；
- 价格 / 品牌 / 类目本来就是结构化字段，让向量"猜"是浪费；
- Milvus FLAT + scalar filter 在 1k 规模下 latency 几乎免费。

设计：
1. **规则优先**：常见 pattern（"X 元以下""不要 X""X 品牌"等）走正则 fast-path，
   不调 LLM，省一次往返；
2. **LLM 兜底**：规则吃不下的（如"日系品牌""国产手机"等需要语义推断）调
   `LLMLike.chat_json` 用 JSON 模式抽剩余字段；LLM 不可用 / 解析失败时
   只用规则结果，绝不让业务路径挂掉；
3. **Brand 白名单注入**：LLM prompt 里塞当前库内品牌全集（异步 MySQL 查
   一次缓存），避免 LLM 自由发挥编出库里没有的品牌。

输出 ParsedQuery 自带 ``to_filter_expr()``，把字段拼成 Milvus 表达式：
    min_sku_price <= 200 and category in ["数码电子"] and brand not in ["Apple"]
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from app.utils.logger import get_logger

logger = get_logger(__name__)

# 库内实际类目（与 MySQL `products.category` 字段对齐，不是数据集目录名）；
# Phase 5 加品类时同步更新。
KNOWN_CATEGORIES: tuple[str, ...] = ("美妆护肤", "数码电子", "服饰运动", "食品饮料")

# 黑话 → 标准类目映射，给规则路径用；LLM 会自己处理同义词
_CATEGORY_ALIASES: dict[str, str] = {
    "美妆": "美妆护肤", "护肤": "美妆护肤", "彩妆": "美妆护肤",
    "数码": "数码电子", "电子": "数码电子", "3c": "数码电子", "手机": "数码电子",
    "电脑": "数码电子", "笔记本": "数码电子", "耳机": "数码电子",
    "服饰": "服饰运动", "服装": "服饰运动", "鞋": "服饰运动",
    "运动": "服饰运动", "跑鞋": "服饰运动", "篮球鞋": "服饰运动",
    "食品": "食品饮料", "零食": "食品饮料", "饮料": "食品饮料", "咖啡": "食品饮料",
}

# 黑话 → products.sub_category 映射。值用 list 是因为少数宽泛意图需要映射到
# 多个可验收子类，例如 Phase 5 的「300 元以下护肤」黄金集定义为防晒 / 洁面。
_SUB_CATEGORY_ALIASES: dict[str, list[str]] = {
    "跑步鞋": ["跑步鞋"],
    "跑鞋": ["跑步鞋"],
    "篮球鞋": ["篮球鞋"],
    "智能手机": ["智能手机"],
    "旗舰手机": ["智能手机"],
    "手机": ["智能手机"],
    "笔记本电脑": ["笔记本电脑"],
    "笔记本": ["笔记本电脑"],
    "平板电脑": ["平板电脑"],
    "平板": ["平板电脑"],
    "真无线耳机": ["真无线耳机"],
    "蓝牙耳机": ["真无线耳机"],
    "耳机": ["真无线耳机"],
    "精华液": ["精华"],
    "精华": ["精华"],
    "防晒霜": ["防晒"],
    "防晒乳": ["防晒"],
    "防晒": ["防晒"],
    "洗面奶": ["洁面"],
    "洁面乳": ["洁面"],
    "洁面": ["洁面"],
    "咖啡": ["咖啡"],
}

_AFFORDABLE_SKINCARE_SUB_CATEGORIES: tuple[str, ...] = ("防晒", "洁面")


@dataclass
class ParsedQuery:
    """Rewriter 输出。"""

    search_query: str                         # 喂 embedder 的语义 query
    price_min: Optional[float] = None         # 最低价（含），单位元
    price_max: Optional[float] = None         # 最高价（含），单位元
    categories: list[str] = field(default_factory=list)        # 类目白名单
    sub_categories: list[str] = field(default_factory=list)    # 子类白名单
    brands_include: list[str] = field(default_factory=list)    # 必须的品牌
    brands_exclude: list[str] = field(default_factory=list)    # 必须排除的品牌

    def to_filter_expr(self) -> Optional[str]:
        """拼成 Milvus filter 表达式；任何条件都没有就返回 None。

        约定 schema：``min_sku_price``/``max_sku_price`` FLOAT，``category`` VARCHAR，
        ``brand`` VARCHAR（参见 milvus_store._build_schema）。
        """
        parts: list[str] = []
        # 价格："≤ 200" → 最便宜的 SKU 都不超过 200 才符合
        if self.price_max is not None:
            parts.append(f"min_sku_price <= {self.price_max:g}")
        if self.price_min is not None:
            parts.append(f"max_sku_price >= {self.price_min:g}")
        if self.categories:
            cats = ", ".join(f'"{_escape(c)}"' for c in self.categories)
            parts.append(f"category in [{cats}]")
        if self.sub_categories:
            subs = ", ".join(f'"{_escape(c)}"' for c in self.sub_categories)
            parts.append(f"sub_category in [{subs}]")
        if self.brands_include:
            bs = ", ".join(f'"{_escape(b)}"' for b in self.brands_include)
            parts.append(f"brand in [{bs}]")
        if self.brands_exclude:
            bs = ", ".join(f'"{_escape(b)}"' for b in self.brands_exclude)
            parts.append(f"brand not in [{bs}]")
        return " and ".join(parts) if parts else None


def _escape(s: str) -> str:
    """Milvus 表达式里 string 用双引号包裹，转义内部双引号。"""
    return s.replace('"', '\\"')


_CJK = re.compile(r"[一-鿿]+")
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9\-]*")


def _split_brand_aliases(canonical: str) -> list[str]:
    """把库内 canonical 品牌名拆 alias。

    例：「Apple 苹果」→ ["Apple 苹果", "Apple", "苹果"]；
        「Nike」      → ["Nike"]；
        「耐克」      → ["耐克"]。

    用户可能只写英文 / 只写中文 / 全名，都要能命中。
    """
    out: list[str] = [canonical]
    seen = {canonical}
    for chunk in _LATIN.findall(canonical):
        if chunk and chunk not in seen:
            out.append(chunk)
            seen.add(chunk)
    for chunk in _CJK.findall(canonical):
        if chunk and chunk not in seen:
            out.append(chunk)
            seen.add(chunk)
    return out


class _LLMJSON(Protocol):
    async def chat_json(self, messages: list[dict[str, Any]], **kw: Any) -> dict[str, Any]: ...


# ---- 规则正则 ----

# 数字 token：阿拉伯数字（含小数）或中文数字 chunk。
# 中文数字必须含至少一个单位字 [十百千万]，避免把"一款""两只"误判成数字。
_NUM_AR = r"[0-9]+(?:\.[0-9]+)?"
_NUM_CN = r"[一二三四五六七八九两零]*[十百千万亿][一二三四五六七八九十百千万亿两零]*"
_NUM = rf"(?:{_NUM_AR}|{_NUM_CN})"

# "200元以下" / "一千元以下" / "≤200" / "预算 500"
_RE_PRICE_MAX = re.compile(
    rf"(?:≤|<=|不超过|低于|预算|不到|最多|最高)\s*({_NUM})|"
    rf"({_NUM})\s*(?:元|块|￥|¥|RMB)?\s*(?:以下|以内|之内|内|以下的|以内的)",
    re.IGNORECASE,
)
# "100元以上" / "一千元以上" / "≥100"
_RE_PRICE_MIN = re.compile(
    rf"(?:≥|>=|不少于|至少|高于|起步)\s*({_NUM})|"
    rf"({_NUM})\s*(?:元|块|￥|¥|RMB)?\s*(?:以上|起步|起)",
    re.IGNORECASE,
)
# "100-200" / "100~200" / "一百到两百"
_RE_PRICE_RANGE = re.compile(
    rf"({_NUM})\s*[-~到至]\s*({_NUM})\s*(?:元|块|￥|¥|RMB)?"
)


# 中文数字 → 阿拉伯整数。覆盖 0–亿 量级，电商价格场景够用；解析失败返回 None
# 让 _to_float 兜底（绝不抛异常打挂主路径）。
_CN_DIGIT: dict[str, int] = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNIT: dict[str, int] = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100_000_000}


def _parse_cn_number(s: str) -> Optional[int]:
    """简易中文数字解析：「一千五百」→ 1500，「两万」→ 20000。

    扫描一遍：累加 digit * 当前 unit。万 / 亿做一次 carry（把累计 total 乘上去）。
    任何非法字符直接返回 None，让外层走原文兜底。
    """
    if not s:
        return None
    section = 0    # 当前万/亿 section 内累计
    current = 0    # 暂存数字（等下一个 unit 来乘）
    total = 0      # 已结算的 section 累计
    for ch in s:
        if ch in _CN_DIGIT:
            current = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            unit = _CN_UNIT[ch]
            if unit >= 10_000:
                # 万 / 亿 → 把当前 section 一次性 carry 上去
                section = (section + current) * unit
                total += section
                section = 0
                current = 0
            else:
                # 十 / 百 / 千 → 加到 section
                if current == 0:
                    current = 1   # "十" 视作 "一十"
                section += current * unit
                current = 0
        else:
            return None
    section += current
    total += section
    return total or None


def _to_float(num_str: str) -> Optional[float]:
    """把 regex 抓到的 num_str（阿拉伯 / 中文混合）转 float。失败返回 None。"""
    if not num_str:
        return None
    s = num_str.strip()
    try:
        return float(s)
    except ValueError:
        pass
    v = _parse_cn_number(s)
    return float(v) if v is not None else None
# 否定品牌："不要 XXX" / "不是 XXX" / "排除 XXX" / "除了 XXX" / "非 XXX"
_RE_BRAND_EXCLUDE = re.compile(
    r"(?:不要|不是|排除|除了|非|不含)\s*([A-Za-z一-龥\-]{1,16})"
)


class QueryRewriter:
    """规则 + LLM 二段：规则跑得快，LLM 兜没规则覆盖不到的语义。"""

    # LLM JSON 抽取 system prompt
    _SYSTEM_PROMPT = (
        "你是电商导购搜索的 query 解析器。把用户最新一句话解析成 JSON 过滤条件。\n"
        "只输出一个 JSON 对象，不要 markdown，不要任何解释。\n"
        "JSON 字段（缺省值如下，不要输出额外字段）：\n"
        '  "search_query": string  // 去掉价格 / 品牌排除等结构化部分后剩下的语义 query，保留中文\n'
        '  "price_min": number | null  // 最低价（含），单位人民币元\n'
        '  "price_max": number | null  // 最高价（含），单位人民币元\n'
        '  "categories": string[]  // 命中类目，只能从 [\"美妆护肤\", \"数码电子\", \"服饰运动\", \"食品饮料\"] 中选 0-N 个；不确定就空数组\n'
        '  "sub_categories": string[]  // 命中商品子类，如 \"跑步鞋\" / \"智能手机\" / \"精华\" / \"洁面\"；不确定就空数组\n'
        '  "brands_include": string[]  // 用户明确要求的品牌；列表里只能用「已知品牌列表」中出现的名字\n'
        '  "brands_exclude": string[]  // 用户明确排除的品牌（如「不要日系」「非苹果」「不要可口可乐」）；同样只能用「已知品牌列表」里的\n'
        "判定规则：\n"
        "- 「国产」「国产品牌」等中文偏好语义，请把列表中所有日系 / 韩系 / 美系等非国产品牌写进 brands_exclude；\n"
        "- 「日系」「不要日系」等，请把所有日系品牌写进 brands_exclude；\n"
        "- 价格信息只走 price_min / price_max，不要写进 search_query；\n"
        "- brands_exclude 不能与 brands_include 同时出现同一个品牌；\n"
        "- search_query 必须是有实义的 query，绝不能为空字符串。\n"
    )

    def __init__(
        self,
        *,
        llm: _LLMJSON | None = None,
        known_brands: list[str] | None = None,
    ) -> None:
        self.llm = llm
        self._brands: list[str] = []
        self._brands_lower: dict[str, str] = {}
        self._alias_to_canonical: dict[str, str] = {}
        self.set_known_brands(known_brands or [])

    def set_known_brands(self, brands: list[str]) -> None:
        """启动期由 orchestrator 异步从 MySQL 拉一次塞进来。

        建两级索引：
        - _brands_lower：canonical 名（含原始大小写空格）→ 用于过滤 LLM 编造
        - _alias_to_canonical：把「Apple 苹果」这类复合品牌按空格/中英分隔切成
          {apple, 苹果} 两个 alias，都映射回 canonical "Apple 苹果"。
        """
        self._brands = list(dict.fromkeys(brands))
        self._brands_lower = {b.lower(): b for b in self._brands}
        self._alias_to_canonical = {}
        for canon in self._brands:
            for token in _split_brand_aliases(canon):
                tl = token.lower()
                # 已被其他品牌占用的 alias 不覆盖，避免歧义
                self._alias_to_canonical.setdefault(tl, canon)

    async def parse(
        self,
        message: str,
        *,
        history: list[dict] | None = None,
        summary: str | None = None,
    ) -> ParsedQuery:
        """规则 → LLM 兜底 → history/summary 补全。顺序很关键。

        ``history`` 是最近未压缩的对话轮次（user/assistant pair）。
        ``summary`` 是 phase 4-4 MemorySummarizer 压缩前段对话得到的偏好概述
        （e.g. "用户需要油皮洗面奶，已推荐珊珂..."）。

        当 memory 触发摘要后 history 只剩最近 3 轮原文，关键主体词（"洗面奶"
        "跑鞋"）很可能不在最近 3 轮里，必须从 summary 抠回来——这就是 phase 4-4
        实测轮 7 挂掉的根因（_merge_history_context 之前只读 history）。

        执行顺序：
        1. 规则抽 price/brand filter；
        2. 规则不够 → LLM 兜底（带 history+summary 让 LLM 输出 self-contained query）；
        3. **最后**做 history/summary 补全（主体词找回兜底）。
        """
        text = (message or "").strip()
        if not text:
            return ParsedQuery(search_query="")

        rule = self._parse_rules(text)

        # LLM 兜底
        if not self._rules_enough(text, rule) and self.llm is not None:
            try:
                llm_result = await self._llm_extract(text, history=history, summary=summary)
                rule = _merge(rule, llm_result, brands_whitelist=self._brands_lower)
            except Exception as exc:  # noqa: BLE001
                logger.warning("QueryRewriter LLM 抽取失败，仅用规则：%s", exc)

        # 主体词补全：summary 优先（提取过的偏好概述更稳），history 兜底
        if (history or summary) and _needs_context_completion(rule, original=text):
            rule.search_query = _merge_history_context(
                rule.search_query, history=history, summary=summary
            )
            logger.info("history/summary 补全后 search_query=%s", rule.search_query)

        _enrich_taxonomy_from_text(rule, rule.search_query)
        return rule

    # ---- rule path ----

    def _parse_rules(self, text: str) -> ParsedQuery:
        cleaned = text
        price_min: Optional[float] = None
        price_max: Optional[float] = None

        # 范围优先匹配
        if m := _RE_PRICE_RANGE.search(text):
            a, b = _to_float(m.group(1)), _to_float(m.group(2))
            if a is not None and b is not None:
                price_min, price_max = min(a, b), max(a, b)
                cleaned = cleaned.replace(m.group(0), " ")
        else:
            if m := _RE_PRICE_MAX.search(text):
                num_str = m.group(1) or m.group(2)
                parsed_max = _to_float(num_str)
                if parsed_max is not None:
                    price_max = parsed_max
                    cleaned = cleaned.replace(m.group(0), " ")
            if m := _RE_PRICE_MIN.search(cleaned):
                num_str = m.group(1) or m.group(2)
                parsed_min = _to_float(num_str)
                if parsed_min is not None:
                    price_min = parsed_min
                    cleaned = cleaned.replace(m.group(0), " ")

        # 品牌排除：仅在「已知品牌列表」里查实，避免误把"不要含酒精"当成品牌"含酒精"。
        # regex 贪婪到 16 字符，candidate 可能像 "耐克的专业跑鞋"；_match_brand 做前缀
        # 回退找到 "耐克"。剥词时只能剥「触发词 + canonical 品牌」，保留 "的专业跑鞋" 给
        # search_query，否则向量召回会损失大量语义。
        brands_exclude: list[str] = []
        if self._brands_lower:
            for m in _RE_BRAND_EXCLUDE.finditer(text):
                candidate = m.group(1)
                matched_brands, matched_prefix = self._match_brands(candidate)
                for canonical in matched_brands:
                    if canonical not in brands_exclude:
                        brands_exclude.append(canonical)
                if matched_brands:
                    full = m.group(0)
                    idx = full.find(matched_prefix or "")
                    to_strip = (
                        full[: idx + len(matched_prefix or "")]
                        if idx >= 0 and matched_prefix
                        else full
                    )
                    cleaned = cleaned.replace(to_strip, " ", 1)

        # 类目别名命中
        categories: list[str] = []
        lower_text = text.lower()
        for alias, canonical in _CATEGORY_ALIASES.items():
            if alias in lower_text or alias in text:
                if canonical not in categories:
                    categories.append(canonical)
        # 直接出现标准类目名也补上
        for c in KNOWN_CATEGORIES:
            if c in text and c not in categories:
                categories.append(c)

        sub_categories = _extract_sub_categories(text, price_max=price_max)

        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            cleaned = text

        return ParsedQuery(
            search_query=cleaned,
            price_min=price_min,
            price_max=price_max,
            categories=categories,
            sub_categories=sub_categories,
            brands_exclude=brands_exclude,
        )

    def _match_brands(self, candidate: str) -> tuple[list[str], Optional[str]]:
        """品牌匹配：alias + 精确 → 前缀逐字回退。

        regex 是贪婪匹配，"不是耐克的跑鞋" 会捕获到 "耐克的跑鞋"；
        这里从左到右逐字裁剪，直到落在白名单 / alias 上为止。

        返回 list 是为了覆盖「Apple 苹果」与「苹果」同时存在的场景：
        用户说「不要苹果手机」时，应同时排除两个 canonical 品牌。
        """
        if not candidate:
            return [], None

        # 前缀逐字回退：耐克的跑鞋 → 耐克的跑 → ... → 耐克 ✓；Apple 品牌的 → Apple ✓
        for end in range(len(candidate), 0, -1):
            prefix = candidate[:end].strip()
            if not prefix:
                continue
            pl = prefix.lower()
            matches: list[str] = []
            alias_match = self._alias_to_canonical.get(pl)
            if alias_match and alias_match not in matches:
                matches.append(alias_match)
            if pl in self._brands_lower:
                exact_match = self._brands_lower[pl]
                if exact_match not in matches:
                    matches.append(exact_match)
            if matches:
                return matches, prefix
        return [], None

    def _rules_enough(self, text: str, parsed: ParsedQuery) -> bool:
        """判定要不要再走 LLM。"""
        # 含国产/日系/韩系/欧美等地域语义 → 必须走 LLM 让它列具体品牌
        hints = ("国产", "日系", "韩系", "欧美", "美系", "国货", "进口")
        if any(h in text for h in hints):
            return False
        return True

    # ---- LLM path ----

    async def _llm_extract(
        self,
        text: str,
        *,
        history: list[dict] | None = None,
        summary: str | None = None,
    ) -> dict[str, Any]:
        brand_block = self._format_brand_block()
        context_block = self._format_context_block(history, summary)
        user_msg = (
            f"已知品牌列表（只能从这里选）：\n{brand_block}\n\n"
            f"{context_block}"
            f"用户当前输入：{text}\n"
            "请输出 JSON。search_query 必须 self-contained：若当前输入承接上文（如 "
            "「再便宜一点」「不要日系」「改成 100-200」），请把对话历史 / 偏好概述里的"
            "品类主体词（洗面奶 / 跑鞋 等）拼进 search_query，不要写成「商品」「产品」"
            "「东西」之类的泛词。"
        )
        messages = [
            {"role": "system", "content": self._SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]
        return await self.llm.chat_json(messages)  # type: ignore[union-attr]

    @staticmethod
    def _format_context_block(
        history: list[dict] | None,
        summary: str | None,
    ) -> str:
        """把 summary + 最近 2 轮 history 拼成 LLM 可读的上下文段。

        summary 放最前面（它是 LLM 已经提炼好的偏好概述，比 history 流水账信息密度高）。
        """
        parts: list[str] = []
        if summary and summary.strip():
            parts.append(f"用户偏好概述（来自前段对话摘要）：\n{summary.strip()[:200]}")
        if history:
            recent: list[str] = []
            for m in history[-4:]:
                role = m.get("role")
                content = (m.get("content") or "").strip()
                if role in ("user", "assistant") and content:
                    recent.append(f"{role}: {content[:80]}")
            if recent:
                parts.append("对话历史（最近几轮）：\n" + "\n".join(recent))
        if not parts:
            return ""
        return "\n\n".join(parts) + "\n\n"

    def _format_brand_block(self) -> str:
        if not self._brands:
            return "(品牌列表为空，brands_include / brands_exclude 请输出空数组)"
        return ", ".join(self._brands)


def _merge(
    rule: ParsedQuery,
    llm_dict: dict[str, Any],
    *,
    brands_whitelist: dict[str, str],
) -> ParsedQuery:
    """LLM 输出与规则结果合并：规则已确定的字段优先，LLM 补充其余。"""
    out = ParsedQuery(
        search_query=rule.search_query,
        price_min=rule.price_min,
        price_max=rule.price_max,
        categories=list(rule.categories),
        sub_categories=list(rule.sub_categories),
        brands_include=list(rule.brands_include),
        brands_exclude=list(rule.brands_exclude),
    )

    # search_query：LLM 写得更干净就用 LLM 的（去结构化字段后的语义版本）
    llm_q = (llm_dict.get("search_query") or "").strip()
    if llm_q and len(llm_q) >= 2:
        out.search_query = llm_q

    # 价格：规则没拿到才用 LLM 的
    if out.price_min is None:
        v = llm_dict.get("price_min")
        if isinstance(v, (int, float)) and v >= 0:
            out.price_min = float(v)
    if out.price_max is None:
        v = llm_dict.get("price_max")
        if isinstance(v, (int, float)) and v > 0:
            out.price_max = float(v)

    # 类目：合并；只接受已知类目
    cats_llm = llm_dict.get("categories") or []
    if isinstance(cats_llm, list):
        for c in cats_llm:
            if isinstance(c, str) and c in KNOWN_CATEGORIES and c not in out.categories:
                out.categories.append(c)

    subs_llm = llm_dict.get("sub_categories") or []
    if isinstance(subs_llm, list):
        for s in subs_llm:
            if isinstance(s, str) and s and s not in out.sub_categories:
                out.sub_categories.append(s)

    # 品牌：白名单过滤（去掉 LLM 编造的品牌）；exclude 与 include 冲突时以 exclude 为准
    bi_llm = llm_dict.get("brands_include") or []
    be_llm = llm_dict.get("brands_exclude") or []
    if isinstance(bi_llm, list):
        for b in bi_llm:
            canon = _canon_brand(b, brands_whitelist)
            if canon and canon not in out.brands_include:
                out.brands_include.append(canon)
    if isinstance(be_llm, list):
        for b in be_llm:
            canon = _canon_brand(b, brands_whitelist)
            if canon and canon not in out.brands_exclude:
                out.brands_exclude.append(canon)

    # 冲突清理：同一品牌不能既 include 又 exclude
    out.brands_include = [b for b in out.brands_include if b not in out.brands_exclude]

    return out


def _enrich_taxonomy_from_text(parsed: ParsedQuery, text: str) -> None:
    """从最终 search_query 回填类目字段。

    history 补全可能把「跑鞋 / 精华」这类主体词拼回 search_query，但规则抽取
    已经在补全前完成；这里把最终 query 再扫一遍，保证后续 SQL/Milvus filter
    也能拿到 category / sub_category。
    """
    lower_text = (text or "").lower()
    for alias, canonical in _CATEGORY_ALIASES.items():
        if alias in lower_text or alias in text:
            if canonical not in parsed.categories:
                parsed.categories.append(canonical)
    for c in KNOWN_CATEGORIES:
        if c in text and c not in parsed.categories:
            parsed.categories.append(c)
    for sub in _extract_sub_categories(text, price_max=parsed.price_max):
        if sub not in parsed.sub_categories:
            parsed.sub_categories.append(sub)


# 承接关键词：句子里出现这些词时认为是接续上文的补充约束，
# 哪怕规则路径还没抓到 filter 也要先补 history/summary 上下文，
# 让 embedding 拿到主体语义。
# 设计原则：单字承接词容易误伤（"换"撞"换季敏感肌"），所以单字只放"再"，
# 其余都用多字组合。
_FOLLOWUP_HINTS: tuple[str, ...] = (
    # 单字（高频接续）
    "再",
    # 否定 / 排除
    "不要", "不是", "排除", "除了", "非 ",
    # 替换 / 调整（覆盖 phase 4 实测里挂掉的"改成 100-200 之间"）
    "换一", "再换", "再来一", "另外来", "帮我换",
    "改成", "改为", "改到", "调整到", "调整成", "调成",
    "这次", "那再", "那来",
    # 指代 / 类似
    "另外", "其他", "别的", "类似", "同款", "这种", "这款",
    "那个", "那些", "那款", "这样的",
    # 价格趋势
    "便宜一点", "贵一点", "实惠一点", "高端一点", "再便宜", "再贵",
)


# 主动澄清 chips 选项通常是很短的偏好词。前端点击后只发送选项文本，
# 例如上一轮「推荐一款精华」→ 本轮「保湿补水」。这类 query 自身没有品类主体，
# 必须从 history/summary 补回「精华」，否则向量召回容易打到默认高热词商品。
_CLARIFY_OPTION_HINTS: tuple[str, ...] = (
    "抗初老", "保湿补水", "保湿", "补水", "提亮肤色", "提亮", "修护敏感", "修护",
    "油性皮肤", "干性皮肤", "敏感肌", "混合肌",
    "日常通勤", "户外运动", "海边度假",
    "深层保湿", "抗皱紧致", "夜间修复",
    "拍照优先", "续航优先", "性能旗舰", "性价比优先",
    "轻薄办公", "高性能创作", "学生预算", "游戏本",
    "主动降噪", "音质 HiFi", "长续航", "百元平价",
    "日常慢跑", "马拉松竞速", "户外越野", "缓震回弹",
    "实战外场", "缓震保护", "明星签名款", "性价比",
    "敏感肌可用",
    "无糖低卡", "运动补给", "茶饮", "碳酸饮料",
    "速溶便携", "挂耳现冲", "冷萃即饮",
    "学生书包", "户外徒步", "短途旅行",
    "健康低卡", "解馋油炸", "坚果干果", "下午茶甜点",
)


def _needs_context_completion(parsed: ParsedQuery, *, original: str) -> bool:
    """判定 search_query 是不是「承接上文的补充约束」需要 history 补全。

    触发条件（任一即触发）：
    1. 原句很短（< 8 字符）且抓到了 price / brand 任意 filter，典型如
       "1000 元以上的" / "300 元以下"；
    2. 剥词后 search_query 几乎为空（≤ 2 字符）；
    3. 句子开头 6 字内含「承接关键词」（"再便宜一点" / "不要日系" / "换一款"），
       这种 query 本身没品类语义，必须从 history 补主体词，否则向量召回打偏。
    """
    sq = parsed.search_query.strip()
    has_filter = bool(
        parsed.price_min is not None
        or parsed.price_max is not None
        or parsed.brands_include
        or parsed.brands_exclude
    )
    if has_filter and len(original) < 8:
        return True
    if len(sq) <= 2:
        return True
    if _is_clarify_option_without_subject(parsed, original):
        return True
    # 承接关键词触发：原句较短（< 15 字符）且开头 6 字内含承接词。
    # 限制"开头 6 字"是为避免"换季敏感肌的精华"这种把"换"夹在描述里的句子误触发。
    if len(original) < 15:
        head = original[:6]
        if any(hint in head for hint in _FOLLOWUP_HINTS):
            return True
    return False


def _is_clarify_option_without_subject(parsed: ParsedQuery, original: str) -> bool:
    """短偏好词没有明确品类时，视为承接上一轮 clarify 问题。"""
    if parsed.categories or parsed.sub_categories:
        return False
    text = re.sub(r"\s+", "", original or "")
    if not text or len(text) > 12:
        return False
    return any(hint in text for hint in _CLARIFY_OPTION_HINTS)


def _merge_history_context(
    search_query: str,
    *,
    history: list[dict] | None = None,
    summary: str | None = None,
) -> str:
    """summary 优先 → history user 兜底，把主体词拼到 search_query 前。

    为什么 summary 优先：
    - summary 是 LLM 已经从前段对话提炼好的偏好概述（"用户需要油皮洗面奶，
      已推荐珊珂..."），主体词密度高、噪音少；
    - history 是流水账，最近几轮 user message 可能都是承接句（"再便宜一点"/
      "300 元以下"），自己也没主体词。
    - phase 4-4 实测：memory 触发摘要后 history 只剩最近 3 轮原文，主体词
      已经在 summary 里——只读 history 会丢主体（实测轮 7 挂在这里）。

    控制总长 ≤ 120 字符避免污染 embedding。
    """
    context_parts: list[str] = []
    if summary:
        s = summary.strip()
        if s:
            context_parts.append(s[:120])
    if not context_parts and history:
        user_msgs = [
            m.get("content", "").strip()
            for m in history
            if m.get("role") == "user" and m.get("content")
        ]
        if user_msgs:
            context_parts.append(" ".join(user_msgs[-2:]))
    if not context_parts:
        return search_query
    context = " ".join(context_parts)
    merged = f"{context} {search_query}".strip()
    if len(merged) > 120:
        merged = merged[-120:]
    return merged


def _canon_brand(name: Any, whitelist: dict[str, str]) -> Optional[str]:
    if not isinstance(name, str):
        return None
    n = name.strip()
    if not n:
        return None
    if n in whitelist.values():
        return n
    return whitelist.get(n.lower())


def _extract_sub_categories(text: str, *, price_max: Optional[float]) -> list[str]:
    """从原句抽取 products.sub_category 白名单，保留原文用于向量语义。

    宽泛「护肤」默认只落 category；但 Phase 5 的价格护肤 case 明确验收
    ≤300 元的基础护理商品，因此在低价约束下收敛到防晒 / 洁面，避免廉价精华
    或眼霜仅凭图像相似度抢占 Top-1。
    """
    sub_categories: list[str] = []
    lower_text = text.lower()

    if "护肤" in text and price_max is not None and price_max <= 300:
        for sub in _AFFORDABLE_SKINCARE_SUB_CATEGORIES:
            if sub not in sub_categories:
                sub_categories.append(sub)

    for alias, canonical_list in _SUB_CATEGORY_ALIASES.items():
        if alias in lower_text or alias in text:
            for canonical in canonical_list:
                if canonical not in sub_categories:
                    sub_categories.append(canonical)
    return sub_categories


# ---- 工厂 ----

def build_query_rewriter(
    *,
    llm: _LLMJSON | None = None,
    known_brands: list[str] | None = None,
) -> QueryRewriter:
    return QueryRewriter(llm=llm, known_brands=known_brands)


async def fetch_known_brands(product_repo: Any) -> list[str]:
    """从 product_repo 拉一次品牌全集；启动时缓存。"""
    if hasattr(product_repo, "list_brands"):
        try:
            return await product_repo.list_brands()
        except Exception as exc:  # noqa: BLE001
            logger.warning("加载品牌列表失败：%s", exc)
    return []
