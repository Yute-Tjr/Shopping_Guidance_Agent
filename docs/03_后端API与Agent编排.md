# 03 · 后端 API 与 Agent 编排

> 本篇覆盖：FastAPI 路由设计、SSE 流式协议、Agent 编排（意图识别 + tool-use）、会话与购物车状态管理。
> RAG 检索的内部细节见 `02_数据工程与RAG设计.md`。

---

## 1. 服务端总览

```
server/app/
├── main.py          # FastAPI app 入口，注册路由 / 中间件 / lifespan
├── config.py        # Pydantic Settings，从 .env 读取
├── api/             # HTTP 路由层（薄层，只做参数校验和响应组装）
├── agent/           # Agent 主流程（业务逻辑）
├── rag/             # RAG 检索（见 02 篇）
├── llm/             # LLM / VLM 客户端封装
├── db/              # Milvus + MySQL (asyncmy) + 内存会话
├── schemas/         # Pydantic 请求/响应模型
└── utils/           # logger / sse / errors
```

**分层原则**：`api` 层薄，`agent` 层是核心，`rag/llm/db` 都被 agent 调用。任何业务逻辑禁止写在 `api/*.py` 里。

---

## 2. 配置与启动

### 2.1 `.env.example`

```ini
# ---- LLM ----
ARK_API_KEY=ark-xxxxxxxx-please-replace
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
ARK_MODEL=ep-20260514111645-lmgt2

# ---- Embedding ----
EMBEDDING_MODEL=doubao-embedding-text-240715
VISION_EMBEDDING_MODEL=doubao-embedding-vision-241215
EMBEDDING_DIM=2048

# ---- Storage ----
MILVUS_DB_PATH=./data/milvus_lite.db

# MySQL：注意 charset=utf8mb4 必带；driver 用 asyncmy
# 格式： mysql+asyncmy://<user>:<password>@<host>:<port>/<dbname>?charset=utf8mb4
MYSQL_DSN=mysql+asyncmy://shopping_user:shopping_pwd@127.0.0.1:3306/shopping_guide?charset=utf8mb4
MYSQL_POOL_SIZE=10
MYSQL_POOL_RECYCLE=1800

# ---- Server ----
HOST=0.0.0.0
PORT=8000
LOG_LEVEL=INFO
CORS_ORIGINS=*

# ---- Optional ----
REDIS_URL=                # 留空则用内存兜底
```

### 2.2 `config.py`

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    ark_api_key: str
    ark_base_url: str
    ark_model: str
    embedding_model: str
    vision_embedding_model: str
    embedding_dim: int = 2048
    milvus_db_path: str = "./data/milvus_lite.db"
    mysql_dsn: str
    mysql_pool_size: int = 10
    mysql_pool_recycle: int = 1800
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    cors_origins: str = "*"
    redis_url: str | None = None

    class Config:
        env_file = ".env"

settings = Settings()
```

### 2.3 启动命令

```bash
# 0. 启动 MySQL 8 (一次性，开发期一直跑)
cd docker/mysql && docker compose up -d
docker exec shopping_mysql mysqladmin ping -h 127.0.0.1 -u root -proot_pwd     # 期待 "mysqld is alive"

# 1. 后端环境
cd ../../server
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # 填入真实 ARK_API_KEY 与 MYSQL_DSN

# 2. 初始化数据：先建表，再灌商品主表，最后建向量索引
python -m app.db.init_db                  # 创建 products / skus / cart_items / orders
python scripts/seed_mysql.py              # 把 100 条商品 + SKU 灌入 MySQL
python scripts/build_index.py             # 建立 Milvus 向量索引

# 3. 起服务
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

## 3. API 设计（外部接口契约）

> 所有路由统一 `/api/v1/` 前缀；请求/响应均 `application/json`，除非显式说明 `multipart/form-data` 或 SSE。

### 3.1 路由总览

| 方法 | 路径 | 用途 | 流式? |
| --- | --- | --- | --- |
| POST | `/api/v1/chat/stream` | 主对话入口，SSE 流式返回 | ✅ SSE |
| POST | `/api/v1/chat/sessions` | 新建会话，返回 session_id | ❌ |
| GET | `/api/v1/chat/sessions/{session_id}/messages` | 拉取历史消息（断线重连/进入页面用） | ❌ |
| GET | `/api/v1/products/{product_id}` | 商品详情（详情页用） | ❌ |
| GET | `/api/v1/products` | 商品列表（开发期调试用） | ❌ |
| POST | `/api/v1/upload/image` | 上传图片（拍照找货） | ❌ |
| GET | `/api/v1/cart` | 查询购物车 | ❌ |
| POST | `/api/v1/cart` | 直接加购（兜底，正常路径走对话） | ❌ |
| DELETE | `/api/v1/cart/{cart_item_id}` | 删除购物车条目 | ❌ |
| POST | `/api/v1/orders` | 模拟下单 | ❌ |
| GET | `/healthz` | 健康检查 | ❌ |

### 3.2 `POST /api/v1/chat/stream` 详细契约

**请求**
```json
{
  "session_id": "uuid-or-null",       // null 表示新会话，后端生成并在事件流中返回
  "message": "推荐一款适合油皮的洗面奶",
  "image_id": null,                    // 可选；走拍照找货时填 upload/image 返回的 id
  "user_id": "device-uuid"             // iOS 端用 IDFV，匿名也行
}
```

**响应（SSE 流）** 事件类型清单：

```
event: session
data: {"session_id":"..."}

event: status
data: {"stage":"retrieving"}        # retrieving / generating / done

event: clarify                       # 仅意图为 clarify_needed 时
data: {
  "question":"请问您更看重控油、保湿还是性价比？",
  "options":["控油优先","保湿优先","性价比优先"]
}

event: token
data: {"text":"为你"}                 # 逐 token，前端用 += 追加

event: product_card
data: {
  "product_id": "p_beauty_001",
  "title": "...",
  "brand": "...",
  "category": "...",
  "image_url": "https://.../p_beauty_001_live.jpg",
  "price_range": {"min": 720.0, "max": 1260.0},
  "skus": [...],
  "reason": "30 字内推荐理由"
}

event: tool_result                  # tool-use 的结果（如加购成功）
data: {"tool":"add_to_cart","ok":true,"cart_count":3}

event: cart_update                  # 购物车有变化时主动推送
data: {"items":[...], "total_count":3, "total_price":1499.0}

event: error
data: {"code":"LLM_TIMEOUT","message":"模型超时，请重试"}

event: done
data: {"finish_reason":"stop","tokens":{"prompt":820,"completion":156}}
```

**约定**：
- iOS 端按 `event:` 分发到不同的 ViewModel 方法，**不要全部当文本拼接**。
- 一次完整对话最后必出 `event: done`，客户端用它做"流式动画结束"的信号。
- 心跳：超过 15s 无事件时后端发 `: keepalive\n\n`（SSE 注释行）保活。

### 3.3 `POST /api/v1/upload/image`

`multipart/form-data`：字段 `file` 是图片，最大 5 MB。

**响应**
```json
{
  "image_id": "img_2026052101abc",
  "expires_at": 1716240000          // 10 分钟后过期
}
```

后端把图存到内存 LRU（够 demo 用），不落磁盘。

---

## 4. SSE 流式实现

### 4.1 库选型：`sse-starlette`

```python
from sse_starlette.sse import EventSourceResponse

@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    async def event_generator():
        async for evt in agent.orchestrate(req):
            # evt 是 dict，含 "event" 和 "data"
            yield evt
    return EventSourceResponse(event_generator(), ping=15)
```

### 4.2 Agent 端事件生成（关键代码骨架）

```python
# server/app/agent/orchestrator.py
async def orchestrate(self, req: ChatRequest) -> AsyncIterator[dict]:
    session = await self.memory.get_or_create(req.session_id)
    yield {"event": "session", "data": {"session_id": session.id}}

    # 1. 意图 + filter 抽取
    yield {"event": "status", "data": {"stage": "parsing"}}
    intent = await self.intent_router.parse(req.message, history=session.history)

    if intent.intent == "clarify_needed":
        yield {"event": "clarify", "data": intent.clarify_payload}
        return

    if intent.intent == "cart_op":
        result = await self.tools.execute(intent.tool_call, session)
        yield {"event": "tool_result", "data": result.dict()}
        yield {"event": "cart_update", "data": await self.cart_repo.snapshot(session.id)}
        # 仍然让 LLM 生成一句确认话术
        async for tok in self.llm.stream_confirmation(result):
            yield {"event": "token", "data": {"text": tok}}
        yield {"event": "done", "data": {"finish_reason": "stop"}}
        return

    # 2. 检索（推荐 / 对比走这里）
    yield {"event": "status", "data": {"stage": "retrieving"}}
    retrieved = await self.retriever.search(intent.search_query, filters=intent.filters)

    # 3. LLM 流式生成
    yield {"event": "status", "data": {"stage": "generating"}}
    messages = self.prompt_builder.build(req.message, retrieved, session.history)

    card_extractor = ProductCardExtractor(allowed_ids={p.product_id for p in retrieved})
    async for chunk in self.llm.chat_stream(messages):
        # chunk.text 是增量 token
        visible_text, cards = card_extractor.feed(chunk.text)
        if visible_text:
            yield {"event": "token", "data": {"text": visible_text}}
        for card in cards:
            yield {"event": "product_card", "data": card}

    # 4. 收尾
    await session.save_turn(req.message, card_extractor.final_text(), card_extractor.cards)
    yield {"event": "done", "data": {"finish_reason": "stop"}}
```

### 4.3 `ProductCardExtractor` 的职责

- 在流式 token 中**实时找** `\`\`\`product_cards` 围栏。
- 围栏外的文本直接 yield 给用户。
- 围栏内累积 JSON，闭合后立即解析、用 `allowed_ids` 校验 → 产生 `product_card` 事件。
- 围栏整段不发给用户（避免看到原始 JSON）。

---

## 5. Agent 编排细节

### 5.1 意图路由（`agent/intent.py`）

走一次 Doubao-lite 小调用（JSON 强约束）输出结构化字段：

```json
{
  "intent": "recommend | compare | cart_op | clarify_needed | chitchat",
  "search_query": "...",
  "filters": { ... },
  "tool_call": { "name": "add_to_cart", "args": {"product_id":"p_beauty_001","sku_id":"s_p_beauty_001_2","quantity":1} },
  "clarify_payload": { "question": "...", "options": ["...","..."] }
}
```

> 注：可在 100 条数据 Demo 阶段先用**规则前置**——纯关键词匹配能识别"加入购物车""删除""下单""对比"等，省一次 LLM 调用。规则识别不到再 fallback 走 LLM。

### 5.2 Prompt 模板（`agent/prompts.py`）

集中管理三套 Prompt：

1. `INTENT_PARSER_PROMPT` —— 强 JSON 输出，温度 0
2. `RECOMMEND_PROMPT` —— 含 `<retrieved_products>` 占位，温度 0.3
3. `COMPARE_PROMPT` —— 强制输出表格 + 推荐总结，温度 0.2

所有 Prompt 在文件顶部写明用途和变更原因（注释规范要求）。

### 5.3 Tool-Use（购物车 / 下单）

利用 Doubao 的 OpenAI 兼容 `tools` 参数：

```python
TOOLS = [
  {
    "type": "function",
    "function": {
      "name": "add_to_cart",
      "description": "把指定 SKU 加入用户购物车",
      "parameters": {
        "type": "object",
        "properties": {
          "product_id": {"type":"string"},
          "sku_id": {"type":"string"},
          "quantity": {"type":"integer","minimum":1,"default":1}
        },
        "required": ["product_id","sku_id"]
      }
    }
  },
  # remove_from_cart / update_quantity / place_order ...
]
```

Agent 流程：
1. 把 tool 定义 + 当前购物车快照 + 用户消息塞给 LLM
2. LLM 返回 `tool_calls` → 后端**实际执行**（用 AsyncSession 写 MySQL，事务包裹）
3. 把执行结果作为 `role=tool` 的 message 塞回去 → LLM 生成确认话术 → SSE 流出

> 重要：tool 参数中的 `product_id / sku_id` 必须用 MySQL 校验存在；否则 Agent 必须改口说"没找到这个商品"。

### 5.4 会话记忆（`agent/memory.py`）

```
class ConversationMemory:
    sessions: dict[str, Session]   # 进程内即可，demo 不持久化

    class Session:
        id: str
        history: list[Message]      # 用户/Agent 交替
        last_recommended_ids: list[str]   # "把这个加到购物车" 中的"这个"指代用

    # 当 history > 6 轮：截断保留最近 4 轮 + LLM 摘要前 N 轮
```

**指代消解**：用户说"加到购物车"时，Agent 优先用 `last_recommended_ids[-1]`，并主动反问"是指刚才推荐的雅诗兰黛吗？"做二次确认（加分项）。

---

## 6. 存储层

### 6.1 MySQL 表设计（SQLAlchemy 2.0 异步 ORM）

**总原则**：所有表 `ENGINE=InnoDB CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`；金额一律 `DECIMAL(10,2)` 不要用 FLOAT（防止精度漂移导致幻觉）。

```python
# server/app/db/mysql_models.py
from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from sqlalchemy import String, DECIMAL, Integer, ForeignKey, DateTime, Text, JSON, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """所有 ORM 模型的基类，统一字符集和引擎在 __table_args__ 里指定。"""


COMMON_TABLE_ARGS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


class Product(Base):
    __tablename__ = "products"
    __table_args__ = COMMON_TABLE_ARGS

    product_id:   Mapped[str]      = mapped_column(String(64), primary_key=True)
    title:        Mapped[str]      = mapped_column(String(255), index=True)
    brand:        Mapped[str]      = mapped_column(String(64), index=True)
    category:     Mapped[str]      = mapped_column(String(32), index=True)
    sub_category: Mapped[str]      = mapped_column(String(32), index=True)
    base_price:   Mapped[Decimal]  = mapped_column(DECIMAL(10, 2))
    image_path:   Mapped[str]      = mapped_column(String(255))
    raw_json:     Mapped[str]      = mapped_column(Text)   # 完整原始 JSON，详情页用
    created_at:   Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    skus: Mapped[list["SKU"]] = relationship(back_populates="product", cascade="all, delete")


class SKU(Base):
    __tablename__ = "skus"
    __table_args__ = COMMON_TABLE_ARGS

    sku_id:     Mapped[str]     = mapped_column(String(64), primary_key=True)
    product_id: Mapped[str]     = mapped_column(String(64), ForeignKey("products.product_id", ondelete="CASCADE"), index=True)
    properties: Mapped[dict]    = mapped_column(JSON)             # {"容量":"30ml 经典装"}
    price:      Mapped[Decimal] = mapped_column(DECIMAL(10, 2))

    product: Mapped["Product"] = relationship(back_populates="skus")


class CartItem(Base):
    __tablename__ = "cart_items"
    __table_args__ = COMMON_TABLE_ARGS

    id:         Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str]      = mapped_column(String(64), index=True)
    product_id: Mapped[str]      = mapped_column(String(64), ForeignKey("products.product_id"))
    sku_id:     Mapped[str]      = mapped_column(String(64), ForeignKey("skus.sku_id"))
    quantity:   Mapped[int]      = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = COMMON_TABLE_ARGS

    order_id:    Mapped[str]      = mapped_column(String(64), primary_key=True)
    session_id:  Mapped[str]      = mapped_column(String(64), index=True)
    items_json:  Mapped[dict]     = mapped_column(JSON)             # 下单时商品快照
    address:     Mapped[str]      = mapped_column(String(255))
    total_price: Mapped[Decimal]  = mapped_column(DECIMAL(10, 2))
    status:      Mapped[str]      = mapped_column(String(16), default="created")   # created / paid (模拟)
    created_at:  Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
```

**连接池封装**（`server/app/db/mysql_session.py`）：
```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.config import settings

engine = create_async_engine(
    settings.mysql_dsn,
    pool_size=settings.mysql_pool_size,
    pool_recycle=settings.mysql_pool_recycle,
    pool_pre_ping=True,         # 防止 MySQL wait_timeout 断连
    echo=False,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def get_session() -> AsyncSession:
    """FastAPI 依赖注入：async with get_session() as s: ..."""
    async with AsyncSessionLocal() as session:
        yield session
```

**为什么金额用 DECIMAL 不用 FLOAT**：商品价格 `720.0`、`1260.0` 等用 FLOAT 在序列化往返时可能变成 `720.0000001`，被用户截图发到群里就成了"AI 编造价格"的事故。`DECIMAL(10,2)` 精确，且写卡片时直接 `str(decimal)` 输出无误差。

### 6.2 图片服务

商品图直接走 FastAPI 的 `StaticFiles`：
```python
app.mount("/static", StaticFiles(directory="../ecommerce_agent_dataset"), name="static")
```
iOS 端拿到 `image_path = "1_美妆护肤/images/p_beauty_001_live.jpg"`，拼 `http://host:port/static/{image_path}` 即可。

---

## 7. 错误处理与降级

| 异常 | 触发条件 | 后端行为 | SSE 行为 |
| --- | --- | --- | --- |
| LLM 限流 (429) | 方舟 RPM 触顶 | 退避 1s 重试 1 次；仍失败 → 走兜底 | 推 `error` + 兜底卡片（Top-3 检索结果） |
| LLM 超时 (>20s) | `httpx.ReadTimeout` | 立即中断 | `error: {"code":"LLM_TIMEOUT"}` + `done` |
| Embedding 失败 | 网络错误 | fallback 到本地 BM25 | 正常返回 |
| Milvus 异常 | 索引未加载等 | 返回 500 | `error: {"code":"INDEX_DOWN"}` |
| MySQL 不可达 | 容器宕 / 连接耗尽 | `pool_pre_ping` 检测后立即抛 → 跳过卡片校验，只回纯文本 | `error: {"code":"DB_DOWN"}`（仅在加购/下单流程致命） |
| MySQL 死锁 / 超时 | 并发购物车操作 | tenacity 重试 2 次 | 仍失败推 `error` |
| product_id 不存在 | LLM 编造 | 卡片丢弃 + 日志告警 | 用户感知不到 |

**全局中间件**：
- `LoggingMiddleware`：每个请求打 trace_id
- `CORSMiddleware`：开发期 `*`，发布前收紧
- `GZipMiddleware`：对非 SSE 接口压缩

**MySQL 连接健康**：
- 启动时 `lifespan`: `engine.connect()` 探活，失败则后端拒绝启动并打印 DSN 提示
- `pool_pre_ping=True` 让连接在借出前先 `SELECT 1`，自动剔除被 MySQL `wait_timeout` 关掉的死连接
- 连接池上限 = `MYSQL_POOL_SIZE`，默认 10，对 100 条数据的 demo 远超需要

---

## 8. 性能与并发

### 8.1 并行化

```python
# orchestrator 内部：embedding 和 MySQL product 主表加载并行
emb_task = asyncio.create_task(embedder.embed_async(query))
recent_history_task = asyncio.create_task(memory.load_recent(session_id))
query_vec, history = await asyncio.gather(emb_task, recent_history_task)
```

### 8.2 首 token 优化

- 系统 Prompt 长度控制在 800 token 以内
- 检索 Top-K 控制在 5 件、每件最多 2 条 chunk → 上下文不超过 2500 token
- LLM stream 收到第一个 token 立刻 yield，**不要做完整 JSON 解析再发**

### 8.3 缓存（加分项 5D）

```
cache_key = sha256(f"{query}|{filters_json}|{history_summary}")
TTL = 600s
```
命中时直接重放：用 0.05s 间隔模拟流式发送之前的回复，保证用户体验一致。

---

## 9. 测试

### 9.1 单元测试（pytest）

- `tests/test_chunker.py`：每个 chunk_type 数量正确、metadata 完整
- `tests/test_retriever.py`：mock embedding，验证 filter expr 生成
- `tests/test_card_extractor.py`：各种残缺 JSON 输入下不崩溃、能识别合法卡片
- `tests/test_intent_parser.py`：用 fixture 跑 20 条 query，命中预期 intent

### 9.2 集成测试（手动 + curl 脚本）

`scripts/smoke_chat.sh`：
```bash
curl -N -X POST http://localhost:8000/api/v1/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"session_id":null,"message":"200元以下的蓝牙耳机"}' | head -50
```

预期能看到 token 流 + 至少 1 个 product_card 事件、且 product_id 真实存在。

---

## 10. 与 iOS 客户端的契约纪律

1. **字段命名**：所有 JSON 字段统一 `snake_case`，iOS 端用 `JSONDecoder.keyDecodingStrategy = .convertFromSnakeCase`。
2. **时间戳**：统一 Unix 秒（int）。
3. **图片 URL**：后端返回**完整 URL**（含 host），iOS 不再拼接。开发期可用相对路径但**必须在响应中带 base_url 字段**。
4. **错误码**：所有 `event: error` 必带 `code`，iOS 按 code 决定提示文案。
5. **break change**：任何 API 变更须先更新本文档第 3 章，再改代码。

---

下一篇 `04_iOS客户端设计.md` 讲怎么在 Swift 端把这套 SSE + 商品卡片 + 多模态消费起来。
