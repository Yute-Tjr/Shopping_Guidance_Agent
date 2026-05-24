"""会话短期记忆（进程内字典版，仅供 Phase 2 演示使用）。

Phase 4 升级方向（docs/03 §5.4）：
- 超过 N 轮自动 LLM 摘要前段历史
- 跨进程持久化时切到 Redis / MySQL chat_messages 表

当前实现只做：
- session_id 缺失时生成 UUID
- save_turn 追加 user/assistant 两条到 history，并刷新 last_recommended_ids
- max_turns 软上限，超过 FIFO 截断（一轮 = user + assistant 各一条）
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass
class Session:
    id: str
    history: list[dict] = field(default_factory=list)
    last_recommended_ids: list[str] = field(default_factory=list)


class ConversationMemory:
    """所有 session 共享一个字典。Phase 2 单进程跑足够。"""

    def __init__(self, max_turns: int = 6) -> None:
        self.max_turns = max_turns
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
        # 一轮 = 2 条 message；max_turns 是允许保留的 user/assistant 对数
        max_msgs = self.max_turns * 2
        if len(session.history) > max_msgs:
            session.history = session.history[-max_msgs:]
        session.last_recommended_ids = list(recommended_ids)


# 单例：API 层共用同一个 ConversationMemory，避免 SSE 接口每个请求新建丢失上下文
_global_memory: ConversationMemory | None = None


def get_memory() -> ConversationMemory:
    global _global_memory
    if _global_memory is None:
        _global_memory = ConversationMemory()
    return _global_memory
