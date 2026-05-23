"""Doubao Embedding 封装（多模态版）。

设计点：
- 走 `client.multimodal_embeddings.create()`，因为 2026 年方舟控制台已下线纯文本
  Embedding 端点（如 doubao-embedding-text-*），统一用多模态模型
  `doubao-embedding-vision-*`，它同时接受 text / image 输入。
- 注意接口形态与纯文本 Embedding 不同：单次调用只接受一个 input、返回一个向量
  （input 是 content parts 列表，描述同一条多模态消息的各部分），无法像
  原 docs/02 §3 那样 batch 16 条；改为 ThreadPoolExecutor 并发跑多个单条请求。
- tenacity 指数退避重试，应对偶发 429 / 5xx / 网络抖动。
- 写入 Milvus 前强制 L2 归一化，配合 IP metric 等价余弦相似度。
- 首次调用懒探测 dim，避免硬编码维度后续模型升级时静默错位。
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, Sequence

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from volcenginesdkarkruntime import Ark
from volcenginesdkarkruntime._exceptions import (
    ArkAPIConnectionError,
    ArkAPITimeoutError,
    ArkInternalServerError,
    ArkRateLimitError,
)

from app.utils.logger import get_logger

logger = get_logger(__name__)

# 多模态接口是单输入返回单向量，需要靠线程并发凑吞吐。
# 方舟限速 RPM 700，8 路并发 + 每路串行 ≈ 满负载安全区。
DEFAULT_CONCURRENCY = 8

RETRYABLE_EXCEPTIONS = (
    ArkAPIConnectionError,
    ArkAPITimeoutError,
    ArkInternalServerError,
    ArkRateLimitError,
)


def l2_normalize(vec: Sequence[float]) -> list[float]:
    """对单条向量做 L2 归一化；零向量原样返回避免除零。"""
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return list(vec)
    return [v / norm for v in vec]


class DoubaoEmbedder:
    """轻量封装，业务代码只关心 .embed_batch(texts)。"""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
        normalize: bool = True,
    ) -> None:
        if base_url:
            self.client = Ark(api_key=api_key, base_url=base_url)
        else:
            self.client = Ark(api_key=api_key)
        self.model = model
        self.concurrency = max(1, concurrency)
        self.normalize = normalize
        self._dim: int | None = None

    @property
    def dim(self) -> int:
        """首次访问时打一发 'probe' 请求探测真实维度。"""
        if self._dim is None:
            vec = self._embed_one_text("probe")
            self._dim = len(vec)
            logger.info("Doubao embedding dim 探测结果：%d（model=%s）", self._dim, self.model)
        return self._dim

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=10),
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        reraise=True,
    )
    def _embed_one_text(self, text: str) -> list[float]:
        """单条文本调用 multimodal_embeddings；失败按 tenacity 配置重试。"""
        payload = (text or " ").strip() or " "
        resp = self.client.multimodal_embeddings.create(
            model=self.model,
            input=[{"type": "text", "text": payload}],
        )
        return resp.data.embedding

    def embed_batch(self, texts: Iterable[str]) -> list[list[float]]:
        """对任意长度的 texts 做并发 embedding。

        返回顺序与输入 texts 一一对应。空字符串会被替换为 ' '（API 拒绝空入参）。
        """
        text_list = [(t or " ").strip() or " " for t in texts]
        if not text_list:
            return []

        results: list[list[float]] = [[] for _ in text_list]
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            for idx, vec in zip(
                range(len(text_list)),
                pool.map(self._embed_one_text, text_list),
            ):
                if self._dim is None:
                    self._dim = len(vec)
                results[idx] = l2_normalize(vec) if self.normalize else list(vec)
        return results

    def embed_one(self, text: str) -> list[float]:
        vec = self._embed_one_text(text)
        if self._dim is None:
            self._dim = len(vec)
        return l2_normalize(vec) if self.normalize else list(vec)


def build_embedder_from_settings() -> DoubaoEmbedder:
    """根据 app.config.settings 构造，便于脚本和 API 复用。

    Embedding key 优先用 ark_embedding_api_key（用于 LLM/Embedding 分账户的场景），
    缺省回退到 ark_api_key。
    """
    from app.config import settings

    embedding_key = settings.ark_embedding_api_key or settings.ark_api_key
    return DoubaoEmbedder(
        api_key=embedding_key,
        model=settings.embedding_model,
        base_url=settings.ark_base_url,
    )
