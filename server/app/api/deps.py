"""FastAPI 依赖注入工厂。

把 retriever / llm / orchestrator 装配成单例，便于：
- 启动期一次性加载 Milvus collection、初始化 AsyncArk；
- 测试期用 app.dependency_overrides 替换成 fake，免去打真实网络/数据库。
"""
from __future__ import annotations

from functools import lru_cache

from app.agent.clarify_detector import ClarifyDetector, build_clarify_detector
from app.agent.compare_planner import CompareTargetExtractor, build_compare_extractor
from app.agent.memory import ConversationMemory, get_memory
from app.agent.memory_summarizer import MemorySummarizer, build_memory_summarizer
from app.agent.multimodal_branch import MultimodalBranch, build_multimodal_branch
from app.agent.orchestrator import AgentOrchestrator
from app.agent.query_rewriter import QueryRewriter, build_query_rewriter
from app.config import settings
from app.db.product_repo import ProductRepository, get_product_repository
from app.llm.doubao_client import DoubaoChatClient, build_chat_client_from_settings
from app.rag.retriever import RagRetriever, build_retriever_from_settings
from app.rag.structured_retriever import StructuredRetriever, build_structured_retriever


@lru_cache(maxsize=1)
def get_retriever() -> RagRetriever:
    return build_retriever_from_settings()


@lru_cache(maxsize=1)
def get_llm_client() -> DoubaoChatClient:
    return build_chat_client_from_settings()


@lru_cache(maxsize=1)
def get_query_rewriter() -> QueryRewriter:
    # 启动时品牌列表为空，main.lifespan 会异步拉一次填进来。
    return build_query_rewriter(llm=get_llm_client(), known_brands=[])


@lru_cache(maxsize=1)
def get_compare_extractor() -> CompareTargetExtractor:
    return build_compare_extractor(llm=get_llm_client())


@lru_cache(maxsize=1)
def get_clarify_detector() -> ClarifyDetector:
    return build_clarify_detector()


@lru_cache(maxsize=1)
def get_memory_summarizer() -> MemorySummarizer:
    return build_memory_summarizer(llm=get_llm_client())


@lru_cache(maxsize=1)
def get_structured_retriever() -> StructuredRetriever:
    return build_structured_retriever()


def get_conversation_memory() -> ConversationMemory:
    return get_memory()


def get_orchestrator() -> AgentOrchestrator:
    return AgentOrchestrator(
        retriever=get_retriever(),
        llm=get_llm_client(),
        product_repo=get_product_repository(),
        memory=get_conversation_memory(),
        query_rewriter=get_query_rewriter(),
        compare_extractor=get_compare_extractor(),
        clarify_detector=get_clarify_detector(),
        memory_summarizer=get_memory_summarizer(),
        structured_retriever=get_structured_retriever(),
        multimodal_branch=get_multimodal_branch(),
    )


def get_product_repo() -> ProductRepository:
    return get_product_repository()


@lru_cache(maxsize=1)
def get_image_embed_cache() -> "ImageEmbedCache":
    """全局唯一 image embedding 缓存（Phase 5）。"""
    from app.rag.image_embed_cache import ImageEmbedCache
    return ImageEmbedCache(capacity=100, ttl_seconds=1800.0)


@lru_cache(maxsize=1)
def get_multimodal_branch() -> MultimodalBranch:
    """Phase 5 图文融合分支单例。"""
    return build_multimodal_branch(
        embedder=get_retriever().embedder,
        retriever=get_retriever(),
        cache=get_image_embed_cache(),
        query_rewriter=get_query_rewriter(),
        structured_retriever=get_structured_retriever(),
    )


def get_upload_dir() -> "Path":
    """上传图落盘根目录。按日期分子目录，自动 mkdir。"""
    from datetime import datetime
    from pathlib import Path
    root = Path(getattr(settings, "upload_root", "data/uploads"))
    today = datetime.now().strftime("%Y%m%d")
    p = root / today
    p.mkdir(parents=True, exist_ok=True)
    return p
