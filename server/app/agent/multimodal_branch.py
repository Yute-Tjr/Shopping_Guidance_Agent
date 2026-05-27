"""Agent 编排层「图 + 文」融合分支。

设计点：
- 与 clarify_detector / compare_planner / orchestrator 主流程同层级；
- 单一职责：拿到 message + image_id → 返回 (query_vector, retrieved, image_lost)；
- 不负责 LLM 流 / prompt 构造（那一步由 orchestrator 接力做，理由：
  错误降级路径在 orchestrator 主体里已经实现，重复一份会偏离）；
- 不接 SQL fallback：图文场景下 search_query 不会"退化到纯结构化承接"，
  scalar filter 配合 multimodal embedding 已经足够。

filter_expr 拼接策略：
- 把 ParsedQuery.to_filter_expr() 的输出（价格 / 品牌 / 品类）与 image-search
  专用的 `chunk_type in ["image", "title"]` 用 `and` 串接；
- 让图搜同时召回 title chunk 命中：上传"白色洗面奶"图 + 文字"洗面奶"，
  颜色靠 image chunk、品类靠 title chunk。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from app.agent.query_rewriter import ParsedQuery, QueryRewriter
from app.rag.image_embed_cache import ImageEmbedCache
from app.rag.retriever import RetrievedProduct
from app.utils.logger import get_logger

logger = get_logger(__name__)


CHUNK_TYPE_FILTER = 'chunk_type in ["image", "title"]'


class _EmbedderLike(Protocol):
    def embed_multimodal(self, *, text: str | None, image_path: str | None) -> list[float]: ...
    def embed_image(self, image_path: str) -> list[float]: ...


class _RetrieverLike(Protocol):
    def search(self, query: str, *, filter_expr: str | None = None, **kw: Any) -> list[RetrievedProduct]: ...


@dataclass
class MultimodalResult:
    """MultimodalBranch.handle() 返回的载荷。"""
    query_vector: list[float]
    retrieved: list[RetrievedProduct]
    parsed: ParsedQuery
    image_lost: bool = False  # 落盘图找不到，已退化到纯文本流


class MultimodalBranch:
    """图+文检索分支处理器。"""

    def __init__(
        self,
        *,
        embedder: _EmbedderLike,
        retriever: _RetrieverLike,
        cache: ImageEmbedCache,
        query_rewriter: QueryRewriter | None = None,
        structured_retriever: Any | None = None,  # 当前未启用，预留
        fallback_image_path_resolver: Callable[[str], str] | None = None,
    ) -> None:
        self.embedder = embedder
        self.retriever = retriever
        self.cache = cache
        self.query_rewriter = query_rewriter
        self.structured_retriever = structured_retriever
        self._resolve_path = fallback_image_path_resolver

    async def handle(
        self,
        *,
        message: str,
        image_id: str,
        history: list[dict] | None,
        summary: str | None,
    ) -> MultimodalResult:
        # 1) 拿 query_vector：优先缓存，miss 则重算
        vec: list[float] | None = None
        image_lost = False
        cached = await self.cache.get(image_id)
        image_path: str | None = None
        if cached is not None:
            vec, image_path = cached
        else:
            # 缓存 miss：用 resolver 找落盘图重算
            if self._resolve_path is not None:
                image_path = self._resolve_path(image_id)
            try:
                vec = self.embedder.embed_multimodal(text=message or None, image_path=image_path)
                if image_path is not None:
                    await self.cache.put(image_id, vec, image_path)
            except FileNotFoundError as exc:
                logger.warning("image_id=%s 落盘图丢失：%s，退化到纯文本流", image_id, exc)
                image_lost = True
                vec = self.embedder.embed_multimodal(text=message or "", image_path=None)
            except Exception as exc:  # noqa: BLE001
                logger.warning("multimodal embed 失败：%s，退化到纯文本", exc)
                image_lost = True
                vec = self.embedder.embed_multimodal(text=message or "", image_path=None)

        # 2) 抽结构化条件
        parsed: ParsedQuery
        if self.query_rewriter is not None:
            try:
                parsed = await self.query_rewriter.parse(message, history=history, summary=summary)
            except Exception as exc:  # noqa: BLE001
                logger.warning("query_rewriter 异常，按 identity 处理：%s", exc)
                parsed = ParsedQuery(search_query=message)
        else:
            parsed = ParsedQuery(search_query=message)

        # 3) 拼 filter_expr：结构化条件 AND chunk_type IN ('image','title')
        structural = parsed.to_filter_expr()
        if structural:
            filter_expr = f"({structural}) and {CHUNK_TYPE_FILTER}"
        else:
            filter_expr = CHUNK_TYPE_FILTER

        # 4) retrieve：绕过 RagRetriever.search 的 embed 步骤，用现成向量直查
        hits = self._search_with_vector(query_vector=vec, filter_expr=filter_expr)

        return MultimodalResult(
            query_vector=vec, retrieved=hits, parsed=parsed, image_lost=image_lost,
        )

    def _search_with_vector(
        self,
        *,
        query_vector: list[float],
        filter_expr: str | None,
    ) -> list[RetrievedProduct]:
        """绕过 RagRetriever.search 的 embed 步骤，用现成向量直查。

        RagRetriever 的 search() 签名是 (query: str, ...)，内部会调 embedder.embed_one(query)。
        我们已经有 multimodal embed 出的向量，直接走底层 store + 复用 _aggregate。
        """
        from app.rag.retriever import _aggregate  # 包内私有，但同包内复用合理
        hits = self.retriever.store.search(
            query_vector=query_vector,
            top_k=30,
            filter_expr=filter_expr,
        )
        if not hits:
            return []
        return _aggregate(hits, top_n_products=5)


def build_multimodal_branch(
    *,
    embedder,
    retriever,
    cache,
    query_rewriter=None,
    structured_retriever=None,
    upload_root: str = "data/uploads",
) -> MultimodalBranch:
    """工厂：从配置造一个 MultimodalBranch，附带落盘图 resolver。"""
    from pathlib import Path

    def resolve(image_id: str) -> str:
        # 缓存 miss 时按 upload 日期目录扫描；demo 用法简单遍历即可（100 件量级）
        root = Path(upload_root)
        for sub in sorted(root.iterdir(), reverse=True) if root.exists() else []:
            for ext in (".jpg", ".jpeg", ".png", ".webp"):
                p = sub / f"{image_id}{ext}"
                if p.exists():
                    return str(p)
        return f"{upload_root}/{image_id}.jpg"  # 找不到也给个路径，让 embed_multimodal 抛 FileNotFoundError

    return MultimodalBranch(
        embedder=embedder,
        retriever=retriever,
        cache=cache,
        query_rewriter=query_rewriter,
        structured_retriever=structured_retriever,
        fallback_image_path_resolver=resolve,
    )
