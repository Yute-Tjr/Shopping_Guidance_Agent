"""主动澄清（Phase 4-3）：判定 recommend 意图下信息是否充足，不足则反问 + chips。

为什么不光靠 IntentRouter 的「字数 < 3 → clarify_needed」：
- 用户写"推荐一款手机"长度 6 字符够过阈值，但语义上极度泛泛，向量召回会
  把所有手机都打成接近分数，LLM 只能瞎推一台，体验差且容易"假装懂用户"。
- docs/01 §Phase 4 验收 query #5 明确点了这条场景。

判定逻辑（保守，宁可漏不要误伤）：
1. 必须是 recommend 意图（compare/cart_op/clarify_needed 都不走这里）；
2. QueryRewriter 输出的 ParsedQuery 里 price / brand_include / brand_exclude 全为空
   （用户给了价格 / 品牌偏好就视为有约束）；
3. 把 message 剥掉「推荐/一款/给我/帮我/有什么/什么样」等通用词后，
   剩余字符必须落在某个「大类目触发词」表里（手机 / 笔记本 / 耳机 / 跑鞋 /
   洗面奶 / 防晒 / 精华 / 面霜 / 饮料 / 咖啡 / 背包），且剥词后**没有任何**
   形容词性描述（"拍照""轻薄""油皮""高倍""降噪"等）；
4. 命中后查类目模板拿出 chips 选项。

不触发 clarify 的反例：
- "推荐一款拍照好的手机" → 剥词后剩"拍照好的"，有具体描述，跳过；
- "推荐一款适合油皮的洗面奶" → 剩"适合油皮的"，跳过；
- "300 元以下的防晒霜" → ParsedQuery.price_max=300，跳过；
- "对比兰蔻和雅诗兰黛"  → compare 意图，跳过。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from app.agent.query_rewriter import ParsedQuery
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ClarifyDecision:
    """detector 输出。should_clarify=False 时其他字段无意义。"""

    should_clarify: bool
    question: str = ""
    options: list[str] = field(default_factory=list)
    category_key: str = ""   # 命中的模板 key，方便日志 / 测试断言


# 通用词：剥掉这些再判定剩余信息密度。不区分顺序 / 重复。
_GENERIC_WORDS: tuple[str, ...] = (
    "推荐", "一款", "一个", "一双", "一只", "一些", "几款",
    "来一款", "来一个", "来一双", "来一只", "来",
    "给我", "帮我", "请", "麻烦",
    "有什么", "有没有", "什么", "啥",
    "想买", "买个", "买一个", "买点", "求", "找一款", "找一个", "找点",
    "看看", "看下", "看一下", "瞧瞧", "瞅瞅",
    "个", "款", "只", "双",
    "的", "吧", "呢", "呀", "啊", "哦", "嘛",
)

# 类目触发词 → chips 模板。
# 设计原则：选项必须互斥且能直接映射回数据集 100 条商品里某个 chunk_type / sub_category。
_CATEGORY_TEMPLATES: dict[str, dict] = {
    "手机": {
        "question": "想看哪种风格的手机？",
        "options": ["拍照优先", "续航优先", "性能旗舰", "性价比优先"],
        "aliases": ("手机", "智能手机"),
    },
    "笔记本": {
        "question": "笔记本主要做什么用？",
        "options": ["轻薄办公", "高性能创作", "学生预算", "游戏本"],
        "aliases": ("笔记本", "笔记本电脑", "电脑"),
    },
    "耳机": {
        "question": "对耳机最看重什么？",
        "options": ["主动降噪", "音质 HiFi", "长续航", "百元平价"],
        "aliases": ("耳机", "蓝牙耳机", "真无线耳机"),
    },
    "跑鞋": {
        "question": "跑鞋主要用在什么场景？",
        "options": ["日常慢跑", "马拉松竞速", "户外越野", "缓震回弹"],
        "aliases": ("跑鞋", "跑步鞋", "运动鞋"),
    },
    "篮球鞋": {
        "question": "篮球鞋偏好哪种？",
        "options": ["实战外场", "缓震保护", "明星签名款", "性价比"],
        "aliases": ("篮球鞋",),
    },
    "洗面奶": {
        "question": "你的肤质偏向？",
        "options": ["油性皮肤", "干性皮肤", "敏感肌", "混合肌"],
        "aliases": ("洗面奶", "洁面", "洁面乳"),
    },
    "防晒": {
        "question": "防晒主要用在哪种场景？",
        "options": ["日常通勤", "户外运动", "海边度假", "敏感肌可用"],
        "aliases": ("防晒", "防晒霜", "防晒乳"),
    },
    "精华": {
        "question": "精华最希望解决哪个问题？",
        "options": ["抗初老", "保湿补水", "提亮肤色", "修护敏感"],
        "aliases": ("精华", "精华液", "精华露"),
    },
    "面霜": {
        "question": "你最看重面霜的哪种功效？",
        "options": ["深层保湿", "抗皱紧致", "修护敏感", "夜间修复"],
        "aliases": ("面霜", "乳液"),
    },
    "饮料": {
        "question": "想喝哪一类？",
        "options": ["无糖低卡", "运动补给", "茶饮", "碳酸饮料"],
        "aliases": ("饮料", "饮品"),
    },
    "咖啡": {
        "question": "想要哪种咖啡？",
        "options": ["速溶便携", "挂耳现冲", "冷萃即饮", "无糖低卡"],
        "aliases": ("咖啡",),
    },
    "背包": {
        "question": "背包主要用在哪里？",
        "options": ["日常通勤", "学生书包", "户外徒步", "短途旅行"],
        "aliases": ("背包", "双肩包", "登山包"),
    },
    "零食": {
        "question": "零食偏好哪种？",
        "options": ["健康低卡", "解馋油炸", "坚果干果", "下午茶甜点"],
        "aliases": ("零食", "小吃"),
    },
}


# alias → canonical key 反向索引（启动期构造一次）
_ALIAS_TO_KEY: dict[str, str] = {}
for _key, _tpl in _CATEGORY_TEMPLATES.items():
    for _alias in _tpl["aliases"]:
        _ALIAS_TO_KEY[_alias] = _key


# 具象修饰词：query 里出现任何一个就视为"用户已给了足够具体诉求"，不触发 clarify。
# 主动维护这一份白名单，避免漏掉应该正常 recommend 的场景。
_SPECIFIC_HINTS: tuple[str, ...] = (
    # 肤质 / 美妆
    "油皮", "干皮", "敏感肌", "混合肌", "痘肌", "美白", "抗老", "抗初老",
    "保湿", "补水", "紧致", "提亮", "修护",
    # 防晒 / 户外
    "高倍", "防水", "户外", "海边", "通勤", "学生",
    # 数码
    "拍照", "影像", "续航", "长续航", "轻薄", "游戏", "降噪", "音质", "高性能",
    "折叠屏", "高刷", "无线", "蓝牙", "降噪耳机",
    # 跑鞋 / 服饰
    "马拉松", "慢跑", "越野", "缓震", "竞速", "实战", "训练", "篮球",
    "瑜伽", "徒步", "登山",
    # 饮品
    "无糖", "低卡", "茶", "果汁", "可乐", "碳酸",
    # 通用价格 / 品牌（已经被 ParsedQuery 抽到了，这里再兜一层）
    "便宜", "性价比", "高端", "旗舰",
)


_WHITESPACE_RE = re.compile(r"\s+")
_PUNCT_TAIL_RE = re.compile(r"[？?！!。，,. ]+$")


class ClarifyDetector:
    """规则版主动澄清判定。当前不接 LLM，模板覆盖率已能撑住验收 query。"""

    def assess(
        self,
        *,
        intent_name: str,
        message: str,
        parsed: Optional[ParsedQuery] = None,
    ) -> Optional[ClarifyDecision]:
        """返回 None 表示不需要澄清；返回 ClarifyDecision 表示要短路 emit。"""
        # 1) 只对 recommend 意图触发；compare / cart_op / clarify_needed 各自走自己的分支
        if intent_name != "recommend":
            return None

        text = (message or "").strip()
        if not text:
            return None

        # 2) ParsedQuery 里有任何结构化约束（价格 / 品牌 include/exclude）→ 视为有信息
        if parsed is not None:
            if parsed.price_min is not None or parsed.price_max is not None:
                return None
            if parsed.brands_include or parsed.brands_exclude:
                return None

        # 3) query 里出现任何具象修饰词 → 视为有信息
        if any(hint in text for hint in _SPECIFIC_HINTS):
            return None

        # 4) 剥掉通用词，看剩余是否只剩某个大类目触发词
        stripped = _strip_generic_words(text)
        category_key = _match_category(stripped)
        if category_key is None:
            return None

        tpl = _CATEGORY_TEMPLATES[category_key]
        logger.info("clarify 触发：category=%s, original=%s, stripped=%s",
                    category_key, text, stripped)
        return ClarifyDecision(
            should_clarify=True,
            question=tpl["question"],
            options=list(tpl["options"]),
            category_key=category_key,
        )


def _strip_generic_words(text: str) -> str:
    """把通用词全部剥掉，再归一化空白与尾标点。"""
    out = text
    for w in _GENERIC_WORDS:
        out = out.replace(w, " ")
    out = _WHITESPACE_RE.sub(" ", out).strip()
    out = _PUNCT_TAIL_RE.sub("", out).strip()
    return out


def _match_category(stripped: str) -> Optional[str]:
    """剥词后必须恰好等于（或几乎等于）某个 alias 才算命中。

    放严判定避免误伤："不要含酒精的防晒霜" 剥词后还有"不要含酒精"，不应触发。
    """
    if not stripped:
        return None
    # 完整等于
    if stripped in _ALIAS_TO_KEY:
        return _ALIAS_TO_KEY[stripped]
    # 容忍 1 个尾部修饰字（如"手机推荐"剥词后变"手机推"）
    for alias, key in _ALIAS_TO_KEY.items():
        if stripped == alias:
            return key
        # 严格条件：stripped 只比 alias 多 1-2 字符且仍是 alias 的子串关系才算
        if alias in stripped and len(stripped) <= len(alias) + 2:
            # 剥剩部分是否都是无意义连接词
            residual = stripped.replace(alias, "").strip()
            if residual in ("", "推", "推荐", "啊", "呢", "吧"):
                return key
    return None


def build_clarify_detector() -> ClarifyDetector:
    return ClarifyDetector()
