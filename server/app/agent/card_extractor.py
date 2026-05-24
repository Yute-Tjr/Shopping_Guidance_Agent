"""流式 LLM token 中识别 ```product_cards 围栏并抽出商品卡片。

Phase 2 的卡片协议（与 prompts.py 内 Prompt 约束一致）：

    LLM 在回复末尾输出形如：

        ```product_cards
        [{"product_id":"p_xxx", "reason":"30 字内推荐理由"}, ...]
        ```

抽取器的职责：
1. **围栏外**的 token 原样回传给上层（最终 SSE event:token）。
2. **围栏内**的内容累积；闭合时解析 JSON、用 allowed_ids 过滤掉编造的
   product_id（防幻觉关键一环）；过滤后逐条 emit 卡片 dict（只含
   product_id / reason，详细字段由 Orchestrator 用 MySQL 检索结果补全）。
3. 围栏的 marker / JSON 不允许出现在用户可见文本里——即便围栏未闭合。
4. 流式 token 可能把围栏 marker 切成任意碎片，feed 必须容错。

非目标：不在抽取器里做 product 字段拼装（解耦：上层拿到 product_id 后
查 retrieved_products 并合并 title / brand / image / sku / price）。
"""
from __future__ import annotations

import json
from typing import Iterable

FENCE_OPEN = "```product_cards"
FENCE_CLOSE = "```"
# reason 字段超长截断，与 schemas.chat.ProductCardEvent.reason max_length 对齐
REASON_MAX_LEN = 120


class ProductCardExtractor:
    """逐 token 推送、按围栏切分文本与卡片。"""

    def __init__(self, allowed_ids: Iterable[str]) -> None:
        self.allowed_ids = set(allowed_ids)
        # NORMAL 状态下未确定能否安全 emit 的尾巴（可能是 fence 前缀）
        self._buffer = ""
        # IN_FENCE 状态下累积的围栏内容（待找闭合 ```）
        self._fence_buffer = ""
        self._in_fence = False

    # ---------- 主入口 ----------

    def feed(self, chunk: str) -> tuple[str, list[dict]]:
        """喂入一段新 token，返回 (本次新增可见文本, 本次新增卡片列表)。"""
        if not chunk:
            return "", []
        visible: list[str] = []
        cards: list[dict] = []
        self._process(chunk, visible, cards)
        return "".join(visible), cards

    def finalize(self) -> tuple[str, list[dict]]:
        """流结束时调用一次。

        - NORMAL：把残留 buffer 整段吐出（此时已经不可能是 fence 前缀）。
        - IN_FENCE：围栏未闭合，整段丢弃，绝不当正文吐——LLM 中途截断时
          残缺的 product_cards JSON 流给用户会被截图当"AI 编造商品"。
        """
        if self._in_fence:
            self._fence_buffer = ""
            self._in_fence = False
            return "", []
        out = self._buffer
        self._buffer = ""
        return out, []

    # ---------- 内部递归 ----------

    def _process(self, chunk: str, visible: list[str], cards: list[dict]) -> None:
        if self._in_fence:
            self._fence_buffer += chunk
            close_idx = self._fence_buffer.find(FENCE_CLOSE)
            if close_idx == -1:
                # 还没闭合，继续等下一段
                return
            json_text = self._fence_buffer[:close_idx]
            cards.extend(self._parse_cards(json_text))
            remaining = self._fence_buffer[close_idx + len(FENCE_CLOSE):]
            self._fence_buffer = ""
            self._in_fence = False
            # 围栏后还有正文 → 回到 NORMAL 继续处理
            if remaining:
                self._process(remaining, visible, cards)
            return

        # NORMAL 状态
        self._buffer += chunk
        open_idx = self._buffer.find(FENCE_OPEN)
        if open_idx != -1:
            # 把围栏前的文本吐出去；
            # 紧跟开口 marker 的换行不算正文（visually 是一段空行），吃掉一个 \n。
            visible.append(self._buffer[:open_idx])
            after = self._buffer[open_idx + len(FENCE_OPEN):]
            if after.startswith("\r\n"):
                after = after[2:]
            elif after.startswith("\n"):
                after = after[1:]
            self._buffer = ""
            self._in_fence = True
            if after:
                self._process(after, visible, cards)
            return

        # 没找到 marker：可以安全吐出的部分 = buffer 去掉「可能是 fence 前缀」的尾巴
        hold = self._suffix_overlap_with(FENCE_OPEN)
        emit_end = len(self._buffer) - hold
        if emit_end > 0:
            visible.append(self._buffer[:emit_end])
            self._buffer = self._buffer[emit_end:]
        # 否则全部 hold，等待更多 token

    def _suffix_overlap_with(self, marker: str) -> int:
        """返回 buffer 末尾与 marker 前缀的最长重合长度。

        例：buffer 末尾是 "``"，marker="```product_cards"，重合 2。
        重合 == len(marker) 时实际上 marker 已在 buffer 里，外层 find 会先命中。
        """
        max_k = min(len(self._buffer), len(marker))
        for k in range(max_k, 0, -1):
            if self._buffer.endswith(marker[:k]):
                return k
        return 0

    def _parse_cards(self, text: str) -> list[dict]:
        text = text.strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        out: list[dict] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            pid = item.get("product_id")
            if not isinstance(pid, str) or pid not in self.allowed_ids:
                continue
            reason = str(item.get("reason") or "").strip()
            out.append({"product_id": pid, "reason": reason[:REASON_MAX_LEN]})
        return out
