"""FastAPI 依赖注入工厂。

把 retriever / llm / orchestrator 装配成单例，便于：
- 启动期一次性加载 Milvus collection、初始化 AsyncArk；
- 测试期用 app.dependency_overrides 替换成 fake，免去打真实网络/数据库。
"""
from __future__ import annotations

from functools import lru_cache

from app.agent.memory import ConversationMemory, get_memory
from app.agent.orchestrator import AgentOrchestrator
from app.db.product_repo import ProductRepository, get_product_repository
from app.llm.doubao_client import DoubaoChatClient, build_chat_client_from_settings
from app.rag.retriever import RagRetriever, build_retriever_from_settings


@lru_cache(maxsize=1)
def get_retriever() -> RagRetriever:
    return build_retriever_from_settings()


@lru_cache(maxsize=1)
def get_llm_client() -> DoubaoChatClient:
    return build_chat_client_from_settings()


def get_conversation_memory() -> ConversationMemory:
    return get_memory()


def get_orchestrator() -> AgentOrchestrator:
    return AgentOrchestrator(
        retriever=get_retriever(),
        llm=get_llm_client(),
        product_repo=get_product_repository(),
        memory=get_conversation_memory(),
    )


def get_product_repo() -> ProductRepository:
    return get_product_repository()
