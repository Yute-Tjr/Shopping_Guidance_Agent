"""POST /api/v1/chat/stream —— SSE 主对话入口。

依赖 sse-starlette 的 EventSourceResponse：
- ping=15s 心跳，避免中间网关切链；
- 每个 yield 是 {"event": <name>, "data": <json str>}；
- 业务事件结构由 AgentOrchestrator 决定，这里只做序列化。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from app.agent.orchestrator import AgentOrchestrator
from app.api.deps import get_orchestrator
from app.schemas.chat import ChatRequest

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    orchestrator: AgentOrchestrator = Depends(get_orchestrator),
) -> EventSourceResponse:
    async def event_gen():
        async for evt in orchestrator.orchestrate(req):
            yield {
                "event": evt["event"],
                "data": json.dumps(evt.get("data") or {}, ensure_ascii=False),
            }

    # ping=15s：中间代理 30s 超时是常见值，我们打 15s 留充分余量
    return EventSourceResponse(event_gen(), ping=15)
