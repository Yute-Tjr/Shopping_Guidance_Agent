# 后端 README

> 后端提供 PriceCat 的 API、RAG 检索、Agent 编排、多模态上传和语音网关。入口是 `app/main.py`，本地开发默认运行在 `http://127.0.0.1:8000`。

## 1. 后端职责

- `POST /api/v1/chat/stream`：主对话入口，使用 SSE 流式返回文本、状态、商品卡片和结束事件。
- `GET /api/v1/products/{product_id}`：商品详情，供 iOS 商品详情页使用。
- `POST /api/v1/upload/image`：图片上传、校验、落盘和 vision embedding 缓存。
- `GET /api/v1/audio/voices`：返回 TTS 音色列表。
- `POST /api/v1/audio/asr`：上传 16k / 16-bit / mono PCM，返回识别文本。
- `POST /api/v1/audio/tts`：输入文本和音色，返回 WAV 音频。
- `/healthz`：应用和 MySQL 健康检查。

## 2. 技术栈与依赖环境

- Python `>=3.11,<3.13`
- FastAPI `0.115.0`、Uvicorn `0.30.6`
- MySQL 8.0、SQLAlchemy 2、asyncmy
- Milvus Lite `2.5.1`、pymilvus `2.5.18`
- 火山方舟 Chat / Embedding / Vision Embedding
- 火山 OpenSpeech ASR / TTS
- Docker Desktop，本地开发用于启动 MySQL

## 3. 目录结构

```text
server/
├── README.md
├── .env.example
├── requirements.txt
├── pyproject.toml
├── Dockerfile
├── app/
│   ├── main.py              # FastAPI app、CORS、静态资源、路由注册
│   ├── config.py            # Pydantic Settings，从 .env 读取
│   ├── api/                 # chat/products/upload/audio 路由
│   ├── agent/               # Agent 主流程、Prompt、澄清、对比、多轮记忆
│   ├── audio/               # OpenSpeech ASR/TTS 客户端
│   ├── db/                  # MySQL ORM、连接池、商品仓库
│   ├── llm/                 # Doubao Chat 流式客户端
│   ├── rag/                 # chunk、embedding、Milvus、文本/图片检索
│   ├── schemas/             # Pydantic 请求/响应模型
│   └── utils/
├── scripts/
│   ├── seed_mysql.py        # 商品和 SKU 灌入 MySQL
│   ├── build_index.py       # 文本向量索引
│   ├── build_image_index.py # 图片向量索引
│   ├── eval_recall.py       # 文本召回评测
│   ├── eval_image_search.py # 图搜评测
│   ├── smoke_chat.sh
│   └── smoke_image_chat.sh
├── tests/
└── data/                    # Milvus Lite、上传图片等本地运行产物
```

## 4. 环境变量

从模板复制：

```bash
cd server
cp .env.example .env
```

常用配置：

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `ARK_API_KEY` | 是 | 火山方舟 LLM 密钥 |
| `ARK_BASE_URL` | 是 | 默认 `https://ark.cn-beijing.volces.com/api/v3` |
| `ARK_MODEL` | 是 | Chat 模型 endpoint id |
| `ARK_EMBEDDING_API_KEY` | 否 | Embedding 单独账号时填写；为空回退到 `ARK_API_KEY` |
| `EMBEDDING_MODEL` | 是 | 文本 embedding endpoint id |
| `VISION_EMBEDDING_MODEL` | 是 | 图片 embedding endpoint id |
| `MYSQL_DSN` | 是 | MySQL 连接串，本地默认使用 `shopping_user/shopping_pwd` |
| `MILVUS_DB_PATH` | 是 | Milvus Lite 数据文件，默认 `./data/milvus_lite.db` |
| `STATIC_BASE_URL` | 部署时建议 | 商品图片公网根地址；本地为空会回退到 `127.0.0.1` |
| `ARK_AUDIO_API_KEY` | 否 | ASR/TTS 独立密钥；为空复用其他方舟 key |
| `ARK_ASR_ENDPOINT` / `ARK_TTS_ENDPOINT` | 否 | OpenSpeech WebSocket 地址 |
| `ARK_TTS_DEFAULT_VOICE` | 否 | 默认 TTS 音色 |

密钥只能写入 `server/.env`，不要提交到 Git。

## 5. 本地启动

### 5.1 启动 MySQL

在仓库根目录执行：

```bash
cd docker/mysql
docker compose up -d
docker exec shopping_mysql mysqladmin ping -h 127.0.0.1 -u root -proot_pwd
```

预期输出包含 `mysqld is alive`。

### 5.2 创建 Python 环境

```bash
cd ../../server
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`，填入真实模型密钥和 endpoint。

### 5.3 初始化商品库和向量索引

从零运行时必须按顺序执行：

```bash
python -m app.db.init_db
python -m scripts.seed_mysql
python -m scripts.build_index --rebuild
```

说明：

- `init_db` 创建 MySQL 表。
- `seed_mysql` 将 `../ecommerce_agent_dataset` 中的 100 条商品和 SKU 写入 MySQL。
- `build_index --rebuild` 构建 Milvus Lite 文本向量索引。
- 如果要体验图片找货，再运行 `python -m scripts.build_image_index --rebuild`。

### 5.4 启动 API

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

健康检查：

```bash
curl http://127.0.0.1:8000/
curl http://127.0.0.1:8000/healthz
```

## 6. API 自测

文本对话 smoke：

```bash
bash scripts/smoke_chat.sh "推荐一款适合油皮的洗面奶"
```

图片对话 smoke：

```bash
bash scripts/smoke_image_chat.sh
```

语音接口：

```bash
curl -s http://127.0.0.1:8000/api/v1/audio/voices | python3 -m json.tool

curl -s -X POST http://127.0.0.1:8000/api/v1/audio/tts \
  -H 'Content-Type: application/json' \
  -d '{"text":"PriceCat 语音播报测试","voice":"saturn_zh_female_cancan_tob"}' \
  --output /tmp/pricecat_tts.wav
```

ASR 需要准备 16k / 16-bit / mono raw PCM：

```bash
curl -s -F 'file=@/tmp/speech.pcm;type=audio/pcm' \
  http://127.0.0.1:8000/api/v1/audio/asr
```

## 7. 测试与评测

```bash
pytest
python -m scripts.eval_recall --output ../docs/phase1_eval_report.md
python -m scripts.eval_image_search --output ../docs/phase5_eval_report.md
```

建议在改动以下模块后至少跑对应测试：

- `agent/`：`pytest tests/test_orchestrator.py tests/test_prompts.py tests/test_card_extractor.py`
- `rag/`：`pytest tests/test_retriever.py tests/test_structured_retriever.py tests/test_embedder.py`
- `api/`：`pytest tests/test_api_chat.py tests/test_upload_api.py tests/test_audio_api.py`

## 8. Docker 沙箱部署

仓库根目录提供 `docker-compose.sandbox.yml`。`up -d --build` 只会构建镜像并启动 MySQL/API；首次部署必须继续执行建表、灌库和索引构建：

```bash
cp server/.env.example server/.env
# 编辑 server/.env，填真实密钥、模型 endpoint 和 STATIC_BASE_URL
docker compose -f docker-compose.sandbox.yml up -d --build

# 首次部署或数据集变化后执行
docker compose -f docker-compose.sandbox.yml exec api python -m app.db.init_db
docker compose -f docker-compose.sandbox.yml exec api python -m scripts.seed_mysql --truncate
docker compose -f docker-compose.sandbox.yml exec api python -m scripts.build_index --rebuild

# 可选：需要图片找货时再建图片索引，会消耗 vision embedding API 配额
docker compose -f docker-compose.sandbox.yml exec api python -m scripts.build_image_index --rebuild

# 灌库后重启 API，让启动期品牌白名单重新加载
docker compose -f docker-compose.sandbox.yml restart api
```

验收：

```bash
curl http://127.0.0.1:8000/healthz
BASE_URL=http://127.0.0.1:8000 bash server/scripts/smoke_chat.sh "推荐一款适合油皮的洗面奶"
```

注意：

- Compose 中 API 只绑定宿主机 `127.0.0.1:8000`，公网访问通常由 Nginx 反代。
- 部署到公网时设置 `STATIC_BASE_URL=http://<公网 IP 或域名>`，否则 iOS 拿到的商品图地址可能仍指向本机。
- 容器内 `MYSQL_DSN` 会覆盖为 `mysql` 服务名，不能沿用本机 `127.0.0.1`。
- 文本索引会写入宿主机 `server/data/milvus_lite.db`；只改 `.env` 或 `STATIC_BASE_URL` 时不需要重新灌库和建索引，执行 `docker compose -f docker-compose.sandbox.yml up -d --force-recreate api` 即可。

## 9. 关键问题与解决方案

- **Milvus 依赖兼容**：`milvus-lite==2.5.1` 仍依赖 `pkg_resources`，因此 `requirements.txt` 固定 `setuptools<81`。
- **Embedding 接口变化**：文本 embedding 统一通过多模态 embedding SDK 调用，文本以 `{"type":"text","text":...}` 形式传入。
- **防幻觉**：卡片中的商品事实字段不由 LLM 生成，全部通过 `ProductRepository` 从 MySQL 查询。
- **SSE 稳定性**：后端使用 15 秒 ping 保活，客户端需要按事件类型解析，不能把所有 SSE data 当普通文本拼接。
- **图片上传失败降级**：vision embedding 失败时返回 503 和 `fallback_text_only` 信息，客户端可提示用户改用文字描述。
