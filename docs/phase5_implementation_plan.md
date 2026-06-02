# Phase 5 多模态交互实施计划

> **设计文档：** `docs/05_多模态图搜设计.md`（先读它了解决策背景再开工）。
> **执行节奏：** 每个 Task 末尾**不自动 commit**——按用户偏好"由我手动 commit"，agent 跑完测试后等用户人工拍板。
> **进度跟踪：** 每个 step 是 `- [ ]` checkbox，做完打勾。

**Goal:** 给 Phase 4 已经成熟的"对话能力增强"链路叠加多模态交互路径，达成"上传图 + 可选文字 → vision embedding 检索 + Phase 4 结构化筛选融合 → 商品推荐流"，并补齐 5C 语音输入 / TTS 语音播报能力。

**Architecture:** 5B 图搜复用现有 `products_text` collection（chunk_type='image' 标量隔离）；二段式 API（`/upload/image` → image_id → `/chat`）；orchestrator 入口按 `image_id` 是否非空分流到 `MultimodalBranch`；Phase 4 的 query_rewriter / structured filter 全部零侵入复用。5C 语音交互升级为服务端音频网关：iOS 录 16k PCM → `/audio/asr` → 豆包语音 OpenSpeech ASR → inputText；assistant 文本 → `/audio/tts` → 豆包语音 OpenSpeech TTS → WAV 播放。端侧 Speech.framework 仅作为 ASR 降级；TTS 取消 AVSpeechSynthesizer 原生朗读模式。

**Tech Stack:** FastAPI / Pydantic v2 / Milvus Lite / Doubao multimodal_embeddings / Volc OpenSpeech ASR/TTS / WebSocket binary protocol / Pillow / SwiftUI / PhotosUI / AVFoundation / Speech.framework fallback / pytest-asyncio / Swift Testing。

---

## Task 总览

| # | Task | 主要产出 |
| --- | --- | --- |
| 1 | ImageEmbedCache | 内存 LRU+TTL 缓存模块 |
| 2 | Embedder 加 embed_image / embed_multimodal | 复用 Doubao multimodal SDK |
| 3 | build_image_index.py | 100 张商品图入向量库 |
| 4 | Upload API | `POST /upload/image` 落盘 + 缓存预算 |
| 5 | Prompts 适配 | `build_image_search_messages` |
| 6 | MultimodalBranch | Agent 编排层图文融合分支 |
| 7 | Orchestrator + Deps 接入 | image_id 分流路由 |
| 8 | iOS ImagePicker + UploadService | 客户端选图压缩 + 上传 |
| 9 | iOS ChatView + MessageBubble UI | 输入栏相机按钮 + 缩略图气泡 |
| 10 | 评测黄金集 + 评测脚本 + Smoke | 4 类指标产出报告 |
| 11 | README + 最终验收 | 文档化 Phase 5 |
| 12 | 5C 协议与 ViewModel 逻辑测试 | 语音输入/TTS 可测边界 |
| 13 | 5C iOS 语音输入基础服务 | Speech.framework 仅作为 ASR 降级 |
| 14 | 5C Chat UI 接入 | 麦克风按钮、播报按钮、自动播报开关 |
| 15 | 5C 权限与验收 | Info.plist 权限、Swift/Xcode 验证 |
| 16 | 5C 服务端 ASR/TTS API | `/audio/asr`、`/audio/tts`、voices |
| 17 | 5C iOS 远端音频重构 | PCM 上传 ASR、WAV TTS 播放、本地降级 |
| 18 | 5C 音色选择 | Header voice 菜单、selectedVoice 透传 |

---

## Task 1: ImageEmbedCache（LRU + TTL）

**Files:**
- Create: `server/app/rag/image_embed_cache.py`
- Test: `server/tests/test_image_embed_cache.py`

- [ ] **Step 1.1: 写失败的测试**

```python
# server/tests/test_image_embed_cache.py
"""ImageEmbedCache：LRU + TTL + 并发安全的图 embedding 内存缓存。"""
from __future__ import annotations

import asyncio

import pytest

from app.rag.image_embed_cache import ImageEmbedCache


@pytest.mark.asyncio
async def test_put_and_get_roundtrip():
    cache = ImageEmbedCache(capacity=2)
    await cache.put("a", [1.0, 2.0, 3.0], "/tmp/a.jpg")
    got = await cache.get("a")
    assert got == ([1.0, 2.0, 3.0], "/tmp/a.jpg")


@pytest.mark.asyncio
async def test_get_returns_none_for_missing_key():
    cache = ImageEmbedCache()
    assert await cache.get("never-existed") is None


@pytest.mark.asyncio
async def test_get_returns_none_after_ttl_expires():
    cache = ImageEmbedCache(ttl_seconds=0.05)
    await cache.put("a", [1.0], "/tmp/a.jpg")
    await asyncio.sleep(0.1)
    assert await cache.get("a") is None


@pytest.mark.asyncio
async def test_lru_capacity_evicts_least_recently_used():
    cache = ImageEmbedCache(capacity=2)
    await cache.put("a", [1.0], "/a")
    await cache.put("b", [2.0], "/b")
    # 访问 a 使其变 most recently used
    await cache.get("a")
    # 现在写入 c：应驱逐 b（LRU），而非 a
    await cache.put("c", [3.0], "/c")
    assert await cache.get("a") is not None
    assert await cache.get("b") is None
    assert await cache.get("c") is not None


@pytest.mark.asyncio
async def test_concurrent_puts_do_not_tear():
    cache = ImageEmbedCache(capacity=200)

    async def write(i: int) -> None:
        await cache.put(f"k{i}", [float(i)], f"/p{i}")

    await asyncio.gather(*(write(i) for i in range(50)))
    assert len(cache) == 50
    for i in range(50):
        got = await cache.get(f"k{i}")
        assert got == ([float(i)], f"/p{i}")
```

- [ ] **Step 1.2: 跑测试验证它失败**

Run:

```bash
cd server && pytest tests/test_image_embed_cache.py -v
```

Expected: 全部 FAIL with `ModuleNotFoundError: No module named 'app.rag.image_embed_cache'`

- [ ] **Step 1.3: 写最小实现**

```python
# server/app/rag/image_embed_cache.py
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
```

- [ ] **Step 1.4: 跑测试验证通过**

Run:

```bash
cd server && pytest tests/test_image_embed_cache.py -v
```

Expected: 5 PASSED。

- [ ] **Step 1.5: 等待人工审阅 + commit**

把 `image_embed_cache.py` 和 `test_image_embed_cache.py` 提交给用户审阅。**不要自动 commit**——由用户人工 commit。

---

## Task 2: Embedder 加 embed_image / embed_multimodal

**Files:**
- Modify: `server/app/rag/embedder.py`
- Test: `server/tests/test_embedder.py` (新建)

- [ ] **Step 2.1: 准备一个最小测试图（如不存在）**

Run:

```bash
cd server && python -c "
from PIL import Image
import pathlib
p = pathlib.Path('tests/fixtures')
p.mkdir(parents=True, exist_ok=True)
img = Image.new('RGB', (64, 64), color=(180, 60, 60))
img.save(p / 'red_64.jpg', 'JPEG', quality=80)
print('fixture saved:', p / 'red_64.jpg')
"
```

Expected: 输出 `fixture saved: tests/fixtures/red_64.jpg`，文件大小 ~1-2 KB。

- [ ] **Step 2.2: 写失败的测试**

```python
# server/tests/test_embedder.py
"""DoubaoEmbedder 多模态接口单测。

不调真实 API：用 monkeypatch 替换 _embed_one_text / _embed_one_image / _embed_one_multimodal
为 fake 返回，只验证调用路径与参数构造、以及 L2 归一化。
"""
from __future__ import annotations

from pathlib import Path

import math
import pytest

from app.rag.embedder import DoubaoEmbedder, l2_normalize


FIXTURE = Path(__file__).parent / "fixtures" / "red_64.jpg"


def _fake_embedder(monkeypatch, captured: dict, *, vec: list[float]):
    """构造一个 DoubaoEmbedder 实例，把 SDK 调用替换成 fake。"""
    emb = DoubaoEmbedder.__new__(DoubaoEmbedder)  # 绕过 __init__ 真连 Ark
    emb.model = "fake-vision"
    emb.concurrency = 1
    emb.normalize = True
    emb._dim = None

    def fake_call(*, model, input):
        captured["model"] = model
        captured["input"] = input
        # 模拟 Ark SDK 返回结构：resp.data.embedding
        class _Resp:
            class data:
                embedding = list(vec)
        return _Resp

    class _Client:
        class multimodal_embeddings:
            create = staticmethod(fake_call)

    emb.client = _Client
    return emb


def test_embed_image_returns_l2_normalized_vector(monkeypatch):
    captured: dict = {}
    raw = [3.0, 0.0, 4.0]  # |v| = 5
    emb = _fake_embedder(monkeypatch, captured, vec=raw)

    vec = emb.embed_image(str(FIXTURE))

    assert math.isclose(sum(v * v for v in vec), 1.0, abs_tol=1e-6)
    # 验证传给 SDK 的 input 是 image_url 形态
    assert captured["model"] == "fake-vision"
    parts = captured["input"]
    assert len(parts) == 1
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_embed_image_raises_when_file_missing(monkeypatch):
    captured: dict = {}
    emb = _fake_embedder(monkeypatch, captured, vec=[1.0])
    with pytest.raises(FileNotFoundError):
        emb.embed_image("/nonexistent/no.jpg")


def test_embed_multimodal_combines_text_and_image(monkeypatch):
    captured: dict = {}
    emb = _fake_embedder(monkeypatch, captured, vec=[1.0, 0.0])

    vec = emb.embed_multimodal(text="清新风格", image_path=str(FIXTURE))

    assert math.isclose(sum(v * v for v in vec), 1.0, abs_tol=1e-6)
    parts = captured["input"]
    assert len(parts) == 2
    types = [p["type"] for p in parts]
    assert "text" in types and "image_url" in types
    # text 在前，与 docs/02 默认顺序一致（便于 prompt cache）
    assert parts[0]["type"] == "text"
    assert parts[0]["text"] == "清新风格"


def test_embed_multimodal_text_only(monkeypatch):
    captured: dict = {}
    emb = _fake_embedder(monkeypatch, captured, vec=[1.0])

    emb.embed_multimodal(text="只有文字")

    parts = captured["input"]
    assert len(parts) == 1
    assert parts[0]["type"] == "text"


def test_embed_multimodal_both_none_raises(monkeypatch):
    captured: dict = {}
    emb = _fake_embedder(monkeypatch, captured, vec=[1.0])
    with pytest.raises(ValueError):
        emb.embed_multimodal(text=None, image_path=None)


def test_l2_normalize_returns_unit_vector():
    out = l2_normalize([3.0, 0.0, 4.0])
    assert math.isclose(sum(v * v for v in out), 1.0, abs_tol=1e-9)


def test_l2_normalize_zero_vector_returns_zero():
    assert l2_normalize([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]
```

- [ ] **Step 2.3: 跑测试验证它失败**

Run:

```bash
cd server && pytest tests/test_embedder.py -v
```

Expected: 多个 FAIL with `AttributeError: 'DoubaoEmbedder' object has no attribute 'embed_image'` / `embed_multimodal`。

- [ ] **Step 2.4: 在 embedder.py 增加图片与多模态接口**

打开 `server/app/rag/embedder.py`，在 import 段末尾加：

```python
import base64
from pathlib import Path
```

在 `DoubaoEmbedder` 类内部、`embed_one` 方法**之后**追加这些方法：

```python
    # ---------- 多模态接口（Phase 5） ----------

    @staticmethod
    def _image_data_url(image_path: str) -> str:
        """把本地图转 base64 data URL，doubao multimodal API 接受 data: 形态。"""
        p = Path(image_path)
        if not p.exists():
            raise FileNotFoundError(f"图片不存在：{image_path}")
        suffix = p.suffix.lower()
        if suffix in (".jpg", ".jpeg"):
            mime = "image/jpeg"
        elif suffix == ".png":
            mime = "image/png"
        elif suffix == ".webp":
            mime = "image/webp"
        else:
            raise ValueError(f"不支持的图片格式：{suffix}")
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{b64}"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=10),
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        reraise=True,
    )
    def _embed_one_image(self, image_path: str) -> list[float]:
        """单张图 embedding；失败按 tenacity 配置重试。"""
        url = self._image_data_url(image_path)
        resp = self.client.multimodal_embeddings.create(
            model=self.model,
            input=[{"type": "image_url", "image_url": {"url": url}}],
        )
        return resp.data.embedding

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=10),
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        reraise=True,
    )
    def _embed_one_multimodal(self, *, text: str | None, image_path: str | None) -> list[float]:
        """图 + 文一次性 embedding，返回单个统一向量。"""
        parts: list[dict] = []
        if text:
            parts.append({"type": "text", "text": text.strip()})
        if image_path:
            parts.append({"type": "image_url", "image_url": {"url": self._image_data_url(image_path)}})
        if not parts:
            raise ValueError("embed_multimodal 至少需要 text 或 image_path 之一")
        resp = self.client.multimodal_embeddings.create(
            model=self.model,
            input=parts,
        )
        return resp.data.embedding

    def embed_image(self, image_path: str) -> list[float]:
        """公开接口：单张图 → 归一化向量。"""
        vec = self._embed_one_image(image_path)
        if self._dim is None:
            self._dim = len(vec)
        return l2_normalize(vec) if self.normalize else list(vec)

    def embed_multimodal(
        self,
        *,
        text: str | None = None,
        image_path: str | None = None,
    ) -> list[float]:
        """公开接口：图+文 / 仅图 / 仅文 → 归一化向量。"""
        if not text and not image_path:
            raise ValueError("embed_multimodal 至少需要 text 或 image_path 之一")
        vec = self._embed_one_multimodal(text=text, image_path=image_path)
        if self._dim is None:
            self._dim = len(vec)
        return l2_normalize(vec) if self.normalize else list(vec)
```

- [ ] **Step 2.5: 跑测试验证通过**

Run:

```bash
cd server && pytest tests/test_embedder.py -v
```

Expected: 7 PASSED。

- [ ] **Step 2.6: 跑全套 server 测试，确保没破坏现有逻辑**

Run:

```bash
cd server && pytest -q
```

Expected: 现有所有测试仍通过，+7 new。

- [ ] **Step 2.7: 等待人工审阅 + commit**

---

## Task 3: build_image_index.py 增量索引脚本

**Files:**
- Create: `server/scripts/build_image_index.py`

> **特殊说明**：这个 task 涉及真实 Doubao API 调用 + 真实 Milvus 写入，不容易做 pure unit test。采用"sanity + 集成确认"路线：先小批量（`--limit 3`）确认链路，再全量跑。

- [ ] **Step 3.1: 写脚本**

```python
# server/scripts/build_image_index.py
"""Phase 5 增量索引：把数据集每件商品的 _live.jpg 编码成 image chunk 写入 Milvus。

幂等策略：先按 source_id 删旧 image chunk 再插，支持多次跑。
chunk_type='image'，text 字段存图片相对路径（占位，prompt 不消费）。

用法：

    cd server
    python -m scripts.build_image_index                    # 全量
    python -m scripts.build_image_index --limit 3          # 调试用
    python -m scripts.build_image_index --rebuild          # 等价于「先清空全部 image chunk 再插」
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Iterable

from app.config import settings
from app.db.product_repo import get_product_repository
from app.rag.chunker import iter_product_files, load_product
from app.rag.embedder import build_embedder_from_settings
from app.rag.milvus_store import COLLECTION_NAME, ProductTextStore
from app.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "ecommerce_agent_dataset"


def find_image_path(dataset_root: Path, product_id: str, category_dir: Path) -> Path | None:
    """从分类目录的 images/ 下找 <pid>_live.jpg。"""
    p = category_dir / "images" / f"{product_id}_live.jpg"
    return p if p.exists() else None


def collect_image_rows(dataset_root: Path, limit: int | None) -> list[dict]:
    """遍历数据集，每件商品产出一个 image chunk row（不含向量）。"""
    rows: list[dict] = []
    count = 0
    for json_path in iter_product_files(dataset_root):
        if limit is not None and count >= limit:
            break
        product = load_product(json_path)
        category_dir = json_path.parent.parent  # data/p_xxx.json → 分类根目录
        img = find_image_path(dataset_root, product["product_id"], category_dir)
        if img is None:
            logger.warning("跳过：商品 %s 找不到 _live.jpg", product["product_id"])
            continue
        # 与现有 Chunk metadata 对齐
        skus = product.get("skus", []) or []
        sku_prices = [float(s.get("price", 0.0)) for s in skus if s.get("price")]
        base_price = float(product.get("base_price", 0.0) or (sku_prices[0] if sku_prices else 0.0))
        rows.append({
            "product_id": product["product_id"],
            "image_path": str(img),
            "text": str(img.relative_to(dataset_root)),  # 占位文本：相对路径方便人肉 debug
            "chunk_type": "image",
            "category": product.get("category", "") or "",
            "sub_category": product.get("sub_category", "") or "",
            "brand": product.get("brand", "") or "",
            "base_price": base_price,
            "min_sku_price": min(sku_prices) if sku_prices else base_price,
            "max_sku_price": max(sku_prices) if sku_prices else base_price,
            "rating": int(product.get("rating", 0) or 0),
            "source_id": f"{product['product_id']}#image#0",
        })
        count += 1
    logger.info("收集 image chunk %d 条", len(rows))
    return rows


def delete_existing_image_chunks(store: ProductTextStore, source_ids: Iterable[str]) -> int:
    """删旧 image chunk（按 source_id），返回删除条数。

    Milvus Lite 支持按 filter 删，但不支持 IN 大列表传太多 ID。这里分 50 一批。
    """
    ids = list(source_ids)
    if not ids:
        return 0
    total = 0
    batch = 50
    for i in range(0, len(ids), batch):
        sub = ids[i:i + batch]
        quoted = ", ".join(f'"{sid}"' for sid in sub)
        filter_expr = f'source_id in [{quoted}]'
        result = store.client.delete(collection_name=COLLECTION_NAME, filter=filter_expr)
        total += result.get("delete_count", 0) if isinstance(result, dict) else 0
    return total


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 5 增量灌入 image chunk")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 件（调试用）")
    parser.add_argument("--rebuild", action="store_true", help="先清空所有 image chunk 再灌")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if not args.dataset.exists():
        raise SystemExit(f"数据集目录不存在：{args.dataset}")

    rows = collect_image_rows(args.dataset, args.limit)
    if not rows:
        logger.warning("没有图片可入库，退出。")
        return

    embedder = build_embedder_from_settings()
    dim = embedder.dim
    logger.info("Embedding model=%s, dim=%d", embedder.model, dim)

    store = ProductTextStore(db_path=settings.milvus_db_path, dim=dim)
    if not store.client.has_collection(COLLECTION_NAME):
        raise SystemExit(f"collection={COLLECTION_NAME} 不存在，请先跑 build_index.py 建文本索引")
    store.client.load_collection(COLLECTION_NAME)

    # 删旧 image chunk
    if args.rebuild:
        deleted = delete_existing_image_chunks(
            store, source_ids=(r["source_id"] for r in rows),
        )
        logger.info("--rebuild：已删除旧 image chunk %d 条", deleted)
    else:
        # 默认行为：按 source_id 删该 row 对应的旧条目，让脚本幂等
        deleted = delete_existing_image_chunks(
            store, source_ids=(r["source_id"] for r in rows),
        )
        if deleted:
            logger.info("幂等删除旧 image chunk %d 条（按 source_id 对应）", deleted)

    # 串行调 embed_image（多线程并发要复用 embedder 的 ThreadPoolExecutor，
    # 但 build_image_index 是离线脚本，等几分钟可接受）
    started = time.time()
    vectors: list[list[float]] = []
    for i, row in enumerate(rows, 1):
        try:
            vec = embedder.embed_image(row["image_path"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("商品 %s 图编码失败：%s（跳过）", row["product_id"], exc)
            continue
        vectors.append(vec)
        if i % 10 == 0:
            logger.info("  ...已编码 %d / %d", i, len(rows))
    logger.info("图 embedding 完成，耗时 %.1fs，成功 %d / %d", time.time() - started, len(vectors), len(rows))

    # 写 Milvus（直接构造 row 字典，不走 chunker 的 Chunk 对象）
    if len(vectors) != len(rows):
        # 跳过失败的；同步对齐 rows
        rows = [r for r, v in zip(rows, vectors + [None] * (len(rows) - len(vectors))) if v is not None][:len(vectors)]

    insert_rows: list[dict] = []
    for row, vec in zip(rows, vectors):
        insert_rows.append({
            "vector": list(vec),
            "product_id": row["product_id"],
            "chunk_type": row["chunk_type"],
            "text": row["text"][:2000],
            "category": row["category"],
            "sub_category": row["sub_category"],
            "brand": row["brand"],
            "base_price": row["base_price"],
            "min_sku_price": row["min_sku_price"],
            "max_sku_price": row["max_sku_price"],
            "rating": row["rating"],
            "source_id": row["source_id"],
        })
    result = store.client.insert(collection_name=COLLECTION_NAME, data=insert_rows)
    inserted = result.get("insert_count", len(insert_rows)) if isinstance(result, dict) else len(insert_rows)
    logger.info("写入 Milvus 完成：%d 条 image chunk → collection=%s", inserted, COLLECTION_NAME)

    # Sanity check：用第一张图自己当 query，应该 Top-1 命中自身
    probe = store.search(vectors[0], top_k=3, filter_expr='chunk_type == "image"')
    logger.info("Sanity search Top-3（chunk_type=image）：")
    for hit in probe:
        ent = hit.get("entity", {})
        logger.info(
            "  score=%.4f  pid=%s  text=%s",
            hit.get("distance", 0.0),
            ent.get("product_id"),
            (ent.get("text") or "")[:60],
        )


if __name__ == "__main__":
    main()
```

- [ ] **Step 3.2: 小规模跑通（限制 3 件）**

Run:

```bash
cd server && python -m scripts.build_image_index --limit 3
```

Expected: 日志显示「写入 Milvus 完成：3 条 image chunk」+ Sanity Top-1 与 query 同 pid。

- [ ] **Step 3.3: 全量跑**

Run:

```bash
cd server && python -m scripts.build_image_index --rebuild
```

Expected: 日志显示「写入 Milvus 完成：100 条 image chunk」。

- [ ] **Step 3.4: 验证 image chunk 数量**

Run:

```bash
cd server && python -c "
from app.config import settings
from app.rag.milvus_store import COLLECTION_NAME, ProductTextStore
from app.rag.embedder import build_embedder_from_settings
emb = build_embedder_from_settings()
store = ProductTextStore(db_path=settings.milvus_db_path, dim=emb.dim)
store.client.load_collection(COLLECTION_NAME)
# 数 image chunk
res = store.client.query(collection_name=COLLECTION_NAME, filter='chunk_type == \"image\"', output_fields=['product_id'], limit=200)
print('image chunk 总数:', len(res))
"
```

Expected: `image chunk 总数: 100`。

- [ ] **Step 3.5: 等待人工审阅 + commit**

---

## Task 4: Upload API（POST /upload/image）

**Files:**
- Create: `server/app/api/upload.py`
- Modify: `server/app/main.py`（注册 upload router）
- Modify: `server/app/api/deps.py`（暴露 ImageEmbedCache 单例）
- Test: `server/tests/test_upload_api.py`

- [ ] **Step 4.1: 在 deps.py 暴露 cache + image_dir 单例**

打开 `server/app/api/deps.py`，在 `get_structured_retriever` 之后追加：

```python
@lru_cache(maxsize=1)
def get_image_embed_cache() -> "ImageEmbedCache":
    """全局唯一 image embedding 缓存。"""
    from app.rag.image_embed_cache import ImageEmbedCache
    return ImageEmbedCache(capacity=100, ttl_seconds=1800.0)


def get_upload_dir() -> "Path":
    """上传图落盘根目录。按日期分子目录，自动 mkdir。"""
    from datetime import datetime
    from pathlib import Path
    from app.config import settings
    root = Path(getattr(settings, "upload_root", "data/uploads"))
    today = datetime.now().strftime("%Y%m%d")
    p = root / today
    p.mkdir(parents=True, exist_ok=True)
    return p
```

- [ ] **Step 4.2: 写 upload 路由**

```python
# server/app/api/upload.py
"""POST /api/v1/upload/image —— 接收用户上传图，落盘 + 同步算 vision embedding 缓存。

二段式 API 设计：
1. 客户端先 POST /upload/image (multipart) → 返回 {image_id, preview_url}
2. 再 POST /chat (JSON) 时把 image_id 带上 → orchestrator 走 multimodal 分支

为什么先 embed 再返回（而不是 lazy 等 /chat 时再算）：
- vision API ~600-1200ms，提前算掉让 /chat 路径上不再阻塞首 token；
- 上传时算失败可以直接返 503，比在 SSE 流里失败更易表达。
"""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError

from app.api.deps import get_image_embed_cache, get_upload_dir
from app.rag.embedder import DoubaoEmbedder
from app.rag.image_embed_cache import ImageEmbedCache
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/upload", tags=["upload"])

_MAX_BYTES = 1 * 1024 * 1024  # 1 MB
_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
_MIME_TO_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def _get_embedder():
    """延迟导入避免循环；返回 process-wide 单例。"""
    from app.api.deps import get_retriever  # retriever 内部已实例化 embedder
    return get_retriever().embedder


@router.post("/image")
async def upload_image(
    file: UploadFile = File(...),
    cache: ImageEmbedCache = Depends(get_image_embed_cache),
    upload_dir: Path = Depends(get_upload_dir),
):
    # 1) MIME 校验
    if file.content_type not in _ALLOWED_MIME:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"图片格式不支持，仅接受 {sorted(_ALLOWED_MIME)}",
        )

    # 2) 大小校验（流式读完先看大小）
    body = await file.read()
    if len(body) > _MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"图片过大（{len(body)} bytes > {_MAX_BYTES} bytes 上限）",
        )
    if not body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="空文件",
        )

    # 3) 解码校验（Pillow 打开 → 防恶意 / 破损文件）
    import io
    try:
        img = Image.open(io.BytesIO(body))
        img.verify()  # 仅校验文件结构，不实际解码全图
    except (UnidentifiedImageError, Exception) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"图片无法读取：{exc}",
        )

    # 4) 落盘
    image_id = uuid.uuid4().hex
    ext = _MIME_TO_EXT[file.content_type]
    saved_path = upload_dir / f"{image_id}{ext}"
    saved_path.write_bytes(body)

    # 5) 同步算 vision embedding 缓存（失败降级返 503 + degraded 标记）
    embedder: DoubaoEmbedder = _get_embedder()
    try:
        vec = embedder.embed_image(str(saved_path))
    except Exception as exc:  # noqa: BLE001
        logger.warning("vision embedding 失败：%s（image_id=%s 已落盘但未缓存）", exc, image_id)
        # 业务降级：让客户端知道可以走纯文本流
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "degraded": True,
                "fallback_text_only": True,
                "message": "图片识别服务繁忙，可继续用文字描述",
            },
        )

    await cache.put(image_id, vec, str(saved_path))
    logger.info("upload 成功：image_id=%s size=%d ext=%s", image_id, len(body), ext)

    return {
        "image_id": image_id,
        "preview_url": f"/static_uploads/{saved_path.name}",  # demo 用，可选
    }
```

- [ ] **Step 4.3: 在 main.py 注册 upload router**

打开 `server/app/main.py`，在 `from app.api.products import router as products_router` 之后加：

```python
from app.api.upload import router as upload_router
```

并在 `app.include_router(products_router, prefix="/api/v1")` 后追加：

```python
app.include_router(upload_router, prefix="/api/v1")
```

- [ ] **Step 4.4: 写测试**

```python
# server/tests/test_upload_api.py
"""POST /api/v1/upload/image 单测：MIME / 大小 / 解码 / 限流降级 / 缓存复用。"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api.deps import (
    get_image_embed_cache,
    get_retriever,
    get_upload_dir,
)
from app.main import app
from app.rag.image_embed_cache import ImageEmbedCache


def _png_bytes(color: tuple[int, int, int] = (10, 20, 30), size: int = 32) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, "PNG")
    return buf.getvalue()


def _jpg_bytes(color: tuple[int, int, int] = (10, 20, 30), size: int = 32) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, "JPEG", quality=85)
    return buf.getvalue()


class _StubEmbedder:
    """假 embedder：返回固定向量，不走真实 API。"""
    def __init__(self, *, fail: bool = False):
        self._fail = fail
    def embed_image(self, path: str) -> list[float]:
        if self._fail:
            raise RuntimeError("vision API 限流")
        return [0.1] * 8


class _StubRetriever:
    def __init__(self, *, fail: bool = False):
        self.embedder = _StubEmbedder(fail=fail)


@pytest.fixture
def client(tmp_path: Path):
    cache = ImageEmbedCache(capacity=10, ttl_seconds=300)
    app.dependency_overrides[get_image_embed_cache] = lambda: cache
    app.dependency_overrides[get_upload_dir] = lambda: tmp_path
    app.dependency_overrides[get_retriever] = lambda: _StubRetriever()
    with TestClient(app) as c:
        yield c, cache, tmp_path
    app.dependency_overrides.clear()


def test_upload_happy_path_returns_image_id_and_caches_vec(client):
    c, cache, tmp_path = client
    body = _jpg_bytes()
    resp = c.post(
        "/api/v1/upload/image",
        files={"file": ("test.jpg", body, "image/jpeg")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "image_id" in data
    image_id = data["image_id"]
    # 落盘
    saved = list(tmp_path.glob(f"{image_id}.*"))
    assert len(saved) == 1
    # 缓存命中
    import asyncio
    got = asyncio.get_event_loop().run_until_complete(cache.get(image_id))
    assert got is not None
    vec, path = got
    assert len(vec) == 8
    assert path == str(saved[0])


def test_upload_rejects_unsupported_mime(client):
    c, _, _ = client
    resp = c.post(
        "/api/v1/upload/image",
        files={"file": ("test.gif", b"GIF89a", "image/gif")},
    )
    assert resp.status_code == 415


def test_upload_rejects_oversize(client):
    c, _, _ = client
    big = b"\xff\xd8\xff\xe0" + b"x" * (2 * 1024 * 1024)  # >1MB
    resp = c.post(
        "/api/v1/upload/image",
        files={"file": ("big.jpg", big, "image/jpeg")},
    )
    assert resp.status_code == 413


def test_upload_rejects_corrupted_image(client):
    c, _, _ = client
    resp = c.post(
        "/api/v1/upload/image",
        files={"file": ("broken.jpg", b"not-an-image", "image/jpeg")},
    )
    assert resp.status_code == 422


def test_upload_rejects_empty_file(client):
    c, _, _ = client
    resp = c.post(
        "/api/v1/upload/image",
        files={"file": ("empty.jpg", b"", "image/jpeg")},
    )
    assert resp.status_code == 400


def test_upload_returns_503_when_vision_api_fails(client, tmp_path: Path):
    c, _, _ = client
    # 把 retriever override 改成 fail 版本
    app.dependency_overrides[get_retriever] = lambda: _StubRetriever(fail=True)
    body = _jpg_bytes()
    resp = c.post(
        "/api/v1/upload/image",
        files={"file": ("test.jpg", body, "image/jpeg")},
    )
    assert resp.status_code == 503
    body_json = resp.json()
    # FastAPI 把 detail dict 整体放在 "detail" 字段下
    assert body_json["detail"]["fallback_text_only"] is True
```

- [ ] **Step 4.5: 跑测试验证全过**

Run:

```bash
cd server && pytest tests/test_upload_api.py -v
```

Expected: 6 PASSED。

- [ ] **Step 4.6: 跑全套测试**

Run:

```bash
cd server && pytest -q
```

Expected: 现有所有测试 + 6 新增全过。

- [ ] **Step 4.7: 等待人工审阅 + commit**

---

## Task 5: Prompts - build_image_search_messages

**Files:**
- Modify: `server/app/agent/prompts.py`
- Test: `server/tests/test_prompts.py`（已存在，加 case）

- [ ] **Step 5.1: 在 test_prompts.py 末尾加测试**

打开 `server/tests/test_prompts.py`，在文件末尾追加：

```python


def test_build_image_search_messages_includes_contradiction_rule():
    """图文矛盾以文字为准——必须在 system prompt 里明示。"""
    from app.agent.prompts import build_image_search_messages
    from app.rag.retriever import RetrievedProduct

    retrieved = [
        RetrievedProduct(
            product_id="p_001",
            score=0.9,
            brand="兰蔻",
            category="美妆",
            sub_category="精华",
            base_price=200.0,
            best_chunk_text="兰蔻小黑瓶",
            supporting_chunks=["兰蔻小黑瓶"],
            title="兰蔻小黑瓶精华液 30ml",
        ),
    ]
    msgs = build_image_search_messages(
        user_message="这个，但要便宜一点的",
        image_path="/data/uploads/20260527/abc.jpg",
        retrieved=retrieved,
        history=None,
        summary=None,
    )
    system = msgs[0]["content"]
    assert "图文矛盾" in system or "以文字为准" in system
    # image_path 本身**不应**出现在 system prompt 里（防泄漏到 LLM 上下文）
    assert "/data/uploads/" not in system
    assert "abc.jpg" not in system


def test_build_image_search_messages_uses_retrieved_block():
    """retrieved_products 块仍走标准 _format_retrieved_block。"""
    from app.agent.prompts import build_image_search_messages
    from app.rag.retriever import RetrievedProduct

    retrieved = [
        RetrievedProduct(
            product_id="p_002",
            score=0.85,
            brand="The Ordinary",
            category="美妆",
            sub_category="精华",
            base_price=80.0,
            min_sku_price=70.0,
            max_sku_price=90.0,
            best_chunk_text="烟酰胺精华",
            supporting_chunks=["烟酰胺精华"],
            title="The Ordinary 烟酰胺 10% + 锌 1% 精华",
        ),
    ]
    msgs = build_image_search_messages(
        user_message="找同款", image_path="/tmp/x.jpg",
        retrieved=retrieved, history=None, summary=None,
    )
    system = msgs[0]["content"]
    assert "p_002" in system
    assert "名称=The Ordinary" in system or "The Ordinary" in system
```

- [ ] **Step 5.2: 跑测试验证失败**

Run:

```bash
cd server && pytest tests/test_prompts.py::test_build_image_search_messages_includes_contradiction_rule -v
```

Expected: FAIL with `ImportError: cannot import name 'build_image_search_messages'`。

- [ ] **Step 5.3: 在 prompts.py 加 build_image_search_messages**

打开 `server/app/agent/prompts.py`，在文件末尾（`build_compare_messages` 之后）追加：

```python


def build_image_search_messages(
    *,
    user_message: str,
    image_path: str,
    retrieved: Iterable[RetrievedProduct],
    history: list[dict] | None,
    summary: str | None = None,
) -> list[dict]:
    """图文检索 prompt（Phase 5）。

    与 build_recommend_messages 的区别：在 _BASE_RULES 上追加图文融合规则。

    设计要点：
    - image_path 仅用于上游 retrieve 已结束，**不**写进 system prompt——
      LLM 看到本地路径既无信息量也增加污染面；
    - 在系统消息里告诉 LLM"用户上传了一张图，检索结果已基于图+文字综合给出，
      你只需基于 retrieved 推荐；若用户文字与图明显矛盾，以文字为准"。
    """
    _ = image_path  # 显式吃掉参数：retrieve 阶段已用，prompt 里不再泄漏
    image_rules = """\n
本轮用户上传了一张图（图相似 + 文字约束综合检索后的结果已在 <retrieved_products> 中给出）。
推荐时请遵守：
- 优先承接用户的文字诉求（图给出视觉风格 / 类型锚点，文字给出价格/品牌/场景约束）；
- 若图文明显矛盾（例如用户文字说"不要 X 品牌"但图正好是 X 品牌），以**文字为准**，
  从 <retrieved_products> 中挑符合文字约束的；
- 介绍商品时不要主观评价"和你上传的图很像/不像"，因为相似度判断已由检索完成。"""

    system_parts = [_BASE_RULES + image_rules]
    summary_block = _format_summary_block(summary)
    if summary_block:
        system_parts.extend(["", summary_block])
    system_parts.extend(["", _format_retrieved_block(retrieved)])
    msgs: list[dict] = [{"role": "system", "content": "\n".join(system_parts)}]
    msgs.extend(_history_messages(history))
    msgs.append({"role": "user", "content": user_message})
    return msgs
```

- [ ] **Step 5.4: 跑测试验证通过**

Run:

```bash
cd server && pytest tests/test_prompts.py -v
```

Expected: 现有 prompts 测试全过 + 2 新增过。

- [ ] **Step 5.5: 等待人工审阅 + commit**

---

## Task 6: MultimodalBranch（Agent 编排层图文融合分支）

**Files:**
- Create: `server/app/agent/multimodal_branch.py`
- Test: `server/tests/test_multimodal_branch.py`

- [ ] **Step 6.1: 写测试**

```python
# server/tests/test_multimodal_branch.py
"""MultimodalBranch：图+文 → query_vector + filter_expr + retrieve 聚合。

不调真实 vision API / 真实 Milvus：注入 fake embedder + fake retriever + fake cache。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.agent.multimodal_branch import MultimodalBranch
from app.agent.query_rewriter import ParsedQuery
from app.rag.retriever import RetrievedProduct


class _StubCache:
    def __init__(self, store: dict | None = None):
        self._store = store or {}
    async def get(self, image_id: str):
        return self._store.get(image_id)
    async def put(self, image_id: str, vec, path):
        self._store[image_id] = (vec, path)


class _StubEmbedder:
    def __init__(self):
        self.called_with: dict = {}
    def embed_multimodal(self, *, text=None, image_path=None):
        self.called_with = {"text": text, "image_path": image_path}
        return [0.5] * 4
    def embed_image(self, image_path: str):
        self.called_with = {"image_path": image_path}
        return [0.6] * 4


class _StubRewriter:
    def __init__(self, parsed: ParsedQuery):
        self._parsed = parsed
    async def parse(self, msg, *, history=None, summary=None) -> ParsedQuery:
        return self._parsed


def _mk_hit(pid: str, score: float = 0.9, chunk_type: str = "image", brand: str = "X") -> dict:
    """模拟 milvus store.search 返回的命中格式（list[dict]）。"""
    return {
        "id": hash(pid) & 0xFFFFFFFF,
        "distance": score,
        "entity": {
            "product_id": pid,
            "chunk_type": chunk_type,
            "text": f"{pid} 占位",
            "category": "美妆",
            "sub_category": "精华",
            "brand": brand,
            "base_price": 100.0,
            "min_sku_price": 90.0,
            "max_sku_price": 110.0,
            "rating": 5,
            "source_id": f"{pid}#{chunk_type}#0",
        },
    }


class _StubStore:
    """模拟 ProductTextStore.search：MultimodalBranch 实际调用 retriever.store.search。"""
    def __init__(self, hits: list[dict]):
        self._hits = hits
        self.search_calls: list[dict] = []
    def search(self, *, query_vector, top_k=20, filter_expr=None):
        self.search_calls.append({"filter_expr": filter_expr, "top_k": top_k})
        return list(self._hits)


class _StubRetriever:
    """MultimodalBranch 通过 retriever.store.search 走底层，所以 stub 只需 expose .store。"""
    def __init__(self, hits: list[dict]):
        self.store = _StubStore(hits)
    @property
    def search_calls(self) -> list[dict]:
        return self.store.search_calls


@pytest.mark.asyncio
async def test_branch_uses_cached_vec_when_available():
    cached_vec = [0.1] * 4
    cache = _StubCache({"img1": (cached_vec, "/tmp/img1.jpg")})
    embedder = _StubEmbedder()
    rewriter = _StubRewriter(ParsedQuery(search_query="这个"))
    retriever = _StubRetriever([_mk_hit("p_a")])

    branch = MultimodalBranch(
        embedder=embedder, retriever=retriever, cache=cache,
        query_rewriter=rewriter, structured_retriever=None,
    )
    result = await branch.handle(
        message="这个", image_id="img1", history=None, summary=None,
    )

    assert result.query_vector == cached_vec
    # 没有再调 embed_multimodal（缓存命中）
    assert embedder.called_with == {}
    assert [p.product_id for p in result.retrieved] == ["p_a"]


@pytest.mark.asyncio
async def test_branch_recomputes_when_cache_miss():
    cache = _StubCache({})  # 全空
    embedder = _StubEmbedder()
    rewriter = _StubRewriter(ParsedQuery(search_query="便宜的"))
    retriever = _StubRetriever([_mk_hit("p_a")])

    branch = MultimodalBranch(
        embedder=embedder, retriever=retriever, cache=cache,
        query_rewriter=rewriter, structured_retriever=None,
        fallback_image_path_resolver=lambda iid: f"/tmp/{iid}.jpg",
    )
    result = await branch.handle(
        message="便宜的", image_id="miss", history=None, summary=None,
    )

    # 应该 fallback 调 embed_multimodal
    assert embedder.called_with["image_path"] == "/tmp/miss.jpg"
    assert embedder.called_with["text"] == "便宜的"
    assert len(result.query_vector) == 4
    # 重算结果应被回填到缓存
    cached = await cache.get("miss")
    assert cached is not None


@pytest.mark.asyncio
async def test_branch_attaches_chunk_type_filter_for_image_and_title():
    cache = _StubCache({"x": ([0.0] * 4, "/tmp/x.jpg")})
    embedder = _StubEmbedder()
    rewriter = _StubRewriter(ParsedQuery(search_query="同款"))
    retriever = _StubRetriever([_mk_hit("p_a")])

    branch = MultimodalBranch(
        embedder=embedder, retriever=retriever, cache=cache,
        query_rewriter=rewriter, structured_retriever=None,
    )
    await branch.handle(message="同款", image_id="x", history=None, summary=None)

    assert len(retriever.search_calls) == 1
    fexpr = retriever.search_calls[0]["filter_expr"]
    assert 'chunk_type in ["image", "title"]' in fexpr


@pytest.mark.asyncio
async def test_branch_combines_structural_filter_with_chunk_type():
    cache = _StubCache({"x": ([0.0] * 4, "/tmp/x.jpg")})
    embedder = _StubEmbedder()
    parsed = ParsedQuery(
        search_query="同款", price_max=1000.0, brands_exclude=["耐克"],
    )
    rewriter = _StubRewriter(parsed)
    retriever = _StubRetriever([_mk_hit("p_a")])

    branch = MultimodalBranch(
        embedder=embedder, retriever=retriever, cache=cache,
        query_rewriter=rewriter, structured_retriever=None,
    )
    await branch.handle(
        message="同款 1000 以下不要耐克", image_id="x",
        history=None, summary=None,
    )

    fexpr = retriever.search_calls[0]["filter_expr"]
    # 同时包含价格 + brand_exclude + chunk_type
    assert "1000" in fexpr  # price_max
    assert "耐克" in fexpr
    assert "chunk_type" in fexpr


@pytest.mark.asyncio
async def test_branch_falls_back_to_text_only_when_image_missing_on_disk():
    """缓存 miss + 落盘图也找不到 → 退化到纯文本 embed，emit warning。"""
    cache = _StubCache({})

    class _EmbedderRaising(_StubEmbedder):
        def embed_multimodal(self, *, text=None, image_path=None):
            if image_path:
                raise FileNotFoundError(image_path)
            return [0.7] * 4

    embedder = _EmbedderRaising()
    rewriter = _StubRewriter(ParsedQuery(search_query="跑步鞋"))
    retriever = _StubRetriever([_mk_hit("p_b")])

    branch = MultimodalBranch(
        embedder=embedder, retriever=retriever, cache=cache,
        query_rewriter=rewriter, structured_retriever=None,
        fallback_image_path_resolver=lambda iid: f"/nope/{iid}.jpg",
    )
    result = await branch.handle(
        message="跑步鞋", image_id="lost", history=None, summary=None,
    )

    assert result.image_lost is True
    assert len(result.retrieved) == 1
```

- [ ] **Step 6.2: 跑测试验证失败**

Run:

```bash
cd server && pytest tests/test_multimodal_branch.py -v
```

Expected: ImportError - `cannot import name 'MultimodalBranch'`。

- [ ] **Step 6.3: 写实现**

```python
# server/app/agent/multimodal_branch.py
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

        # 4) retrieve
        # retriever.search 的 query 参数被 embedder 替换了语义：这里传 message 仅用作日志，
        # 真正的查询向量我们在下一步直接给 milvus_store。但 RagRetriever.search 内部会
        # 自己 embed query —— 我们需要绕过它。改成直接调底层 store.search：
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
```

- [ ] **Step 6.4: 跑测试验证通过**

Run:

```bash
cd server && pytest tests/test_multimodal_branch.py -v
```

Expected: 5 PASSED。

- [ ] **Step 6.5: 等待人工审阅 + commit**

---

## Task 7: Orchestrator + Deps 接入 image_id 分流

**Files:**
- Modify: `server/app/agent/orchestrator.py`
- Modify: `server/app/api/deps.py`
- Test: `server/tests/test_orchestrator.py`（加 case）

- [ ] **Step 7.1: 在 test_orchestrator.py 末尾加测试**

打开 `server/tests/test_orchestrator.py`，在文件末尾追加：

```python


@pytest.mark.asyncio
async def test_orchestrator_routes_to_multimodal_when_image_id_present(monkeypatch):
    """image_id 非空 → 走 MultimodalBranch；image_id 为空 → 走原 recommend 分支。"""
    from app.agent.multimodal_branch import MultimodalBranch, MultimodalResult
    from app.agent.query_rewriter import ParsedQuery
    from app.rag.retriever import RetrievedProduct
    from app.schemas.chat import ChatRequest

    called: dict = {"mm": 0, "rec": 0}

    async def fake_handle(self, *, message, image_id, history, summary):
        called["mm"] += 1
        return MultimodalResult(
            query_vector=[0.1] * 4,
            retrieved=[RetrievedProduct(
                product_id="p_x", score=0.9, brand="X", category="美妆",
                sub_category="精华", base_price=100.0,
                best_chunk_text="", supporting_chunks=[], title="X 商品",
            )],
            parsed=ParsedQuery(search_query=message),
            image_lost=False,
        )

    monkeypatch.setattr(MultimodalBranch, "handle", fake_handle)

    orch = _make_orchestrator_with_multimodal()

    # image_id 非空 → mm 分支
    req = ChatRequest(session_id=None, message="找同款", image_id="img-abc")
    events = [e async for e in orch.orchestrate(req)]
    assert called["mm"] == 1
    assert any(e["event"] == "product_card" for e in events) or any(e["event"] == "token" for e in events)

    # image_id 为空 → 不走 mm 分支
    called["mm"] = 0
    req2 = ChatRequest(session_id=None, message="推荐手机", image_id=None)
    events2 = [e async for e in orch.orchestrate(req2)]
    assert called["mm"] == 0


def _make_orchestrator_with_multimodal():
    """构造一个挂载 stub multimodal_branch + 现有 stub 的 orchestrator。

    现有 test_orchestrator.py 里应该已有类似 helper；这里写独立版本避免依赖私有 fixture。
    """
    from app.agent.memory import ConversationMemory
    from app.agent.orchestrator import AgentOrchestrator
    from app.rag.retriever import RetrievedProduct

    class _StubLLM:
        async def chat_stream(self, messages, **kw):
            yield "推荐 p_x 一款。\n```product_cards\n[{\"product_id\":\"p_x\",\"reason\":\"匹配\"}]\n```"

    class _StubRetriever:
        def search(self, q, **kw):
            return [RetrievedProduct(
                product_id="p_x", score=0.9, brand="X", category="美妆",
                sub_category="精华", base_price=100.0,
                best_chunk_text="", supporting_chunks=[], title="X 商品",
            )]

    class _StubProductRepo:
        async def get_card_view(self, pid):
            return {
                "product_id": pid, "title": "X 商品", "brand": "X",
                "category": "美妆", "image_url": "/static/x.jpg",
                "price_range": {"min": 100, "max": 200}, "skus": [],
            }

    class _StubMultimodalBranch:
        # 占位实例，handle 被 monkeypatch 替换
        async def handle(self, **kw):
            raise NotImplementedError

    return AgentOrchestrator(
        retriever=_StubRetriever(),
        llm=_StubLLM(),
        product_repo=_StubProductRepo(),
        memory=ConversationMemory(),
        multimodal_branch=_StubMultimodalBranch(),
    )
```

- [ ] **Step 7.2: 跑测试验证失败**

Run:

```bash
cd server && pytest tests/test_orchestrator.py::test_orchestrator_routes_to_multimodal_when_image_id_present -v
```

Expected: FAIL with `TypeError: AgentOrchestrator.__init__() got an unexpected keyword argument 'multimodal_branch'`。

- [ ] **Step 7.3: 在 orchestrator.py 加 multimodal_branch 入参 + 分流**

打开 `server/app/agent/orchestrator.py`。

**改动 1**：在 import 段加：

```python
from app.agent.multimodal_branch import MultimodalBranch
```

**改动 2**：`__init__` 加参数。把：

```python
        structured_retriever: StructuredRetriever | None = None,
    ) -> None:
```

替换成：

```python
        structured_retriever: StructuredRetriever | None = None,
        multimodal_branch: MultimodalBranch | None = None,
    ) -> None:
```

并在 `self.structured_retriever = structured_retriever` 行后追加：

```python
        # Phase 5：image_id 非空时走该分支；None 时回退到 Phase 4 路径
        self.multimodal_branch = multimodal_branch
```

**改动 3**：在 `orchestrate(self, req)` 方法的开头（`yield {"event": "session", ...}` 之后）加 image_id 分流。把：

```python
        # 0) Phase 4-4：进入新一轮前，看看上一轮 save_turn 后是否需要摘要
```

替换成：

```python
        # Phase 5：image_id 非空 → 走 multimodal 分支。短路其它意图判定。
        if req.image_id and self.multimodal_branch is not None:
            async for evt in self._multimodal_orchestrate(req, session):
                yield evt
            return

        # 0) Phase 4-4：进入新一轮前，看看上一轮 save_turn 后是否需要摘要
```

**改动 4**：在 `_classify_error` 之前（文件末尾的辅助函数前）追加 `_multimodal_orchestrate` 方法：

```python
    async def _multimodal_orchestrate(self, req: ChatRequest, session) -> AsyncIterator[dict]:
        """Phase 5 图+文分支：检索由 MultimodalBranch 完成，其余事件流复用现有路径。"""
        from app.agent.prompts import build_image_search_messages

        yield {"event": "status", "data": {"stage": "parsing"}}

        try:
            mm_result = await self.multimodal_branch.handle(
                message=req.message,
                image_id=req.image_id,
                history=session.history,
                summary=session.summary,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("multimodal_branch.handle 异常，emit error + done：%s", exc)
            yield {"event": "error", "data": {"code": "IMAGE_SEARCH_FAIL", "message": str(exc)[:200]}}
            yield {"event": "done", "data": {"finish_reason": "error"}}
            return

        if mm_result.image_lost:
            yield {"event": "warning", "data": {"code": "IMAGE_LOST", "message": "图片已失效，按文字处理"}}

        retrieved = mm_result.retrieved
        yield {"event": "status", "data": {"stage": "generating"}}
        messages = build_image_search_messages(
            user_message=req.message,
            image_path="",  # prompt 不消费实际路径
            retrieved=retrieved,
            history=session.history,
            summary=session.summary,
        )

        allowed_ids = {p.product_id for p in retrieved}
        extractor = ProductCardExtractor(allowed_ids=allowed_ids)
        emitted_card_ids: list[str] = []
        full_visible = ""

        try:
            async for delta in self.llm.chat_stream(messages):
                visible, cards = extractor.feed(delta)
                if visible:
                    full_visible += visible
                    yield {"event": "token", "data": {"text": visible}}
                for card in cards:
                    hydrated = await self._hydrate_card(card)
                    if hydrated is None:
                        continue
                    emitted_card_ids.append(hydrated["product_id"])
                    yield {"event": "product_card", "data": hydrated}
            tail_visible, tail_cards = extractor.finalize()
            if tail_visible:
                full_visible += tail_visible
                yield {"event": "token", "data": {"text": tail_visible}}
            for card in tail_cards:
                hydrated = await self._hydrate_card(card)
                if hydrated is None:
                    continue
                emitted_card_ids.append(hydrated["product_id"])
                yield {"event": "product_card", "data": hydrated}
        except Exception as exc:
            logger.exception("LLM 流式异常（图搜分支），走降级链路")
            code = _classify_error(exc)
            yield {"event": "error", "data": {"code": code, "message": str(exc)[:200]}}
            tip = "模型暂不可用，先给你看几款最匹配的商品："
            yield {"event": "token", "data": {"text": tip}}
            full_visible += tip
            for p in retrieved[:_FALLBACK_CARDS]:
                hydrated = await self._hydrate_card({"product_id": p.product_id, "reason": "图搜 Top-K 兜底"})
                if hydrated is None:
                    continue
                emitted_card_ids.append(hydrated["product_id"])
                yield {"event": "product_card", "data": hydrated}

        self.memory.save_turn(session.id, req.message, full_visible, emitted_card_ids)
        yield {"event": "done", "data": {"finish_reason": "stop"}}
```

- [ ] **Step 7.4: 把 MultimodalBranch 注入 deps**

打开 `server/app/api/deps.py`。

**改动 1**：import 段加：

```python
from app.agent.multimodal_branch import MultimodalBranch, build_multimodal_branch
```

**改动 2**：在 `get_image_embed_cache` 之后追加：

```python
@lru_cache(maxsize=1)
def get_multimodal_branch() -> MultimodalBranch:
    return build_multimodal_branch(
        embedder=get_retriever().embedder,
        retriever=get_retriever(),
        cache=get_image_embed_cache(),
        query_rewriter=get_query_rewriter(),
        structured_retriever=get_structured_retriever(),
    )
```

**改动 3**：把 `get_orchestrator` 改成：

```python
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
```

- [ ] **Step 7.5: 跑测试验证通过**

Run:

```bash
cd server && pytest tests/test_orchestrator.py -v
```

Expected: 现有 orchestrator 测试 + 1 新增全过。

- [ ] **Step 7.6: 跑全套测试**

Run:

```bash
cd server && pytest -q
```

Expected: 199 PASSED（约数；Phase 4 基线 176 + 本期新增 23）。

- [ ] **Step 7.7: 等待人工审阅 + commit**

---

## Task 8: iOS ImagePicker + UploadService

**Files:**
- Create: `client/ShoppingGuide/Features/Chat/ImagePicker.swift`
- Create: `client/ShoppingGuide/Services/UploadService.swift`
- Modify: `client/ShoppingGuide/Models/ChatMessage.swift`

> **iOS 测试节奏**：Xcode 自带 XCTest，但本期改动主要是 UI + 网络调用，单测覆盖率有限，主要靠 Xcode 模拟器 happy path 验收。

- [ ] **Step 8.1: 在 ChatMessage 模型加图片字段**

打开 `client/ShoppingGuide/Models/ChatMessage.swift`，在已有字段后追加：

```swift
    // Phase 5：用户消息可选携带本地缩略图，用于气泡内嵌渲染
    var localImageURL: URL? = nil
```

如该文件用的是 struct + memberwise init，确认其它构造点不被破坏（默认 nil，向后兼容）。

- [ ] **Step 8.2: 创建 ImagePicker.swift**

```swift
// client/ShoppingGuide/Features/Chat/ImagePicker.swift
// PhotosPicker 封装 + 压缩。
//
// 设计点：
// - 仅相册选图；相机捕获在 demo 阶段不必要；
// - 压缩到 ≤ 1 MB / 短边 ≤ 1600：和后端 _MAX_BYTES 对齐，超大图客户端先压再传；
// - 输出 Data + 本地临时 URL（气泡渲染用，避免重复解压）。

import PhotosUI
import SwiftUI
import UIKit

struct PickedImage {
    let data: Data
    let localURL: URL
}

enum ImagePickerError: Error {
    case invalidItem
    case compressionFailed
}

struct ImagePicker: View {
    @Binding var selection: PhotosPickerItem?
    @Binding var picked: PickedImage?
    @Binding var errorMessage: String?

    var body: some View {
        PhotosPicker(
            selection: $selection,
            matching: .images,
            photoLibrary: .shared()
        ) {
            Image(systemName: "camera")
                .resizable()
                .scaledToFit()
                .frame(width: 22, height: 22)
                .foregroundColor(.priceCatOrange)  // 复用品牌色
        }
        .onChange(of: selection) { _, newItem in
            Task { @MainActor in
                guard let newItem else { return }
                do {
                    let result = try await loadAndCompress(item: newItem)
                    picked = result
                } catch {
                    errorMessage = "图片读取失败，请重试"
                }
            }
        }
    }

    private func loadAndCompress(item: PhotosPickerItem) async throws -> PickedImage {
        guard let raw = try await item.loadTransferable(type: Data.self) else {
            throw ImagePickerError.invalidItem
        }
        guard let uiimg = UIImage(data: raw) else {
            throw ImagePickerError.invalidItem
        }
        let resized = uiimg.resizedToShortSide(maxShortSide: 1600)
        // 逐级降质量直到 ≤ 1 MB
        var quality: CGFloat = 0.85
        var data: Data? = resized.jpegData(compressionQuality: quality)
        while let d = data, d.count > 1024 * 1024, quality > 0.3 {
            quality -= 0.1
            data = resized.jpegData(compressionQuality: quality)
        }
        guard let final = data else {
            throw ImagePickerError.compressionFailed
        }
        // 写入临时目录给气泡 AsyncImage / Image(uiImage:) 用
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("\(UUID().uuidString).jpg")
        try final.write(to: tmp)
        return PickedImage(data: final, localURL: tmp)
    }
}

private extension UIImage {
    func resizedToShortSide(maxShortSide: CGFloat) -> UIImage {
        let shortSide = min(size.width, size.height)
        guard shortSide > maxShortSide else { return self }
        let scale = maxShortSide / shortSide
        let newSize = CGSize(width: size.width * scale, height: size.height * scale)
        let renderer = UIGraphicsImageRenderer(size: newSize)
        return renderer.image { _ in
            self.draw(in: CGRect(origin: .zero, size: newSize))
        }
    }
}
```

> **注意**：`.priceCatOrange` 这个颜色常量需要确认在客户端代码里已经定义（Phase 3 重做时引入）。如果命名不一致，替换成实际的品牌色访问点。

- [ ] **Step 8.3: 创建 UploadService.swift**

```swift
// client/ShoppingGuide/Services/UploadService.swift
// POST /api/v1/upload/image —— multipart 上传，返回 image_id。
//
// 与 ChatService 分文件：上传失败 / chat 失败错误处理独立，
// 避免一个 service 类承担太多 endpoint。

import Foundation

struct UploadResponse: Codable {
    let imageId: String
    let previewUrl: String?
}

enum UploadError: Error, LocalizedError {
    case invalidResponse
    case server(status: Int, message: String)
    case degraded(message: String)

    var errorDescription: String? {
        switch self {
        case .invalidResponse: return "服务返回异常"
        case .server(_, let m): return m
        case .degraded(let m): return m
        }
    }
}

final class UploadService {
    private let session: URLSession
    private let baseURL: URL

    init(baseURL: URL, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.session = session
    }

    func upload(image data: Data, filename: String = "upload.jpg") async throws -> String {
        let url = baseURL.appendingPathComponent("/api/v1/upload/image")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        let boundary = "Boundary-\(UUID().uuidString)"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        req.httpBody = makeMultipartBody(boundary: boundary, fieldName: "file", filename: filename, mime: "image/jpeg", data: data)

        let (responseData, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw UploadError.invalidResponse }
        if http.statusCode == 200 {
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            let parsed = try decoder.decode(UploadResponse.self, from: responseData)
            return parsed.imageId
        }
        // 503 降级：服务器告知可以纯文本继续
        if http.statusCode == 503 {
            let msg = String(data: responseData, encoding: .utf8) ?? "图片识别繁忙"
            throw UploadError.degraded(message: msg)
        }
        // 其它错误：尽量从 body 读 detail
        let detail = (try? JSONDecoder().decode(ServerErrorBody.self, from: responseData))?.detail ?? "上传失败"
        throw UploadError.server(status: http.statusCode, message: detail)
    }

    private func makeMultipartBody(
        boundary: String, fieldName: String, filename: String, mime: String, data: Data,
    ) -> Data {
        var body = Data()
        let lineBreak = "\r\n"
        func append(_ s: String) { body.append(s.data(using: .utf8)!) }
        append("--\(boundary)\(lineBreak)")
        append("Content-Disposition: form-data; name=\"\(fieldName)\"; filename=\"\(filename)\"\(lineBreak)")
        append("Content-Type: \(mime)\(lineBreak)\(lineBreak)")
        body.append(data)
        append(lineBreak)
        append("--\(boundary)--\(lineBreak)")
        return body
    }
}

private struct ServerErrorBody: Decodable {
    let detail: String?
}
```

- [ ] **Step 8.4: 跑 Xcode build 验证编译过**

Run（在 Xcode 项目目录下）：

```bash
cd client && xcodebuild -project ShoppingGuide.xcodeproj -scheme ShoppingGuide -destination 'platform=iOS Simulator,name=iPhone 15' -configuration Debug build 2>&1 | tail -20
```

Expected: `** BUILD SUCCEEDED **`。如有 warning 可接受。

- [ ] **Step 8.5: 等待人工审阅 + commit**

---

## Task 9: iOS ChatView + MessageBubble UI 接入

**Files:**
- Modify: `client/ShoppingGuide/Features/Chat/ChatView.swift`
- Modify: `client/ShoppingGuide/Features/Chat/MessageBubble.swift`
- Modify: `client/ShoppingGuide/Services/ChatService.swift`

- [ ] **Step 9.1: 在 ChatService 加 image_id 透传**

打开 `client/ShoppingGuide/Services/ChatService.swift`，找到发送 chat 请求的方法（通常叫 `streamChat` 或 `send`）。在请求 body 构造时把可选 `imageId` 加进去。

例如，如果原构造像这样：

```swift
let body: [String: Any] = [
    "session_id": sessionId as Any,
    "message": message,
]
```

改成：

```swift
var body: [String: Any] = [
    "session_id": sessionId as Any,
    "message": message,
]
if let imageId = imageId {
    body["image_id"] = imageId
}
```

并把发送方法签名加上 `imageId: String? = nil` 参数：

```swift
func send(message: String, sessionId: String?, imageId: String? = nil) async -> AsyncStream<...>
```

> **注意**：实际函数签名以现有 ChatService.swift 为准；写 plan 时未读取该文件细节，执行时按现有 API 形态加参数即可。

- [ ] **Step 9.2: 在 ChatView 加相机按钮 + 缩略图条 + 发送流程改造**

打开 `client/ShoppingGuide/Features/Chat/ChatView.swift`。

**改动概要**：
1. 在 `@State` 区追加：

```swift
@State private var pickedImage: PickedImage? = nil
@State private var photosPickerItem: PhotosPickerItem? = nil
@State private var uploadErrorMessage: String? = nil
@State private var isUploading = false
```

2. 在输入栏（输入框 + 发送按钮的 HStack）之前**叠加一个缩略图条**（仅 picked 非空时显示）：

```swift
if let p = pickedImage {
    HStack(spacing: 8) {
        if let uiimg = UIImage(contentsOfFile: p.localURL.path) {
            Image(uiImage: uiimg)
                .resizable()
                .scaledToFill()
                .frame(width: 56, height: 56)
                .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        Spacer()
        Button(action: { pickedImage = nil; photosPickerItem = nil }) {
            Image(systemName: "xmark.circle.fill")
                .foregroundColor(.secondary)
                .imageScale(.large)
        }
    }
    .padding(.horizontal, 12)
    .padding(.top, 4)
}
```

3. 在输入栏的最左侧加 ImagePicker 入口：

```swift
ImagePicker(
    selection: $photosPickerItem,
    picked: $pickedImage,
    errorMessage: $uploadErrorMessage,
)
```

4. 改造 `sendMessage()` 方法：

```swift
private func sendMessage() {
    let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !text.isEmpty || pickedImage != nil else { return }

    let picked = pickedImage
    let localURL = picked?.localURL

    // 入消息列表（用户气泡先显示，图先用本地路径）
    let userMsg = ChatMessage(
        id: UUID(),
        role: .user,
        content: text,
        localImageURL: localURL,
    )
    messages.append(userMsg)

    // 清空输入与缩略图
    inputText = ""
    pickedImage = nil
    photosPickerItem = nil

    Task {
        var imageId: String? = nil
        if let imgData = picked?.data {
            isUploading = true
            defer { isUploading = false }
            do {
                imageId = try await uploadService.upload(image: imgData)
            } catch let UploadError.degraded(message) {
                // 降级：仍发文字
                uploadErrorMessage = message + "（已按文字继续）"
            } catch {
                uploadErrorMessage = "图片上传失败：\(error.localizedDescription)"
                return  // 不发 chat（用户先看到错误）
            }
        }
        await streamChat(message: text, imageId: imageId)
    }
}
```

5. 在 `streamChat` 里把 imageId 透传给 ChatService.send：

```swift
private func streamChat(message: String, imageId: String? = nil) async {
    let stream = chatService.send(
        message: message,
        sessionId: sessionId,
        imageId: imageId,
    )
    // 后续 SSE 处理沿用原逻辑
    ...
}
```

> **注意**：实际 ChatView 代码结构以现有文件为准，上面是改动**模式示意**——执行 task 时按现有变量命名和结构整合。

- [ ] **Step 9.3: 在 MessageBubble 加缩略图渲染**

打开 `client/ShoppingGuide/Features/Chat/MessageBubble.swift`。

在用户气泡渲染（`if message.role == .user`）的内容上方追加缩略图：

```swift
if let localURL = message.localImageURL,
   let img = UIImage(contentsOfFile: localURL.path) {
    Image(uiImage: img)
        .resizable()
        .scaledToFill()
        .frame(maxWidth: 200, maxHeight: 200)
        .clipped()
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .padding(.bottom, 6)
}
Text(message.content)
    // 现有 Text 样式不变
```

> **注意**：现有用户气泡用 `Text` + HStack/Spacer 撑右侧 hug 宽度。本改动是在 Bubble 的 VStack 顶部插入图片。如现有不是 VStack，把缩略图 + 文字包成 VStack。

- [ ] **Step 9.4: Xcode 模拟器 happy path 手动跑通**

启动 server（`cd server && uvicorn app.main:app --reload`）+ Xcode 模拟器：

1. 启动 app → 进入 ChatView
2. 点击相机按钮 → 从模拟器相册选一张图（先用模拟器内置照片）
3. 输入栏上方出现缩略图条 + 输入"找类似的"
4. 点发送 → 用户气泡上嵌缩略图 + 下方文字
5. 服务端日志显示 `upload 成功：image_id=...` + multimodal 检索
6. iOS 端收到推荐 token + 商品卡片

- [ ] **Step 9.5: 用商品自身图做精确同款测试**

把数据集 `ecommerce_agent_dataset/3_服饰运动/images/p_sneaker_005_live.jpg` 放进模拟器相册，发送"找同款"：

Expected: Top-1 卡片 product_id == `p_sneaker_005`。

- [ ] **Step 9.6: 等待人工审阅 + commit**

---

## Task 10: 评测黄金集 + 评测脚本 + Smoke

**Files:**
- Create: `server/scripts/eval/image_queries.json`
- Create: `server/scripts/eval_image_search.py`
- Create: `server/scripts/smoke_image_chat.sh`
- Create: `docs/phase5_eval_report.md`（脚本运行后产出）

- [ ] **Step 10.1: 写黄金集**

```json
// server/scripts/eval/image_queries.json
[
  {
    "case_id": "same-1",
    "type": "same_item",
    "image_path": "ecommerce_agent_dataset/3_服饰运动/images/p_sneaker_005_live.jpg",
    "text": "",
    "expected_pids": ["p_sneaker_005"]
  },
  {
    "case_id": "same-2",
    "type": "same_item",
    "image_path": "ecommerce_agent_dataset/1_美妆护肤/images/p_beauty_010_live.jpg",
    "text": "",
    "expected_pids": ["p_beauty_010"]
  },
  {
    "case_id": "same-3",
    "type": "same_item",
    "image_path": "ecommerce_agent_dataset/2_数码电子/images/p_digital_008_live.jpg",
    "text": "",
    "expected_pids": ["p_digital_008"]
  },
  {
    "case_id": "same-4",
    "type": "same_item",
    "image_path": "ecommerce_agent_dataset/4_食品生活/images/p_food_015_live.jpg",
    "text": "",
    "expected_pids": ["p_food_015"]
  },
  {
    "case_id": "similar-1",
    "type": "similar",
    "image_path": "ecommerce_agent_dataset/3_服饰运动/images/p_sneaker_005_live.jpg",
    "text": "类似款",
    "expected_pids": ["p_sneaker_005", "p_sneaker_006", "p_sneaker_007", "p_sneaker_001"]
  },
  {
    "case_id": "similar-2",
    "type": "similar",
    "image_path": "ecommerce_agent_dataset/1_美妆护肤/images/p_beauty_010_live.jpg",
    "text": "类似的精华",
    "expected_pids": ["p_beauty_010", "p_beauty_001", "p_beauty_011"]
  },
  {
    "case_id": "similar-3",
    "type": "similar",
    "image_path": "ecommerce_agent_dataset/2_数码电子/images/p_digital_008_live.jpg",
    "text": "差不多的耳机",
    "expected_pids": ["p_digital_008", "p_digital_001", "p_digital_002"]
  },
  {
    "case_id": "similar-4",
    "type": "similar",
    "image_path": "ecommerce_agent_dataset/3_服饰运动/images/p_sneaker_020_live.jpg",
    "text": "类似款",
    "expected_pids": ["p_sneaker_020", "p_sneaker_010", "p_sneaker_021"]
  },
  {
    "case_id": "similar-5",
    "type": "similar",
    "image_path": "ecommerce_agent_dataset/4_食品生活/images/p_food_002_live.jpg",
    "text": "差不多的零食",
    "expected_pids": ["p_food_002", "p_food_001", "p_food_003"]
  },
  {
    "case_id": "price-1",
    "type": "image_plus_price",
    "image_path": "ecommerce_agent_dataset/3_服饰运动/images/p_sneaker_005_live.jpg",
    "text": "1000 元以下的",
    "expected_pids": ["p_sneaker_007", "p_sneaker_008", "p_sneaker_009"]
  },
  {
    "case_id": "price-2",
    "type": "image_plus_price",
    "image_path": "ecommerce_agent_dataset/1_美妆护肤/images/p_beauty_001_live.jpg",
    "text": "200 元以下",
    "expected_pids": ["p_beauty_005", "p_beauty_011", "p_beauty_020"]
  },
  {
    "case_id": "price-3",
    "type": "image_plus_price",
    "image_path": "ecommerce_agent_dataset/2_数码电子/images/p_digital_001_live.jpg",
    "text": "500 以下",
    "expected_pids": ["p_digital_010", "p_digital_015"]
  },
  {
    "case_id": "brand-exclude-1",
    "type": "image_plus_brand_exclude",
    "image_path": "ecommerce_agent_dataset/3_服饰运动/images/p_sneaker_005_live.jpg",
    "text": "不要耐克",
    "expected_pids": ["p_sneaker_010", "p_sneaker_020", "p_sneaker_015"]
  },
  {
    "case_id": "brand-exclude-2",
    "type": "image_plus_brand_exclude",
    "image_path": "ecommerce_agent_dataset/1_美妆护肤/images/p_beauty_001_live.jpg",
    "text": "不要兰蔻",
    "expected_pids": ["p_beauty_011", "p_beauty_015", "p_beauty_020"]
  },
  {
    "case_id": "brand-exclude-3",
    "type": "image_plus_brand_exclude",
    "image_path": "ecommerce_agent_dataset/2_数码电子/images/p_digital_001_live.jpg",
    "text": "不要苹果",
    "expected_pids": ["p_digital_010", "p_digital_015", "p_digital_020"]
  }
]
```

> **注意**：`expected_pids` 是基于现有数据集 100 件构造的"合理候选集"——pid 命名可能与实际数据集略有出入；运行前用 `ls ecommerce_agent_dataset/*/images/` 校对一遍真实 product_id，把不存在的 pid 删除/替换。允许 `expected_pids` 为多个，命中任一即算 Top-N。

- [ ] **Step 10.2: 写评测脚本**

```python
# server/scripts/eval_image_search.py
"""Phase 5 多模态图搜评测：跑 image_queries.json 黄金集，按四类输出 Top-1/3/5。

用法：

    cd server
    python -m scripts.eval_image_search                    # 输出到 stdout
    python -m scripts.eval_image_search --output docs/phase5_eval_report.md
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.agent.multimodal_branch import build_multimodal_branch
from app.api.deps import (
    get_image_embed_cache, get_query_rewriter, get_retriever, get_structured_retriever,
)
from app.config import settings
from app.rag.embedder import build_embedder_from_settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

QUERIES_PATH = Path(__file__).parent / "eval" / "image_queries.json"


def _topk_hit(predicted: list[str], expected: list[str], k: int) -> bool:
    """前 k 个预测中只要命中 expected 任意一个即算成功。"""
    return any(p in expected for p in predicted[:k])


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--queries", type=Path, default=QUERIES_PATH)
    args = parser.parse_args()

    with args.queries.open("r", encoding="utf-8") as f:
        cases = json.load(f)
    logger.info("载入 %d 条评测 case", len(cases))

    # 用真实依赖构造一个 MultimodalBranch（脱离 FastAPI 容器）
    embedder = build_embedder_from_settings()
    embedder.dim  # 探测一次

    from app.rag.image_embed_cache import ImageEmbedCache
    from app.rag.milvus_store import COLLECTION_NAME, ProductTextStore
    from app.rag.retriever import RagRetriever

    store = ProductTextStore(db_path=settings.milvus_db_path, dim=embedder.dim)
    store.client.load_collection(COLLECTION_NAME)
    retriever = RagRetriever(embedder=embedder, store=store)

    branch = build_multimodal_branch(
        embedder=embedder,
        retriever=retriever,
        cache=ImageEmbedCache(),
        query_rewriter=get_query_rewriter(),
        structured_retriever=get_structured_retriever(),
    )

    # 跑每个 case
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    project_root = Path(__file__).resolve().parents[2]  # 项目根

    for c in cases:
        img_abs = project_root / c["image_path"]
        if not img_abs.exists():
            logger.warning("跳过 %s：图片缺失 %s", c["case_id"], img_abs)
            continue

        # 直接调 embedder.embed_multimodal（绕过 cache + upload）
        try:
            vec = embedder.embed_multimodal(
                text=c.get("text") or None,
                image_path=str(img_abs),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("%s embed 失败：%s", c["case_id"], exc)
            continue

        # 构造 filter_expr（结构化 + chunk_type）
        parsed = await branch.query_rewriter.parse(c.get("text", ""))
        structural = parsed.to_filter_expr()
        if structural:
            filter_expr = f'({structural}) and chunk_type in ["image", "title"]'
        else:
            filter_expr = 'chunk_type in ["image", "title"]'

        hits = store.search(query_vector=vec, top_k=30, filter_expr=filter_expr)
        from app.rag.retriever import _aggregate
        products = _aggregate(hits, top_n_products=10)
        predicted_pids = [p.product_id for p in products]
        record = {
            "case_id": c["case_id"],
            "predicted_top10": predicted_pids,
            "expected": c["expected_pids"],
            "top1": _topk_hit(predicted_pids, c["expected_pids"], 1),
            "top3": _topk_hit(predicted_pids, c["expected_pids"], 3),
            "top5": _topk_hit(predicted_pids, c["expected_pids"], 5),
        }
        by_type[c["type"]].append(record)

    # 汇总
    report_lines: list[str] = ["# Phase 5 多模态图搜评测报告\n"]
    summary_rows: list[str] = ["| 类型 | n | Top-1 | Top-3 | Top-5 |", "| --- | --- | --- | --- | --- |"]
    for typ, records in by_type.items():
        n = len(records)
        if n == 0:
            continue
        t1 = sum(r["top1"] for r in records) / n * 100
        t3 = sum(r["top3"] for r in records) / n * 100
        t5 = sum(r["top5"] for r in records) / n * 100
        summary_rows.append(f"| {typ} | {n} | {t1:.1f}% | {t3:.1f}% | {t5:.1f}% |")
    report_lines.append("\n## 汇总\n" + "\n".join(summary_rows) + "\n")

    # 详细
    report_lines.append("\n## 逐条详情\n")
    for typ, records in by_type.items():
        report_lines.append(f"### {typ}\n")
        for r in records:
            mark = "✅" if r["top1"] else ("🟡" if r["top3"] else "❌")
            report_lines.append(
                f"- {mark} `{r['case_id']}` — expected={r['expected']}, top3={r['predicted_top10'][:3]}"
            )
        report_lines.append("")

    out = "\n".join(report_lines)
    if args.output:
        args.output.write_text(out, encoding="utf-8")
        logger.info("报告写入 %s", args.output)
    else:
        print(out)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 10.3: 写 smoke 脚本**

```bash
#!/usr/bin/env bash
# server/scripts/smoke_image_chat.sh
# 端到端冒烟：upload → chat (with image_id) → 断言收到 token + product_card。
#
# 用法：bash scripts/smoke_image_chat.sh [image_path] [message]
set -euo pipefail

BASE="${BASE_URL:-http://127.0.0.1:8000}"
IMG="${1:-../ecommerce_agent_dataset/3_服饰运动/images/p_sneaker_005_live.jpg}"
MSG="${2:-找同款}"

if [ ! -f "$IMG" ]; then
  echo "图片不存在：$IMG"
  exit 1
fi

echo "== 1) upload $IMG"
UPLOAD_RESP=$(curl -s -X POST "$BASE/api/v1/upload/image" -F "file=@$IMG;type=image/jpeg")
echo "$UPLOAD_RESP" | python -m json.tool
IMAGE_ID=$(echo "$UPLOAD_RESP" | python -c "import sys,json;print(json.load(sys.stdin)['image_id'])")
echo "  → image_id=$IMAGE_ID"

echo "== 2) chat with image_id"
curl -N -s -X POST "$BASE/api/v1/chat/stream" \
  -H "Content-Type: application/json" \
  -d "{\"message\":\"$MSG\",\"image_id\":\"$IMAGE_ID\"}" | head -60
echo
echo "== 完成"
```

并赋可执行权限：

```bash
chmod +x server/scripts/smoke_image_chat.sh
```

- [ ] **Step 10.4: 跑评测**

确认 server 不需要启动（脚本直接调依赖）：

```bash
cd server && python -m scripts.eval_image_search --output ../docs/phase5_eval_report.md
```

Expected: `docs/phase5_eval_report.md` 生成，四类指标汇总表 + 逐条详情。

- [ ] **Step 10.5: 验证四类指标达标**

打开 `docs/phase5_eval_report.md` 看汇总表：

- `same_item` Top-1 ≥ 90%（4 条至少 3 条 Top-1 命中）
- `similar` Top-3 ≥ 80%
- `image_plus_price` Top-3 ≥ 80%
- `image_plus_brand_exclude` Top-3 ≥ 80%

如未达标：检查 image_queries.json 里 expected_pids 是否对齐真实数据集（可能 product_id 命名差异）；再确认 filter_expr 是否正确生成。

- [ ] **Step 10.6: 启动 server 跑 smoke**

终端 1：

```bash
cd server && uvicorn app.main:app --reload --port 8000
```

终端 2：

```bash
cd server && bash scripts/smoke_image_chat.sh
```

Expected: upload 返 200 + image_id；chat SSE 流里看到 `event: token` 和 `event: product_card`。

- [ ] **Step 10.7: 等待人工审阅 + commit**

---

## Task 11: README + 最终验收

**Files:**
- Modify: `README.md`

- [ ] **Step 11.1: 在 README 「阶段进度」一节把 Phase 5 改为完成**

打开 `README.md`，找到第 74 行附近：

```markdown
- [ ] Phase 5：加分项（业务闭环 / 多模态 / 性能）
```

替换成：

```markdown
- [x] **Phase 5**：加分项 —— 多模态图搜（拍照找货）([详情](#phase-5-多模态图搜)，[评测报告](docs/phase5_eval_report.md)，[设计文档](docs/05_多模态图搜设计.md))
```

- [ ] **Step 11.2: 在 README 增加 Phase 5 主章节**

在 Phase 4 章节之后、Phase 6 一节之前，追加：

````markdown
## Phase 5 多模态图搜

> 详细设计见 [`docs/05_多模态图搜设计.md`](docs/05_多模态图搜设计.md)；评测见 [`docs/phase5_eval_report.md`](docs/phase5_eval_report.md)。

在 Phase 4 对话能力的基础上叠加多模态输入路径：iOS 端拍照/选图 + 文字 → 二段式上传 → vision embedding 检索 + Phase 4 结构化筛选融合 → 商品推荐流。

### 关键设计

1. **共享向量空间**：Doubao multimodal_embeddings 把图与文编码到同一 2048 维空间，沿用 `products_text` collection，新增 `chunk_type='image'` 标量字段隔离。
2. **二段式 API**：`POST /upload/image` → `image_id` → `POST /chat {message, image_id}`。上传时同步算 vision embedding 缓存到 `ImageEmbedCache`（LRU+TTL），让 `/chat` 路径首 token 不再阻塞在 vision 编码。
3. **单向量多模态融合**：图+文一次性 embed → 单查询向量；价格/品牌/品类等结构化条件仍走 Phase 4 `query_rewriter` 抽 + scalar filter。retrieve 路径仍是**一次 milvus search**，filter_expr 多挂一个 `chunk_type IN ('image','title')` 让标题命中也参与召回。
4. **图文矛盾**：retrieve 层不做仲裁；Prompt 加规则"用户文字与图明显矛盾时以文字为准"由 LLM 处理。
5. **任意一环失败可退化**：vision API 限流 / 落盘图丢失 / Milvus 空结果 → 全部能退化到纯文本流，绝不出现"上传图后对话卡死"。

### 实测产出

```
build_image_index：100 张 _live.jpg → 100 条 image chunk → collection 总 1192 chunks
upload happy path：~700ms（含 vision embed 缓存预算）
multimodal chat：首 token ~1.2s（图缓存命中）/ ~1.8s（缓存 miss 重算）
```

### 评测结果

详见 `docs/phase5_eval_report.md`。

### 与 Phase 1-4 的关系

Phase 5 是**纯增量**——所有 Phase 1-4 文件零回退。新增分支处理器 `MultimodalBranch` 与 `clarify_detector` / `compare_planner` 同层级；orchestrator 入口按 `image_id` 是否非空决定走 Phase 4 还是 Phase 5 路径；纯文本对话体验完全不变。
````

- [ ] **Step 11.3: 最终验收清单**

逐条勾掉验收清单（与设计文档 §7 对齐）：

- [ ] 100 张商品图全部入向量库：`milvus_store.count(chunk_type='image') == 100`
- [ ] `/upload/image` happy path 在 iOS 模拟器跑通
- [ ] `/chat` 带 image_id 跑通：iOS 用户气泡显示缩略图 + 文字 + 收到推荐卡片
- [ ] smoke 四条 case 全过：
  - [ ] 上传 `p_sneaker_005_live.jpg` 原图 → Top-1 == p_sneaker_005
  - [ ] 上传 `p_sneaker_005_live.jpg` + "1000 元以下的" → 命中全部 ≤ 1000
  - [ ] 上传 `p_beauty_010_live.jpg` + "不要兰蔻" → 命中无兰蔻
  - [ ] 上传图后服务端重启 → 再发 chat 仍能正确推荐（缓存重算路径）
- [ ] `eval_image_search.py` 跑出报告，四类指标全部达标
- [ ] server 单测全过（约 199 例）
- [ ] README 加 Phase 5 章节

- [ ] **Step 11.4: 等待最终人工审阅 + commit**

---

## Task 12: 5C 协议与 ViewModel 逻辑测试

**Files:**
- Create: `client/ShoppingGuide/Features/Chat/SpeechRecognitionService.swift`
- Create: `client/ShoppingGuide/Features/Chat/SpeechSynthesisService.swift`
- Modify: `client/ShoppingGuide/Features/Chat/ChatViewModel.swift`
- Test: `client/Tests/ShoppingGuideKitTests/ChatViewModelTests.swift`

- [x] **Step 12.1: 写失败的 Swift 逻辑测试**

新增 fake speech recognizer / speaker，覆盖：

```swift
@Test func voiceInputPartialTranscriptUpdatesInputText() async
@Test func voiceInputFailureSetsNoticeAndStopsListening() async
@Test func sendStopsActiveVoiceInputBeforeStreaming() async
@Test func resetSessionStopsVoiceAndSpeechOutput() async
@Test func autoSpeakReadsAssistantReplyAfterDone() async
@Test func voiceInputCompletionStopsListening() async
@Test func staleVoiceCompletionAfterResetDoesNotMutateNewSession() async
```

Expected red: `ChatViewModel` 还没有 `startVoiceInput()` / `stopVoiceInput()` / `speakAssistantText()` / `autoSpeakEnabled` 等成员。

- [x] **Step 12.2: 定义可测试协议**

`SpeechRecognizing`：

```swift
public protocol SpeechRecognizing: Sendable {
    @MainActor
    func start(
        onPartialResult: @escaping @MainActor @Sendable (String) -> Void,
        onCompletion: @escaping @MainActor @Sendable (SpeechRecognitionError?) -> Void
    ) async throws

    @MainActor
    func stop()
}
```

`SpeechSpeaking`：

```swift
public protocol SpeechSpeaking: Sendable {
    @MainActor
    func speak(_ text: String, voice: SpeechVoice)

    @MainActor
    func stop()
}
```

- [x] **Step 12.3: ViewModel 最小接入**

`ChatViewModel` 新增：

```swift
@Published public var isListening: Bool = false
@Published public var voiceNotice: String? = nil
@Published public var autoSpeakEnabled: Bool = false
```

并注入：

```swift
private let speechRecognizer: SpeechRecognizing?
private let speechSpeaker: SpeechSpeaking?
```

新增行为：
- `startVoiceInput()`：启动 recognizer，partial 写入 `inputText`
- `stopVoiceInput()`：停止 recognizer，`isListening=false`
- `send()` 开头取消当前录音，避免旧 ASR 回调污染本轮发送
- `.done` 后如果 `autoSpeakEnabled`，播报 assistant 文本
- `resetSession()` 停止录音与播报
- `.done` 视为本轮流式结束，立即结束 `send()`，避免底层流未 finish 时输入栏被长时间锁住

---

## Task 13: 5C iOS 语音输入基础服务

**Files:**
- Modify: `client/ShoppingGuide/Features/Chat/SpeechRecognitionService.swift`
- Test: Xcode build

- [x] **Step 13.1: 实现 iOS SpeechRecognitionService**

生产实现只在 iOS App 编译路径使用，封装：
- `SFSpeechRecognizer(locale: Locale(identifier: "zh-CN"))`
- `SFSpeechAudioBufferRecognitionRequest`
- `SFSpeechRecognitionTask`
- `AVAudioEngine`
- `SFSpeechRecognizer.requestAuthorization`
- `AVAudioSession.requestRecordPermission`

错误映射：
- speech 权限拒绝：`SpeechRecognitionError.permissionDenied`
- 麦克风权限拒绝：`SpeechRecognitionError.microphoneDenied`
- recognizer 不可用：`SpeechRecognitionError.unavailable`
- task error：`SpeechRecognitionError.recognitionFailed(message:)`

- [x] **Step 13.2: 取消原生 TTS 朗读**

TTS 不再实现 `AVSpeechSynthesizer` 本地朗读降级。播报统一走服务端 `/api/v1/audio/tts` + OpenSpeech 音色，避免用户选择的 voice id 与系统本地音色不一致。

---

## Task 14: 5C Chat UI 接入

**Files:**
- Modify: `client/ShoppingGuide/Features/Chat/ChatView.swift`
- Modify: `client/ShoppingGuide/Features/Chat/MessageBubble.swift`
- Test: Xcode build + 手动 UI

- [x] **Step 14.1: 输入栏新增麦克风按钮**

按钮位置：相册按钮右侧、输入框左侧。

状态：
- 未录音：`mic.fill`
- 录音中：`mic.circle.fill`，品牌橙高亮
- 发送中禁用录音按钮

交互：
- 点击未录音 → `await viewModel.startVoiceInput()`
- 点击录音中 → `viewModel.stopVoiceInput()`

- [x] **Step 14.2: Header 新增自动播报开关**

在新建会话按钮左侧加 speaker 图标：
- 关闭：`speaker.wave.2.circle`
- 开启：`speaker.wave.2.circle.fill`
- 点击切换 `viewModel.autoSpeakEnabled`

- [x] **Step 14.3: MessageBubble 新增 assistant 播报按钮**

assistant 非 streaming 且正文非空时，气泡下方显示小 speaker 图标按钮。
点击调用 `onSpeakAssistant?(message.text)`。

---

## Task 15: 5C 权限、验收与构建

**Files:**
- Modify: `client/ShoppingGuide.xcodeproj/project.pbxproj`
- Test: `client` SwiftPM 测试（若当前工具链支持）+ Xcode build

- [x] **Step 15.1: 写入自动生成 Info.plist 权限描述**

Debug / Release 均新增：

```text
INFOPLIST_KEY_NSMicrophoneUsageDescription = "PriceCat 需要使用麦克风把你的语音问题转换为文字。";
INFOPLIST_KEY_NSSpeechRecognitionUsageDescription = "PriceCat 需要使用语音识别把你的语音问题转换为文字。";
```

- [ ] **Step 15.2: 验收场景**

手动验证：
- 点击麦克风，说“推荐蓝牙耳机” → 输入框出现转写文本
- 停止录音后点击发送 → 正常收到推荐
- assistant 回复后点击播报 → 听到语音播报
- 打开自动播报后发一条新消息 → assistant 完成后自动播报
- 新建会话时正在录音/播报 → 录音和播报立即停止
- 权限拒绝时文本输入和图片上传仍可用

状态：需要在实际前端/真机或可用麦克风的模拟器中手动验证。当前已通过 ViewModel 单测覆盖状态迁移，且已确认构建产物 Info.plist 包含语音权限键。

- [x] **Step 15.3: 最终验证**

Run:

```bash
cd client && swift test
```

如果本机 SwiftPM 工具链仍因 `no such module 'Testing'` 失败，记录原因并继续用 Xcode build 验证 App 编译。

Run:

```bash
/Applications/Xcode.app/Contents/Developer/usr/bin/xcodebuild \
  -project client/ShoppingGuide.xcodeproj \
  -scheme ShoppingGuide \
  -destination "generic/platform=iOS Simulator" \
  -derivedDataPath /private/tmp/ShoppingGuideDerivedData \
  CODE_SIGNING_ALLOWED=NO build
```

Expected: `** BUILD SUCCEEDED **`

本次实现已执行：
- `server/.venv/bin/python -m pytest server/tests`：217 tests passed
- `swift test --scratch-path /private/tmp/ShoppingGuideSwiftPM --cache-path /private/tmp/ShoppingGuideSwiftPMCache`：46 tests passed
- `xcodebuild -project client/ShoppingGuide.xcodeproj -scheme ShoppingGuide -destination "generic/platform=iOS Simulator" -derivedDataPath /private/tmp/ShoppingGuideDerivedData CODE_SIGNING_ALLOWED=NO build`：`** BUILD SUCCEEDED **`
- `plutil -p /private/tmp/ShoppingGuideDerivedData/Build/Products/Debug-iphonesimulator/ShoppingGuide.app/Info.plist`：包含 `NSMicrophoneUsageDescription` 与 `NSSpeechRecognitionUsageDescription`

---

## Task 16: 5C 服务端 ASR/TTS API

**Files:**
- Create: `server/app/audio/__init__.py`
- Create: `server/app/audio/ark_audio_client.py`
- Create: `server/app/api/audio.py`
- Modify: `server/app/config.py`
- Modify: `server/app/main.py`
- Test: `server/tests/test_audio_api.py`

- [x] **Step 16.1: 方舟音频配置**

新增配置项：

```env
ARK_ASR_MODEL=Speech_Recognition_Seed_streaming2000000781793693122
ARK_ASR_MODEL_NAME=bigmodel
ARK_TTS_MODEL=TTS-SeedTTS2.02000000781762207298
ARK_TTS_DEFAULT_VOICE=saturn_zh_female_cancan_tob
# 可选覆盖；默认即官方 OpenSpeech 网关：
# ARK_ASR_ENDPOINT=wss://openspeech.bytedance.com/api/v3/sauc/bigmodel
# ARK_TTS_ENDPOINT=wss://openspeech.bytedance.com/api/v3/tts/bidirection
ARK_ASR_RESOURCE_ID=volc.seedasr.sauc.duration
ARK_TTS_RESOURCE_ID=seed-tts-2.0
```

API key 默认复用 `ARK_EMBEDDING_API_KEY` 或 `ARK_API_KEY`；如后续语音单独 key，可配 `ARK_AUDIO_API_KEY`。
联调时若握手返回 HTTP 401，优先检查 `X-Api-Key` 是否来自豆包语音新版控制台 API Key，以及 `X-Api-Resource-Id` 是否与控制台开通的 ASR/TTS 商品一致。

- [x] **Step 16.2: ArkAudioService**

`ArkAudioService` 封装 OpenSpeech WebSocket：
- 建连：请求头 `X-Api-Key`、`X-Api-Resource-Id`、`X-Api-Request-Id`、`X-Api-Connect-Id`
- ASR：连接 `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel`，发送 gzip JSON full request，再按 200ms 左右分片发送 audio-only 二进制帧，收集 `result.text`
- TTS：连接 `wss://openspeech.bytedance.com/api/v3/tts/bidirection`，发送 StartConnection / StartSession / TaskRequest / FinishSession 事件帧，收集 audio-only PCM，最终包装成 WAV

- [x] **Step 16.3: Audio API**

新增：
- `GET /api/v1/audio/voices`
- `POST /api/v1/audio/asr`：multipart `audio/pcm`，16kHz / 16-bit / mono raw PCM
- `POST /api/v1/audio/tts`：JSON `{text, voice}`，返回 `audio/wav`

音色白名单：

```text
zh_female_vv_uranus_bigtts
saturn_zh_female_cancan_tob
saturn_zh_female_keainvsheng_tob
saturn_zh_female_tiaopigongzhu_tob
saturn_zh_male_shuanglangshaonian_tob
saturn_zh_male_tiancaitongzhuo_tob
zh_female_xiaohe_uranus_bigtts
zh_male_m191_uranus_bigtts
zh_male_taocheng_uranus_bigtts
en_male_tim_uranus_bigtts
```

- [x] **Step 16.4: 后端测试**

`test_audio_api.py` 覆盖 voices、ASR 上传、空音频拒绝、TTS 指定 voice、unknown voice 422。

---

## Task 17: 5C iOS 远端音频重构

**Files:**
- Create: `client/ShoppingGuide/Networking/AudioService.swift`
- Modify: `client/ShoppingGuide/Features/Chat/SpeechRecognitionService.swift`
- Modify: `client/ShoppingGuide/Features/Chat/SpeechSynthesisService.swift`
- Modify: `client/ShoppingGuide/Features/Chat/ChatView.swift`
- Modify: `client/ShoppingGuide/Features/Chat/ChatViewModel.swift`
- Test: `client/Tests/ShoppingGuideKitTests/ChatViewModelTests.swift`

- [x] **Step 17.1: AudioService HTTP 客户端**

新增：
- `transcribe(pcm:) -> String`：multipart 上传 `/api/v1/audio/asr`
- `synthesize(text:voice:) -> Data`：JSON 调 `/api/v1/audio/tts`

- [x] **Step 17.2: ServerSpeechRecognitionService**

iOS 主链路：
- `AVAudioEngine` 采集麦克风
- `AVAudioConverter` 转 16kHz / 16-bit / mono PCM
- 手动停止录音后上传 ASR
- ASR completion 回填 `inputText`
- reset / send 会取消旧 voice turn，避免旧回调污染新会话

降级：远端录音链路不可用时退回 `SpeechRecognitionService`（端侧 `SFSpeechRecognizer`）。若用户没有说话导致 ASR 返回空文本，前端只结束录音状态，不展示"ASR 返回空文本"提示。

- [x] **Step 17.3: ServerSpeechSynthesisService**

iOS 主链路：
- 调 `/api/v1/audio/tts`
- 用 `AVAudioPlayer(data:)` 播放后端返回 WAV
- TTS 失败时停止本次播报，不退回本地 `AVSpeechSynthesizer`

---

## Task 18: 5C 音色选择

**Files:**
- Modify: `client/ShoppingGuide/Features/Chat/SpeechSynthesisService.swift`
- Modify: `client/ShoppingGuide/Features/Chat/ChatView.swift`
- Modify: `client/ShoppingGuide/Features/Chat/ChatViewModel.swift`
- Test: `client/Tests/ShoppingGuideKitTests/ChatViewModelTests.swift`

- [x] **Step 18.1: SpeechVoice 模型**

`SpeechVoice` 定义 voice id、展示名、locale、gender，内置 10 个用户指定音色，默认 `saturn_zh_female_cancan_tob`。

- [x] **Step 18.2: Header 音色菜单**

`ChatView` Header 新增 `waveform.circle` 菜单，用户可选择 TTS 音色；菜单仅展示白名单音色。

- [x] **Step 18.3: selectedVoice 透传**

`ChatViewModel.selectedVoice` 传给 `speechSpeaker.speak(text, voice:)`；测试覆盖 `ttsUsesSelectedVoice()`。

---

## 附录：异常处理快查表

| 现象 | 第一步排查 |
| --- | --- |
| upload 返 422 但图正常打得开 | 检查 `Image.verify()` 是否被消耗：`verify()` 调用后需要重 `Image.open()` 才能后续操作；本期只 verify 不解码，OK |
| upload 返 503 一直触发 | Doubao vision API 限流或 key 错；先 curl 直接打 `client.multimodal_embeddings.create()` 验证 key |
| chat 拿到 image_id 但 retrieve 空 | 检查 `filter_expr` 拼接：用 `logger.info` 打 filter_expr 字符串看是否含 `chunk_type in ["image", "title"]` |
| 同款检索 Top-1 ≠ 自身 | 99% 是 image chunk 没入库；跑 `python -m scripts.build_image_index --rebuild` |
| iOS 气泡缩略图不显示 | `localImageURL` 写入临时目录后是否被系统清理；可改用 `FileManager.default.urls(for: .cachesDirectory, ...)` |
| 麦克风按钮无反应 | 检查 `NSMicrophoneUsageDescription` / `NSSpeechRecognitionUsageDescription` 是否写入生成的 Info.plist |
| Speech 权限已给但无法识别 | 检查设备/模拟器是否支持 `SFSpeechRecognizer(locale: "zh-CN")`，并查看 `voiceNotice` |
| 新建会话后仍在播报 | 检查 `ChatViewModel.resetSession()` 是否调用 `speechSpeaker.stop()` |

---

## 执行说明

按用户偏好"由我手动 commit"，**agent 不应在 task 末尾自动跑 `git commit`**。每个 Task 跑完测试后：

1. 把改动的文件列表清晰报告给用户
2. 等待用户审阅 + 用户人工 commit
3. 用户给出"下一步"指令后再进 Task N+1

各 Task 间相互独立但有依赖顺序：Task 1 → 2 → 3 → 4 → 5 → 6 → 7（server 闭环）→ Task 8 → 9（iOS 图搜闭环）→ Task 10（评测）→ Task 11（文档）→ Task 12 → 13 → 14 → 15（5C 语音/TTS 闭环）。

Task 1-7 完成即可 server smoke；Task 1-9 完成即可 iOS 端 e2e。
Task 12-15 完成即可覆盖 5C 语音输入 / TTS 播报验收。
