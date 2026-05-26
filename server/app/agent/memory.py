"""会话短期记忆（进程内字典版）。

Phase 2：保留最近 max_turns 轮，超出 FIFO 截断。
Phase 4-4：加摘要压缩 —— 超过 summary_after_turns 时把前段历史交给 LLM 压成
            ≤100 字的用户偏好概述，写入 session.summary；history 只保留最近
            keep_recent_turns 轮原文。这样既不丢老约束（油皮 / 预算 / 不要日系），
            又能把 prompt token 控制住。

跨进程持久化（Redis / MySQL chat_messages）留给后续工程化阶段。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass
class Session:
    id: str
    history: list[dict] = field(default_factory=list)
    last_recommended_ids: list[str] = field(default_factory=list)
    # Phase 4-4：早期 N 轮被 LLM 压成的偏好概述，供 prompts 拼 system 段
    summary: str | None = None


class ConversationMemory:
    """所有 session 共享一个字典。Phase 2 单进程跑足够。"""

    def __init__(
        self,
        max_turns: int = 12,
        summary_after_turns: int = 6,
        keep_recent_turns: int = 3,
    ) -> None:
        """
        max_turns         硬上限。即使 summarizer 不可用，history 也不会无限增长。
        summary_after_turns 触发摘要的阈值（user+assistant 对数）。
        keep_recent_turns 摘要触发后保留的最近原文轮数；要 < summary_after_turns。
        """
        assert keep_recent_turns < summary_after_turns, "keep_recent 必须小于 summary_after"
        self.max_turns = max_turns
        self.summary_after_turns = summary_after_turns
        self.keep_recent_turns = keep_recent_turns
        self._sessions: dict[str, Session] = {}

    def get_or_create(self, session_id: str | None) -> Session:
        if not session_id:
            session_id = uuid.uuid4().hex
        session = self._sessions.get(session_id)
        if session is None:
            session = Session(id=session_id)
            self._sessions[session_id] = session
        return session

    def save_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
        recommended_ids: list[str],
    ) -> None:
        session = self.get_or_create(session_id)
        session.history.append({"role": "user", "content": user_message})
        session.history.append({"role": "assistant", "content": assistant_message})
        # 硬上限：summarizer 没注入时退化为 FIFO 截断
        max_msgs = self.max_turns * 2
        if len(session.history) > max_msgs:
            session.history = session.history[-max_msgs:]
        session.last_recommended_ids = list(recommended_ids)

    # ---- Phase 4-4 摘要相关 ----

    def needs_summary(self, session: Session) -> bool:
        """轮数（user+assistant 对）超过阈值时返回 True。"""
        return len(session.history) >= self.summary_after_turns * 2

    def get_history_to_summarize(self, session: Session) -> list[dict]:
        """返回要被摘要掉的前段 history（保留最近 keep_recent_turns 轮原文）。"""
        keep = self.keep_recent_turns * 2
        if keep <= 0:
            return list(session.history)
        return list(session.history[:-keep])

    def apply_summary(self, session_id: str, summary: str) -> None:
        """把 summary 写入 session 并截断 history 到最近 keep_recent_turns 轮。"""
        session = self.get_or_create(session_id)
        if summary:
            session.summary = summary.strip()
        keep = self.keep_recent_turns * 2
        if keep > 0:
            session.history = session.history[-keep:]
        else:
            session.history = []


# 单例：API 层共用同一个 ConversationMemory，避免 SSE 接口每个请求新建丢失上下文
_global_memory: ConversationMemory | None = None


def get_memory() -> ConversationMemory:
    global _global_memory
    if _global_memory is None:
        _global_memory = ConversationMemory()
    return _global_memory
