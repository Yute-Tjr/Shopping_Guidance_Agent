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

- **事实可信与防幻觉**：LLM 只能引用检索结果中的 `product_id`；流式卡片解析器会过滤未知 ID；商品卡片事实字段统一从 MySQL hydrate，LLM 只生成推荐理由。
- **否定语义与结构化条件**：品牌排除、价格范围、品类等条件由 Query Rewriter 和 Structured Retriever 处理，避免只依赖向量模型理解“不想要某品牌”这类语义。
- **流式体验**：后端用 `sse-starlette` 输出事件，客户端用 `URLSessionDataDelegate` 增量解析，兼容 `\r\n\r\n` 和 `\n\n` SSE 帧分隔。
- **多轮上下文**：进程内会话记忆保存最近对话、最近推荐商品和摘要压缩结果，用于反选、对比和追问。
- **图片找货**：上传图片时先校验 MIME/大小/可解码性，落盘后立即计算 vision embedding 并缓存，后续聊天请求通过 `image_id` 进入多模态召回分支。
- **语音闭环**：客户端只负责录音和播放；ASR/TTS 云服务调用收口到后端 `/api/v1/audio/*`，便于隐藏密钥和统一错误处理。
- **部署静态资源**：本地开发直接挂载 `ecommerce_agent_dataset` 到 `/static`；公网部署需要设置 `STATIC_BASE_URL` 并由 Nginx 暴露商品图片。

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

开发机部署推荐直接用 Uvicorn；沙箱服务器可用仓库根目录的 Compose 文件：

```bash
cp server/.env.example server/.env
# 填写 server/.env 中的真实密钥和模型 endpoint
docker compose -f docker-compose.sandbox.yml up -d --build
curl http://127.0.0.1:8000/healthz
```

沙箱 Compose 会启动 MySQL 和 API，并把 API 绑定到宿主机 `127.0.0.1:8000`。公网访问通常再由 Nginx 反代到本地 8000，并暴露商品静态资源。更细的后端部署说明见 [server/README.md](server/README.md)。

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
