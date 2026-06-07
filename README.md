# 基于 RAG 的多模态电商智能导购 AI Agent

> PriceCat 是一个端到端 AI 导购 Demo：iOS 原生客户端通过文本、图片、语音与导购 Agent 对话，FastAPI 后端基于 MySQL 商品库、Milvus Lite 向量索引和火山方舟模型完成召回、推理、商品卡片返回与语音播报。

## 文档导航

- 阶段开发总文档：[docs/phase1-6开发流程.md](docs/phase1-6开发流程.md)
- 总体开发文档：[docs/01_项目开发文档.md](docs/01_项目开发文档.md)
- 数据工程与 RAG：[docs/02_数据工程与RAG设计.md](docs/02_数据工程与RAG设计.md)
- 后端 API 与 Agent：[docs/03_后端API与Agent编排.md](docs/03_后端API与Agent编排.md)
- iOS 客户端设计：[docs/04_iOS客户端设计.md](docs/04_iOS客户端设计.md)
- 多模态设计：[docs/05_多模态设计.md](docs/05_多模态设计.md)
- 打磨与交付：[docs/06_打磨交付文档.md](docs/06_打磨交付文档.md)
- 后端启动与部署：[server/README.md](server/README.md)
- 客户端体验与调试：[client/README.md](client/README.md)

---

## 一、设计文档

### 1. 系统架构

```text
iOS App (SwiftUI)
  ├─ ChatView / ChatViewModel
  ├─ ProductCardView / ProductDetailView
  ├─ ImagePicker / UploadService
  └─ SpeechRecognitionService / SpeechSynthesisService
             │
             │ HTTP JSON / SSE / multipart
             ▼
FastAPI Gateway (server/app/api)
  ├─ /api/v1/chat/stream      主对话 SSE
  ├─ /api/v1/products/{id}    商品详情
  ├─ /api/v1/upload/image     拍照找货
  └─ /api/v1/audio/*          ASR / TTS 网关
             │
             ▼
Agent Orchestrator
  ├─ Intent / Query Rewriter / Clarify Detector
  ├─ Compare Planner / Memory Summarizer
  ├─ Multimodal Branch
  └─ Product Card Extractor
             │
    ┌────────┴────────┐
    ▼                 ▼
RAG Retrieval       LLM / Audio
  ├─ Chunker          ├─ Doubao Chat Stream
  ├─ Embedder         ├─ Doubao Embedding / Vision Embedding
  ├─ Milvus Lite      └─ OpenSpeech ASR / TTS
  └─ Structured Filter
    │
    ▼
Storage
  ├─ MySQL 8: products / skus
  ├─ Milvus Lite: text and image vectors
  └─ In-memory conversation state
```

一次完整推荐请求的核心链路是：

1. iOS 发送 `POST /api/v1/chat/stream`，后端以 SSE 返回 `session/status/token/product_card/done` 等事件。
2. Agent 先做意图识别、Query Rewriter 和澄清判断，再进入 RAG 检索或多模态分支。
3. 检索层在 Milvus Lite 召回文本或图片相似商品，并结合 MySQL 元数据做结构化过滤。
4. LLM 只生成解释和对话文本，商品标题、价格、SKU、图片等事实字段全部由 MySQL hydrate。
5. iOS 逐字渲染 token，收到 `product_card` 后插入可点击商品卡片。

### 2. 技术栈

| 层 | 技术 | 说明 |
| --- | --- | --- |
| iOS 客户端 | Swift 5.9、SwiftUI、Combine | 原生 App，最低 iOS 16 |
| 客户端网络 | URLSession、AsyncStream | 自研 SSE 解析和流式消费 |
| 客户端多模态 | PhotosUI、AVFoundation、Speech | 图片上传、录音、TTS 播放、端侧 ASR 兜底 |
| 后端 | Python 3.11、FastAPI、Uvicorn | async API、SSE、multipart 上传 |
| 模型 | 火山方舟 Doubao Chat / Embedding / Vision Embedding | 对话、文本向量、图片向量 |
| 语音 | 火山 OpenSpeech ASR / TTS | 后端统一代理，客户端不直连云服务 |
| 向量库 | Milvus Lite 2.5.1、pymilvus 2.5.18 | 本地嵌入式向量库 |
| 结构化存储 | MySQL 8、SQLAlchemy 2、asyncmy | 商品、SKU 和事实字段来源 |
| 部署 | Docker、Docker Compose、Nginx | `docker-compose.sandbox.yml` 支持服务端沙箱部署 |

### 3. 依赖环境

本地完整运行需要：

- macOS 14+，Xcode 15+，iOS 16+ 模拟器或真机。
- Python 3.11，建议使用 `server/.venv` 虚拟环境。
- Docker Desktop，用于启动 MySQL 8。
- 可访问火山方舟和 OpenSpeech 的网络环境。
- 火山方舟密钥与端点：`ARK_API_KEY`、`ARK_MODEL`、`EMBEDDING_MODEL`、`VISION_EMBEDDING_MODEL`；语音能力可额外配置 `ARK_AUDIO_API_KEY`。

### 4. 目录结构

```text
.
├── README.md                     # 当前总览、设计说明、快速体验
├── docs/
│   ├── phase1-6开发流程.md        # 原根 README 的阶段开发过程
│   ├── 01_项目开发文档.md
│   ├── 02_数据工程与RAG设计.md
│   ├── 03_后端API与Agent编排.md
│   ├── 04_iOS客户端设计.md
│   ├── 05_多模态设计.md
│   ├── 06_打磨交付文档.md
│   └── assets/
├── server/
│   ├── README.md
│   ├── .env.example
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── app/
│   │   ├── api/                  # chat/products/upload/audio 路由
│   │   ├── agent/                # Agent 编排、Prompt、记忆、澄清、对比
│   │   ├── audio/                # OpenSpeech ASR/TTS 客户端
│   │   ├── db/                   # MySQL session、ORM、商品仓库
│   │   ├── llm/                  # Doubao Chat 客户端
│   │   ├── rag/                  # chunk、embedding、Milvus、检索
│   │   └── schemas/
│   ├── scripts/                  # 灌库、建索引、评测、smoke
│   └── tests/
├── client/
│   ├── README.md
│   ├── Package.swift             # macOS 逻辑单测用 SwiftPM 包
│   ├── ShoppingGuide.xcodeproj
│   ├── ShoppingGuide/
│   │   ├── App/
│   │   ├── Components/
│   │   ├── Features/
│   │   ├── Models/
│   │   ├── Networking/
│   │   └── Resources/
│   └── Tests/
├── docker/mysql/                 # 本地 MySQL 8
├── docker-compose.sandbox.yml    # API + MySQL 沙箱部署
└── ecommerce_agent_dataset/      # 100 条脱敏商品数据和图片
```

### 5. 配置说明

后端配置集中在 `server/.env`，从 `server/.env.example` 复制：

```bash
cd server
cp .env.example .env
```

必填或常用配置：

| 变量 | 用途 |
| --- | --- |
| `ARK_API_KEY` | 火山方舟 LLM 调用密钥 |
| `ARK_BASE_URL` | 方舟 API 地址，默认 `https://ark.cn-beijing.volces.com/api/v3` |
| `ARK_MODEL` | Chat 模型 endpoint id |
| `ARK_EMBEDDING_API_KEY` | 可选；Embedding 与 LLM 不同账号时填写 |
| `EMBEDDING_MODEL` | 文本 embedding endpoint id |
| `VISION_EMBEDDING_MODEL` | 图片 embedding endpoint id |
| `MYSQL_DSN` | MySQL 连接串，本地默认指向 `127.0.0.1:3306` |
| `MILVUS_DB_PATH` | Milvus Lite 文件路径，默认 `./data/milvus_lite.db` |
| `STATIC_BASE_URL` | 公网部署时的静态资源域名或 IP |
| `ARK_AUDIO_API_KEY` | 可选；ASR/TTS 独立密钥 |

客户端默认后端地址写在 `client/ShoppingGuide/App/AppEnvironment.swift`：

```swift
@Published var baseURL: URL = URL(string: "http://121.196.247.225")!
```

如果体验本地后端，把它改成：

```swift
@Published var baseURL: URL = URL(string: "http://127.0.0.1:8000")!
```

### 6. 关键问题解决方案

#### 6.1 商品事实可信与防幻觉

电商导购最不能接受的问题是“编造商品、编造价格、编造 SKU”。本项目把 LLM 从“事实字段生成者”降级为“推荐理由生成者”，用多层校验保证商品卡片可信：

- **检索白名单**：RAG 召回后只把候选商品的 `product_id` 放进 Prompt 上下文，System Prompt 明确要求 LLM 只能引用 `<retrieved_products>` 中的商品。
- **卡片协议隔离**：LLM 若要返回商品卡片，必须输出到 ```product_cards 围栏 JSON 中；普通解释文本和结构化卡片分离，客户端不会把 JSON 当自然语言渲染。
- **流式解析过滤**：`server/app/agent/card_extractor.py` 在 token 流中解析卡片 JSON，并用 `allowed_ids` 拦截不在检索结果里的商品 ID。
- **MySQL 二次 hydrate**：标题、品牌、图片、价格区间、SKU 等字段都由 `ProductRepository.get_card_view()` 从 MySQL 读取，LLM 只保留 `reason` 文案。
- **异常兜底**：LLM 超时、限流或输出格式不合法时，`AgentOrchestrator` 可以降级返回检索 Top-N 的真实商品，避免用户看到空白结果。

这套设计的核心收益是：即使模型生成能力波动，最终进入 iOS 商品卡片的事实数据仍来自结构化商品库。

#### 6.2 否定语义、价格区间与结构化筛选

单纯向量检索擅长“语义相似”，但不擅长处理“不要兰蔻”“200 元以下”“只看运动鞋”这类硬约束。项目用 Query Rewriter + Structured Retriever 拆分语义检索和结构化过滤：

- **Query Rewriter**：把用户自然语言解析为 `dense_query` 和结构化条件，例如 `max_price=200`、`exclude_brands=["兰蔻"]`、`category="数码电子"`。
- **品牌白名单**：服务启动时从 MySQL 加载品牌列表注入 Rewriter，减少模型把普通词误判为品牌的概率。
- **Milvus scalar filter**：价格、品类、品牌、评分等字段在 chunk metadata 中保留，检索时通过 filter 表达式先过滤再排序。
- **结构化回退**：当向量召回不稳定时，`StructuredRetriever` 可按 MySQL 元数据补充候选，避免硬条件被语义相似结果冲掉。

这样能把“推荐相关性”和“业务规则正确性”分开处理：向量负责找相似，结构化过滤负责守边界。

#### 6.3 多轮对话、反选与对比

真实导购对话不是单轮问答，用户会追问“不要这个”“再便宜一点”“这两个对比一下”。项目通过会话记忆和摘要压缩支撑多轮任务：

- **匿名会话 ID**：iOS 端用 IDFV 生成 `session_id`，后端按会话维护上下文。
- **最近推荐商品记忆**：`ConversationMemory` 保存最近推荐过的商品 ID，用于解析“这款”“上一个”“不要第二个”等指代。
- **反选能力**：用户表达排除意图时，Agent 会把上一轮商品或品牌加入排除条件，再重新检索。
- **对比规划**：`CompareTargetExtractor` 从用户话术中抽取对比对象，无法明确时结合最近推荐列表补全。
- **摘要压缩**：长对话中用 `MemorySummarizer` 将早期轮次压缩进 summary，避免 Prompt 无限增长。

这让项目从“查一次商品”升级为“能持续协商需求的导购 Agent”。

#### 6.4 图片找货与图文融合检索

图片找货不是简单把图片传给模型描述一下，而是要把图片纳入可复用的检索链路：

- **上传前校验**：`/api/v1/upload/image` 限制 MIME 为 JPEG/PNG/WebP，限制大小为 1 MB，并用 Pillow 校验图片可解码，避免无效文件进入检索链路。
- **提前计算向量**：上传成功时立即调用 vision embedding，结果写入 `ImageEmbedCache`，聊天请求只传 `image_id`，减少 `/chat/stream` 首 token 等待。
- **图文单向量融合**：多模态分支将图片和可选文字需求共同编码为 query vector，再复用 Milvus 搜索。
- **结构化条件复用**：图片检索仍然可以叠加价格、品类、品牌排除等 filter，不另起一套孤立的图搜系统。
- **失败降级**：vision embedding 失败时返回 `fallback_text_only`，客户端可以提示用户继续用文字描述需求。

这种设计保证图片能力是 RAG 主链路的增量增强，而不是和文本推荐割裂的功能。

#### 6.5 语音输入与 TTS 播报闭环

语音能力容易把客户端复杂度和云服务密钥暴露问题带到端侧。项目采用“端侧采集/播放，后端代理云服务”的边界：

- **ASR 网关**：iOS 使用 `AVAudioEngine` 录音并转为 16k / 16-bit / mono PCM，再上传到 `/api/v1/audio/asr`。
- **TTS 网关**：客户端传 `{text, voice}` 到 `/api/v1/audio/tts`，后端返回 WAV，iOS 用 `AVAudioPlayer` 播放。
- **音色列表**：`/api/v1/audio/voices` 由服务端统一返回可选音色和默认音色，客户端不硬编码云服务细节。
- **密钥隔离**：OpenSpeech 的 API Key 只存在 `server/.env`，iOS App 不直接连接第三方语音服务。
- **体验降级**：端侧保留系统 Speech 作为录音链路不可用时的兜底思路，TTS 失败不影响文本推荐主流程。

最终效果是：用户可以用语音输入、听回复播报，但核心 RAG 推荐链路仍保持同一套服务端逻辑。

#### 6.6 SSE 流式体验与客户端稳定性

导购对话需要“边生成边展示”，否则用户会明显感觉等待。项目选用 SSE 而不是普通 JSON 响应：

- **事件类型分离**：后端输出 `session/status/token/product_card/clarify/error/done` 等事件，客户端按事件类型分发，不把所有内容混成一段文本。
- **长连接保活**：`sse-starlette` 设置 15 秒 ping，降低中间代理断开空闲连接的概率。
- **CRLF 兼容**：客户端 `SSEParser` 先归一化 `\r\n`，再按帧分隔解析，避免不同服务端换行格式导致解析失败。
- **生命周期管理**：`StreamingClient` 在 `AsyncStream.onTermination` 中持有并取消 URLSession task，防止流式请求被提前释放。
- **可恢复 UI 状态**：收到 `done` 才结束 loading，收到 `error` 时保留当前对话并展示失败状态。

这保证 iOS 端看到的是稳定的实时对话，而不是等待完整响应后一次性刷新。

#### 6.7 部署、静态资源与公网联调

本项目既要本地可跑，也要能在 ECS/Nginx 沙箱里演示，因此对部署路径做了显式处理：

- **本地静态图**：FastAPI 将 `ecommerce_agent_dataset` 挂载到 `/static`，开发期可以直接访问商品图片。
- **公网静态图**：部署时通过 `STATIC_BASE_URL` 将图片 URL 拼成公网可访问地址，避免 iOS 拿到 `127.0.0.1` 图片链接。
- **Docker 镜像**：`server/Dockerfile` 复制后端和数据集，并创建 `/app/ecommerce_agent_dataset -> /ecommerce_agent_dataset` 软链，保证容器内路径和本地路径兼容。
- **Compose 沙箱**：`docker-compose.sandbox.yml` 编排 MySQL 和 API，API 只绑定宿主机 `127.0.0.1:8000`，公网入口交给 Nginx 控制。
- **密钥安全**：`.dockerignore` 排除 `.env`、虚拟环境、本地数据、测试缓存和构建产物，避免密钥进入镜像上下文。

这让同一套代码可以覆盖开发机调试、服务器沙箱部署和 iOS 公网联调三种场景。

---

## 二、说明文档

### 1. 其他用户如何快速体验

**方式 A：只体验 iOS App，使用已配置公网沙箱**

1. 确认 `client/ShoppingGuide/App/AppEnvironment.swift` 的 `baseURL` 仍是 `http://121.196.247.225`。
2. 打开 Xcode：

   ```bash
   cd client
   open ShoppingGuide.xcodeproj
   ```

3. 选择 iOS 16+ 模拟器或真机，Cmd+R 运行。
4. 在聊天框输入示例问题：
   - `推荐一款适合油皮的洗面奶`
   - `200 元以下的蓝牙耳机`
   - `不要兰蔻，推荐一款精华`
   - `这两款帮我用表格对比一下`

公网沙箱是否可用取决于部署机器和密钥状态；如果请求失败，按方式 B 本地启动后端。

**方式 B：本地完整体验**

```bash
# 1. 启动 MySQL
cd docker/mysql
docker compose up -d
docker exec shopping_mysql mysqladmin ping -h 127.0.0.1 -u root -proot_pwd

# 2. 准备后端
cd ../../server
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

编辑 `server/.env`，至少填好 `ARK_API_KEY`、`ARK_MODEL`、`EMBEDDING_MODEL`、`VISION_EMBEDDING_MODEL`。

```bash
# 3. 初始化 MySQL 商品表和 Milvus 向量索引
python -m app.db.init_db
python -m scripts.seed_mysql
python -m scripts.build_index --rebuild

# 4. 启动 API
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

另开终端验证：

```bash
curl http://127.0.0.1:8000/healthz
bash server/scripts/smoke_chat.sh "推荐一款适合油皮的洗面奶"
```

然后将 `client/ShoppingGuide/App/AppEnvironment.swift` 的 `baseURL` 改为 `http://127.0.0.1:8000`，用 Xcode 运行客户端。

### 2. 服务端部署

开发环境部署推荐直接用 Uvicorn；沙箱服务器可用仓库根目录的 Compose 文件。注意：`up -d --build` 只负责构建并启动容器，首次部署还需要初始化数据库和向量索引。

```bash
cp server/.env.example server/.env
# 填写 server/.env 中的真实密钥、模型 endpoint 和 STATIC_BASE_URL
docker compose -f docker-compose.sandbox.yml up -d --build

# 首次部署或数据集变化后执行：建表、灌库、建立文本索引
docker compose -f docker-compose.sandbox.yml exec api python -m app.db.init_db
docker compose -f docker-compose.sandbox.yml exec api python -m scripts.seed_mysql --truncate
docker compose -f docker-compose.sandbox.yml exec api python -m scripts.build_index --rebuild

# 如需体验图片找货，再建立图片索引；会消耗 vision embedding API 配额
docker compose -f docker-compose.sandbox.yml exec api python -m scripts.build_image_index --rebuild

# 灌库后重启 API，让启动期品牌白名单和本地索引状态重新加载
docker compose -f docker-compose.sandbox.yml restart api
```

验收：

```bash
curl http://127.0.0.1:8000/healthz
BASE_URL=http://127.0.0.1:8000 bash server/scripts/smoke_chat.sh "推荐一款适合油皮的洗面奶"
```

沙箱 Compose 会启动 MySQL 和 API，并把 API 绑定到宿主机 `127.0.0.1:8000`。公网访问通常再由 Nginx 反代到本地 8000，并暴露商品静态资源。只改 `.env` 或 `STATIC_BASE_URL` 时，不需要重新灌库和建索引，执行 `docker compose -f docker-compose.sandbox.yml up -d --force-recreate api` 即可。更细的后端部署说明见 [server/README.md](server/README.md)。

### 3. 产品体验路径

1. 打开 App 后进入开场动画和聊天页。
2. 输入文字问题，观察逐字流式回复和商品卡片。
3. 点击商品卡片进入详情页，查看 SKU、价格和图片。
4. 点图片按钮上传商品或场景图，随后用文字补充需求。
5. 点麦克风录音，ASR 文本回填输入框；收到回复后可点 speaker 播放 TTS。
6. 对已推荐商品追问“不要这个品牌”“这几个对比一下”“更便宜的有没有”，验证多轮上下文。

### 4. 测试与验收

后端：

```bash
cd server
source .venv/bin/activate
pytest
python -m scripts.eval_recall --output ../docs/phase1_eval_report.md
python -m scripts.eval_image_search --output ../docs/phase5_eval_report.md
```

客户端逻辑单测：

```bash
cd client
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test
```

端到端手动验收建议至少覆盖：

- 文本推荐：是否有流式回复和商品卡片。
- 结构化约束：价格、品牌排除是否生效。
- 多商品对比：是否输出表格，并只对比库内商品。
- 图片上传：是否能拿到图片相关推荐。
- 语音：ASR 是否回填，TTS 是否可播放。

---

## 三、项目亮点与创新点

### 1. 核心亮点

- **端到端闭环完整**：不是只做后端接口或单页聊天框，而是完成 iOS 原生 App、FastAPI 服务、MySQL 商品库、Milvus 向量库、LLM、图片检索、语音输入和 TTS 播报的完整链路。
- **RAG 与结构化电商数据结合**：向量检索负责理解自然语言需求，MySQL 负责商品事实字段和筛选条件，解决了纯 RAG Demo 常见的“能回答但不适合交易场景”的问题。
- **强防幻觉设计**：通过 Prompt 约束、卡片协议、ID 白名单、MySQL hydrate、异常兜底五层机制，让推荐结果可验证、可点击、可追溯。
- **多轮导购能力**：支持反选、追问、对比和主动澄清，更接近真实导购沟通，而不是一次性搜索结果页。
- **多模态不是附加玩具**：图片和语音都接入主对话链路，图片复用 RAG 检索和结构化过滤，语音复用文本推荐链路。
- **工程交付完整**：包含本地启动、沙箱部署、评测报告、后端测试、客户端 SwiftPM 逻辑测试和分阶段文档。

### 2. 与同类型项目对比

| 对比维度 | 常见电商搜索 Demo | 常见 RAG 问答 Demo | 常见多模态聊天 Demo | 本项目 |
| --- | --- | --- | --- | --- |
| 交互形态 | 搜索框 + 列表，偏关键词匹配 | 文本问答，通常输出自然语言 | 图片/语音能聊，但结果不一定可交易 | iOS 原生多轮导购，对话中直接返回商品卡片 |
| 商品事实来源 | 数据库或静态 JSON | 文档 chunk，结构化商品字段弱 | 模型描述或临时识别结果 | MySQL hydrate 商品标题、价格、SKU、图片 |
| 推荐可信度 | 规则强但语义理解弱 | 语义强但容易编造事实 | 依赖模型视觉理解，校验弱 | RAG 召回 + ID 白名单 + MySQL 二次校验 |
| 复杂条件处理 | 价格/类目可筛，口语化弱 | 口语化强，硬过滤弱 | 多模态强，业务过滤弱 | Query Rewriter 将口语需求转为 metadata filter |
| 多轮能力 | 通常无上下文 | 有上下文但缺商品指代 | 可聊天但难绑定商品状态 | 记忆最近推荐、支持反选、对比、追问 |
| 多模态深度 | 通常没有 | 通常没有或只做上传附件 | 有图片/语音，但不一定进入商品检索 | 图片向量进入 Milvus，语音进入同一条 RAG 主链路 |
| 客户端形态 | Web 页面较多 | Web 页面或接口测试较多 | Demo UI 较轻 | SwiftUI 原生 App，含流式渲染、卡片、详情、录音、播报 |
| 工程可交付性 | 演示可用，文档和测试不一定完整 | 评测多偏检索指标 | 体验亮眼但后端业务闭环弱 | 启动文档、评测报告、测试、Docker 沙箱部署齐全 |

### 3. 创新点总结

1. **职责拆分**：模型负责理解和解释，数据库负责事实，适合需要可信交易信息的电商场景。
2. **自然语言需求到结构化 filter 的转换**：把“不要某品牌”“预算 200 内”“适合油皮”等口语需求落到可执行检索条件上。
3. **文本、图片、语音统一进入导购主流程**：多模态输入没有另写孤立功能，而是复用同一套 Agent、RAG、商品卡片和客户端渲染协议。
4. **面向答辩和真实演示的工程化闭环**：同时覆盖数据灌库、索引构建、召回评测、API smoke、iOS 单测和服务器部署。
5. **对话式电商体验从“推荐结果”扩展到“需求协商”**：主动澄清、多轮反选、多商品对比让用户可以逐步收敛购买需求。
