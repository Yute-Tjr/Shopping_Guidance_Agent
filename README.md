# 基于 RAG 的多模态电商智能导购 AI Agent

> 把传统“展示型电商广告”升级为“交互型 AI 导购”：iOS 原生 App + FastAPI 后端 +
> Doubao LLM + Milvus Lite 向量检索，端到端跑通文本 / 图片 / 语音多轮对话推荐。

完整设计请阅读 [`docs/01_项目开发文档.md`](docs/01_项目开发文档.md)；数据 / 后端 / iOS 三向细节分别在 `docs/02`、`docs/03`、`docs/04`。

---

## 仓库结构

```
.
├── docs/                       # 全部 Markdown 文档
├── server/                     # 后端（FastAPI + Python 3.11）
├── client/                     # iOS 客户端（Swift / SwiftUI）
├── docker/                     # 本地基础设施（MySQL 8）
└── ecommerce_agent_dataset/    # 课题给定的 100 条脱敏商品数据
```

---

## 一键启动（开发期 demo）

> 前置：macOS 已安装 Docker Desktop、Python 3.11、Xcode 15+。

### 1. 起本地 MySQL 8

```bash
cd docker/mysql
docker compose up -d
docker exec shopping_mysql mysqladmin ping -h 127.0.0.1 -u root -proot_pwd
```

### 2. 后端环境 + 建表

```bash
cd ./server
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                              
python -m app.db.init_db                          
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

健康检查：

```bash
curl http://127.0.0.1:8000/healthz
# {"app":"ok","db":"ok","db_error":null}
```

### 3. iOS 客户端

```bash
cd ./client
# 用 Xcode 打开 ShoppingGuide.xcodeproj（Phase 0 暂未生成 Xcode 工程文件，
# 请按 client/README.md 在本机新建 SwiftUI 工程后把 ShoppingGuide/ 下源码加入）。
```

---

## 阶段进度

- [x] **Phase 0**：环境与脚手架
- [x] **Phase 1**：数据工程与向量索引 ([详情](#phase-1-数据工程与向量索引)，[评测报告](docs/phase1_eval_report.md))
- [ ] Phase 2：后端最小闭环
- [ ] Phase 3：iOS 客户端最小闭环
- [ ] Phase 4：对话能力增强（多轮 / 反选 / 对比）
- [ ] Phase 5：加分项（业务闭环 / 多模态 / 性能）
- [ ] Phase 6：打磨与交付

---

## Phase 1 数据工程与向量索引

100 件商品 → 1092 条文本 chunk → 2048 维向量索引 → 25 条评测 query **Top-5 召回 100%**。

按设计文档 [`docs/02_数据工程与RAG设计.md`](docs/02_数据工程与RAG设计.md) 落地，三步走：

### (a) Chunker + MySQL 主表灌库

| 文件 | 实现 |
| --- | --- |
| [`server/app/rag/chunker.py`](server/app/rag/chunker.py) | 单商品 JSON → 按 `title / description / faq / review` 四种语义字段切 chunk（不做固定 token 切分，避免 FAQ 被切碎）；每条 chunk 带 `product_id / chunk_type / category / brand / base_price / min_sku_price / max_sku_price / rating / source_id` metadata，价格字段强制 float 便于后续 Milvus 范围过滤 |
| [`server/tests/test_chunker.py`](server/tests/test_chunker.py) | 10 个单测：chunk 类型完整 / 计数与原 JSON 对齐 / source_id 唯一 / review rating 透传 / 全数据集产出落在 [800, 1200] 区间 / 空 rag_knowledge 兜底 |
| [`server/scripts/seed_mysql.py`](server/scripts/seed_mysql.py) | 遍历 `ecommerce_agent_dataset/*/data/*.json`，按 `product_id` 主键 upsert 写入 `products` + `skus` 表；幂等（重复跑数量不变），支持 `--truncate` 强制重灌、`--dataset` 自定义路径 |

**产出实测：**

```
chunker：100 件商品 → 1092 条 chunk
  └─ title=100  description=100  faq=439  review=453
seed_mysql：products=100，skus=585，按品类均分 (美妆/数码/服饰/食品 × 25 件)
chunker 单测：10/10 全过
```

### (b) Embedder + Milvus Lite 向量索引

| 文件 | 实现 |
| --- | --- |
| [`server/app/rag/embedder.py`](server/app/rag/embedder.py) | Doubao Embedding 封装：走 `client.multimodal_embeddings.create()`（控制台已下线纯文本 Embedding，统一改用多模态模型，文本走 `{"type":"text","text":...}` 单输入返回单向量）；ThreadPoolExecutor 8 路并发凑吞吐；tenacity 指数退避重试 429 / 5xx / 超时；写入前强制 L2 归一化；首调用懒探测 dim |
| [`server/app/rag/milvus_store.py`](server/app/rag/milvus_store.py) | `products_text` collection 封装：13 字段 schema（含 metadata 全量）、FLAT 索引 + IP metric（1000 条规模 FLAT 暴搜即可，省去 IVF 训练）；`ensure_collection(rebuild=False)` 幂等建库、`insert_chunks / search / count` 三个最小操作面 |
| [`server/scripts/build_index.py`](server/scripts/build_index.py) | 串起 chunker + embedder + milvus_store 的端到端入口：支持 `--rebuild` 重建、`--limit N` 联调用前 N 件、`--dry-run` 只跑 chunker 不调 API；跑完自动做一次 sanity Top-3 抽查 |
| [`server/app/config.py`](server/app/config.py) | 新增 `ARK_EMBEDDING_API_KEY`（可选，缺省回退到 `ARK_API_KEY`），用于 LLM 与 Embedding 分账户的场景（主办方 key 调不了你自己账号下的 Embedding 端点） |

**产出实测（1092 条全量）：**

```
Embedding：17.4s （8 路并发）
写入 Milvus：1092 条，dim=2048
Sanity search Top-3（用 p_beauty_001 自身向量当 query）:
  1.0000  p_beauty_001  title
  0.8877  p_beauty_001  description
  0.7261  p_beauty_002  title    (兰蔻精华，同类目相邻品牌)
```

**期间踩到的依赖坑（已在 `requirements.txt` 修改配套版本）：**

1. `pymilvus 2.4.9` 依赖 `pkg_resources`（setuptools 81 起被移除）→ 升 `pymilvus==2.5.18`
2. `milvus-lite 3.0` 的 search 服务端 bug（`AttributeError: function_score`）→ 降回 `milvus-lite==2.5.1`
3. 而 `milvus-lite 2.5.1` 又依赖 `pkg_resources` → 钉 `setuptools<81`（临时方案，milvus-lite 迁移 importlib.metadata 后可摘）
4. 控制台已下线纯文本 Embedding 模型，只剩 `doubao-embedding-vision-*` 多模态系列 → Embedder 改用 `multimodal_embeddings.create()` 接口

### (c) Top-K Recall 评测

| 文件 | 实现                                                                                                                           |
| --- |------------------------------------------------------------------------------------------------------------------------------|
| [`server/scripts/eval/queries.json`](server/scripts/eval/queries.json) | 25 条手工黄金集，覆盖四种意图：`category_recommend` × 8 / `price_filter` × 5 / `brand_exclude` × 4 / `scenario` × 8；每条标 1-3 个示例 product_id |
| [`server/scripts/eval_recall.py`](server/scripts/eval_recall.py) | 黄金集逐条 embed → search Top-50 chunk → 按 product_id 去重 → 计算 Top-1/3/5/10 Recall；按意图分组统计；可选 `--output` 输出 Markdown 报告            |

**评测结果：**

| 指标 | Top-1 | Top-3 | Top-5 | Top-10 |
| --- | --- | --- | --- | --- |
| **总体 Recall** | **80.00%** (20/25) | **100%** (25/25) | **100%** (25/25) | **100%** (25/25) |

| 意图 | n | Top-1 | Top-3 | Top-5 |
| --- | --- | --- | --- | --- |
| category_recommend | 8 | 87.5% | 100% | 100% |
| price_filter | 5 | 100% | 100% | 100% |
| scenario | 8 | 100% | 100% | 100% |
| brand_exclude | 4 | **0%** | 100% | 100% |

- **Top-5 100% 达 [docs/02 §9.2](docs/02_数据工程与RAG设计.md#92-指标) 目标（≥ 80%）**
- `brand_exclude` Top-1=0% 印证了 docs/02 §5.4 的设计预判——"向量模型不擅长否定语义"，这块要靠 Phase 2 的 Query Rewriter 把"非 X 品牌"解析成 metadata 过滤，不是 Phase 1 向量召回能解决的
- 全量耗时 3.5s（25 次 embed + 25 次 search）

完整 query-级别明细见 [`docs/phase1_eval_report.md`](docs/phase1_eval_report.md)。

### Phase 1 复跑指令

```bash
cd server
source .venv/bin/activate

# 1. (a) MySQL 主表
python -m app.db.init_db        # 建表（已存在跳过）
python -m scripts.seed_mysql    # 灌 100 件商品

# 2. (b) 向量索引
python -m scripts.build_index --rebuild   # 全量 ~20s

# 3. (c) 评测
python -m scripts.eval_recall --output ../docs/phase1_eval_report.md
```

---

## 强制规范

- 客户端必须 **iOS 原生**（Swift / SwiftUI），禁用 Web / H5 套壳。
- 依赖必须显式锁版本：`server/requirements.txt`、`client/.../Package.swift`。
- `.env` 严禁提交 Git；`API Key` 统一通过 `.env` 注入。
- **严禁幻觉**：Agent 不得编造库内不存在的商品 / 价格 / SKU；所有商品卡片字段必须来自检索结果。
