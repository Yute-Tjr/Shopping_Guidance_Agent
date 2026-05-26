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


@dataclass
class ParsedQuery:
    """Rewriter 输出。"""

    search_query: str                         # 喂 embedder 的语义 query
    price_min: Optional[float] = None         # 最低价（含），单位元
    price_max: Optional[float] = None         # 最高价（含），单位元
    categories: list[str] = field(default_factory=list)        # 类目白名单
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

# "200元以下" / "200 块以内" / "≤200" / "200 以下"
_RE_PRICE_MAX = re.compile(
    r"(?:≤|<=|不超过|低于|预算|不到|最多|最高)\s*([0-9]+(?:\.[0-9]+)?)|"
    r"([0-9]+(?:\.[0-9]+)?)\s*(?:元|块|￥|¥|RMB)?\s*(?:以下|以内|之内|内|以下的|以内的)",
    re.IGNORECASE,
)
# "100元以上" / "≥100" / "至少 100"
_RE_PRICE_MIN = re.compile(
    r"(?:≥|>=|不少于|至少|高于|起步)\s*([0-9]+(?:\.[0-9]+)?)|"
    r"([0-9]+(?:\.[0-9]+)?)\s*(?:元|块|￥|¥|RMB)?\s*(?:以上|起步|起)",
    re.IGNORECASE,
)
# "100-200" / "100~200" / "100到200"
_RE_PRICE_RANGE = re.compile(
    r"([0-9]+(?:\.[0-9]+)?)\s*[-~到至]\s*([0-9]+(?:\.[0-9]+)?)\s*(?:元|块|￥|¥|RMB)?"
)
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

    async def parse(self, message: str) -> ParsedQuery:
        """规则跑一遍 → 不够再调 LLM 补缺；最终合并返回。"""
        text = (message or "").strip()
        if not text:
            return ParsedQuery(search_query="")

        rule = self._parse_rules(text)

        # 规则已能定下价格 + 品牌排除，且没有"日系/国产/韩系/欧美"这类需要语义推断的词
        # 时不再调 LLM，省一次往返。
        if self._rules_enough(text, rule):
            return rule

        if self.llm is None:
            return rule  # 没接 LLM 也别崩

        try:
            llm_result = await self._llm_extract(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("QueryRewriter LLM 抽取失败，仅用规则：%s", exc)
            return rule

        return _merge(rule, llm_result, brands_whitelist=self._brands_lower)

    # ---- rule path ----

    def _parse_rules(self, text: str) -> ParsedQuery:
        cleaned = text
        price_min: Optional[float] = None
        price_max: Optional[float] = None

        # 范围优先匹配
        if m := _RE_PRICE_RANGE.search(text):
            a, b = float(m.group(1)), float(m.group(2))
            price_min, price_max = min(a, b), max(a, b)
            cleaned = cleaned.replace(m.group(0), " ")
        else:
            if m := _RE_PRICE_MAX.search(text):
                num_str = m.group(1) or m.group(2)
                if num_str:
                    price_max = float(num_str)
                    cleaned = cleaned.replace(m.group(0), " ")
            if m := _RE_PRICE_MIN.search(cleaned):
                num_str = m.group(1) or m.group(2)
                if num_str:
                    price_min = float(num_str)
                    cleaned = cleaned.replace(m.group(0), " ")

        # 品牌排除：仅在「已知品牌列表」里查实，避免误把"不要含酒精"当成品牌"含酒精"。
        # regex 贪婪到 16 字符，candidate 可能像 "耐克的专业跑鞋"；_match_brand 做前缀
        # 回退找到 "耐克"。剥词时只能剥「触发词 + canonical 品牌」，保留 "的专业跑鞋" 给
        # search_query，否则向量召回会损失大量语义。
        brands_exclude: list[str] = []
        if self._brands_lower:
            for m in _RE_BRAND_EXCLUDE.finditer(text):
                candidate = m.group(1)
                canonical = self._match_brand(candidate)
                if canonical and canonical not in brands_exclude:
                    brands_exclude.append(canonical)
                    full = m.group(0)
                    idx = full.find(canonical)
                    to_strip = full[: idx + len(canonical)] if idx >= 0 else full
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

        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            cleaned = text

        return ParsedQuery(
            search_query=cleaned,
            price_min=price_min,
            price_max=price_max,
            categories=categories,
            brands_exclude=brands_exclude,
        )

    def _match_brand(self, candidate: str) -> Optional[str]:
        """品牌匹配：精确 → alias → 前缀逐字回退。

        regex 是贪婪匹配，"不是耐克的跑鞋" 会捕获到 "耐克的跑鞋"；
        这里从左到右逐字裁剪，直到落在白名单 / alias 上为止。
        """
        if not candidate:
            return None
        cand_lower = candidate.lower().strip()
        # 精确命中（包含完整 canonical 名）
        if cand_lower in self._brands_lower:
            return self._brands_lower[cand_lower]
        if cand_lower in self._alias_to_canonical:
            return self._alias_to_canonical[cand_lower]
        # 前缀逐字回退：耐克的跑鞋 → 耐克的跑 → ... → 耐克 ✓；Apple 品牌的 → Apple ✓
        for end in range(len(candidate), 0, -1):
            prefix = candidate[:end].strip()
            if not prefix:
                continue
            pl = prefix.lower()
            if pl in self._brands_lower:
                return self._brands_lower[pl]
            if pl in self._alias_to_canonical:
                return self._alias_to_canonical[pl]
        return None

    def _rules_enough(self, text: str, parsed: ParsedQuery) -> bool:
        """判定要不要再走 LLM。"""
        # 含国产/日系/韩系/欧美等地域语义 → 必须走 LLM 让它列具体品牌
        hints = ("国产", "日系", "韩系", "欧美", "美系", "国货", "进口")
        if any(h in text for h in hints):
            return False
        return True

    # ---- LLM path ----

    async def _llm_extract(self, text: str) -> dict[str, Any]:
        brand_block = self._format_brand_block()
        user_msg = (
            f"已知品牌列表（只能从这里选）：\n{brand_block}\n\n"
            f"用户输入：{text}\n"
            "请输出 JSON。"
        )
        messages = [
            {"role": "system", "content": self._SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]
        return await self.llm.chat_json(messages)  # type: ignore[union-attr]

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


def _canon_brand(name: Any, whitelist: dict[str, str]) -> Optional[str]:
    if not isinstance(name, str):
        return None
    n = name.strip()
    if not n:
        return None
    if n in whitelist.values():
        return n
    return whitelist.get(n.lower())


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
