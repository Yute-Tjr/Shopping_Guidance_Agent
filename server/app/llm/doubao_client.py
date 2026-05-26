"""Doubao Chat 流式客户端封装。

设计要点：
- 走 AsyncArk + OpenAI 兼容 chat.completions.create(stream=True)；
- 业务侧只关心 chat_stream(messages) -> AsyncIterator[str]，每次 yield 增量 token；
- 空 delta / 心跳 chunk 直接忽略，省一帧 SSE 也省客户端拼接负担；
- 超时与重试：SDK 自带 max_retries；timeout 走全局 60s 默认即可，
  Phase 2 验收要求 30s 内出首 token，read=600 留足边界；
- tool_calls / function call 字段 Phase 2 不消费，留位置不报错；
- Phase 4 新增 chat_json：非流式 JSON 模式抽取，给 QueryRewriter 用。
"""
from __future__ import annotations

import json
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

    async def chat_json(
        self,
        messages: Iterable[dict[str, Any]],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = 512,
    ) -> dict[str, Any]:
        """非流式 JSON 抽取：让 LLM 返回严格 JSON 对象。

        Phase 4 QueryRewriter 用：input 中文 query → output filters dict。

        当前 Doubao 主推模型还不支持 ``response_format=json_object`` 强模式（会 400），
        所以走"提示约束 + 容错解析"路径：
        - prompt 已经在 system message 里强约束「只输出 JSON 不要 markdown」；
        - 解析时容忍 ``` ```json ... ``` ``` 围栏与首尾散文，提取第一个完整 JSON object；
        - 仍然解析失败抛 ``ValueError``，让上层走规则兜底。
        """
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=list(messages),
            stream=False,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        choices = getattr(resp, "choices", None) or []
        if not choices:
            raise ValueError("chat_json: 空 choices")
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None) if message is not None else None
        if not content:
            raise ValueError("chat_json: 空 content")
        parsed = _extract_json_object(content)
        if not isinstance(parsed, dict):
            raise ValueError(f"chat_json: 顶层不是 object: {type(parsed).__name__}")
        return parsed

    async def aclose(self) -> None:
        """关闭底层 httpx client（lifespan 中可调用）。"""
        try:
            await self._client.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("AsyncArk close 失败：%s", exc)


def _extract_json_object(text: str) -> Any:
    """从 LLM 自由文本里抠出第一个完整 JSON 对象。

    依次尝试：
    1. 整段直接 json.loads；
    2. 去掉 ```json ... ``` 围栏后 json.loads；
    3. 用花括号配对扫描，定位第一个 ``{`` 到对应 ``}`` 之间的子串再解析（容忍前后散文）。
    """
    s = text.strip()
    # 1) 直接解析
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # 2) 去围栏
    if s.startswith("```"):
        # 形如 ```json\n{...}\n``` 或 ```\n{...}\n```
        lines = s.splitlines()
        if len(lines) >= 2 and lines[0].startswith("```"):
            body = "\n".join(lines[1:])
            if body.endswith("```"):
                body = body[: -3]
            try:
                return json.loads(body.strip())
            except json.JSONDecodeError:
                pass
    # 3) 花括号配对
    start = s.find("{")
    if start < 0:
        raise ValueError(f"chat_json: 非合法 JSON: {text[:200]}")
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                snippet = s[start : i + 1]
                try:
                    return json.loads(snippet)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"chat_json: 非合法 JSON: {text[:200]}") from exc
    raise ValueError(f"chat_json: 找不到完整 JSON 对象: {text[:200]}")


def build_chat_client_from_settings() -> DoubaoChatClient:
    """读取 app.config.settings 构造，避免每个调用方重复读 env。"""
    from app.config import settings

    return DoubaoChatClient(
        api_key=settings.ark_api_key,
        model=settings.ark_model,
        base_url=settings.ark_base_url,
    )
