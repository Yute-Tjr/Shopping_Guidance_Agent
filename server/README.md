# 后端 · 启动说明

## 环境要求

- Python **3.11**（与 `pyproject.toml` 中 `requires-python = ">=3.11,<3.13"` 对齐）
- MySQL **8.0+**（开发期推荐 `docker/mysql` 一键起容器）
- 可访问火山方舟接口的网络环境（`ARK_BASE_URL`）

## 一键启动

```bash
# 0. 在仓库根目录起 MySQL（一次性，开发期常驻）
cd docker/mysql && docker compose up -d
cd ../../server

# 1. 建虚拟环境 + 装依赖
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. 配置环境变量（ARK_API_KEY 必填）
cp .env.example .env
$EDITOR .env

# 3. 建表（Phase 0 验收点）
python -m app.db.init_db

# 4. 起服务
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

服务起来后：

```bash
curl http://127.0.0.1:8000/
curl http://127.0.0.1:8000/healthz
```

## 目录索引

```
server/
├── app/
│   ├── main.py            # FastAPI 入口（已就位）
│   ├── config.py          # Pydantic Settings（已就位）
│   ├── api/               # 路由层（Phase 2 起补齐）
│   ├── agent/             # Agent 编排（Phase 2 起补齐）
│   ├── rag/               # RAG 检索（Phase 1 起补齐）
│   ├── llm/               # LLM / VLM 客户端（Phase 2 起补齐）
│   ├── db/
│   │   ├── mysql_models.py    # ORM 模型（已就位）
│   │   ├── mysql_session.py   # 异步连接池（已就位）
│   │   └── init_db.py         # 建表脚本（已就位）
│   ├── schemas/           # Pydantic 请求/响应
│   └── utils/             # 日志等共用工具
├── scripts/               # 数据灌库、向量建索、评测脚本
├── tests/                 # pytest 单测
└── data/                  # Milvus Lite 本地文件（已加入 .gitignore）
```

## Phase 0 自测清单

- [x] `uvicorn app.main:app --reload` 能起，访问 `/` 返回 JSON
- [x] `python -m app.db.init_db` 能成功在 MySQL 创建 4 张表
- [x] `pytest tests/test_smoke.py` 通过
- [x] `.env` 已在 `.gitignore` 中
