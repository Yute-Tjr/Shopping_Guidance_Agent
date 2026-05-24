"""Doubao Chat 流式客户端封装。

设计要点：
- 走 AsyncArk + OpenAI 兼容 chat.completions.create(stream=True)；
- 业务侧只关心 chat_stream(messages) -> AsyncIterator[str]，每次 yield 增量 token；
- 空 delta / 心跳 chunk 直接忽略，省一帧 SSE 也省客户端拼接负担；
- 超时与重试：SDK 自带 max_retries；timeout 走全局 60s 默认即可，
  Phase 2 验收要求 30s 内出首 token，read=600 留足边界；
- tool_calls / function call 字段 Phase 2 不消费，留位置不报错。
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Iterable

from volcenginesdkarkruntime import AsyncArk

from app.utils.logger import get_logger

logger = get_logger(__name__)


class DoubaoChatClient:
    """业务侧统一入口：chat_stream(messages) → 增量 token 异步迭代。"""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout_seconds: float = 60.0,
        max_retries: int = 1,
    ) -> None:
        self.model = model
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": timeout_seconds,
            "max_retries": max_retries,
        }
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncArk(**kwargs)

    async def chat_stream(
        self,
        messages: Iterable[dict[str, Any]],
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        """流式调用，逐 token yield 文本内容。

        非 content 的 chunk（usage-only / tool_calls 段头）直接跳过，
        Phase 2 不用 tool；Phase 5A 业务闭环再扩展。
        """
        create_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "stream": True,
            "temperature": temperature,
        }
        if max_tokens is not None:
            create_kwargs["max_tokens"] = max_tokens
        if tools:
            create_kwargs["tools"] = tools

        stream = await self._client.chat.completions.create(**create_kwargs)
        async for chunk in stream:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue
            content = getattr(delta, "content", None)
            if content:
                yield content

    async def aclose(self) -> None:
        """关闭底层 httpx client（lifespan 中可调用）。"""
        try:
            await self._client.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("AsyncArk close 失败：%s", exc)


def build_chat_client_from_settings() -> DoubaoChatClient:
    """读取 app.config.settings 构造，避免每个调用方重复读 env。"""
    from app.config import settings

    return DoubaoChatClient(
        api_key=settings.ark_api_key,
        model=settings.ark_model,
        base_url=settings.ark_base_url,
    )
