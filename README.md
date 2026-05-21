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

- [x] **Phase 0**：环境与脚手架（本次完成）
- [ ] Phase 1：数据工程与向量索引
- [ ] Phase 2：后端最小闭环
- [ ] Phase 3：iOS 客户端最小闭环
- [ ] Phase 4：对话能力增强（多轮 / 反选 / 对比）
- [ ] Phase 5：加分项（业务闭环 / 多模态 / 性能）
- [ ] Phase 6：打磨与交付

---

## 强制规范

- 客户端必须 **iOS 原生**（Swift / SwiftUI），禁用 Web / H5 套壳。
- 依赖必须显式锁版本：`server/requirements.txt`、`client/.../Package.swift`。
- `.env` 严禁提交 Git；`API Key` 统一通过 `.env` 注入。
- **严禁幻觉**：Agent 不得编造库内不存在的商品 / 价格 / SKU；所有商品卡片字段必须来自检索结果。
