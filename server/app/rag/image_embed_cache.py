"""图 embedding 内存 LRU 缓存。

设计点：
- 写在独立模块而非塞 retriever.py：upload.py 和 multimodal_branch.py 都依赖它，
  放 retriever.py 里两边 import 会形成交叉依赖。
- 100 件 demo 场景 + multi-turn 最多 2-3 个活跃 image_id，cap=100 足够；
  TTL=30min 配合 demo 单次使用模型。
- asyncio.Lock 包写：FastAPI 在同一事件循环里并发协程，纯 dict 操作虽然
  GIL 安全但 OrderedDict.move_to_end + popitem 组合不是原子的。
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass


@dataclass
class _Entry:
    vec: list[float]
    image_path: str
    inserted_at: float


class ImageEmbedCache:
    """LRU + TTL 双策略：超出容量驱逐最久未访问；超出 TTL 读取时清理。"""

    def __init__(self, *, capacity: int = 100, ttl_seconds: float = 1800.0) -> None:
        if capacity <= 0:
            raise ValueError("capacity 必须 > 0")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds 必须 > 0")
        self._cap = capacity
        self._ttl = ttl_seconds
        self._store: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = asyncio.Lock()

    async def put(self, image_id: str, vec: list[float], image_path: str) -> None:
        async with self._lock:
            now = time.time()
            self._store[image_id] = _Entry(vec=list(vec), image_path=image_path, inserted_at=now)
            self._store.move_to_end(image_id)
            while len(self._store) > self._cap:
                self._store.popitem(last=False)

    async def get(self, image_id: str) -> tuple[list[float], str] | None:
        async with self._lock:
            entry = self._store.get(image_id)
            if entry is None:
                return None
            if time.time() - entry.inserted_at > self._ttl:
                del self._store[image_id]
                return None
            self._store.move_to_end(image_id)
            return list(entry.vec), entry.image_path

    def __len__(self) -> int:
        return len(self._store)
