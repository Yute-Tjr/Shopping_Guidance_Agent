"""FastAPI 入口。

- Phase 0：/healthz + 根路由 + CORS + lifespan + StaticFiles。
- Phase 2：挂载 /api/v1/chat/stream（SSE）与 /api/v1/products/{id}。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.audio import router as audio_router
from app.api.chat import router as chat_router
from app.api.products import router as products_router
from app.api.upload import router as upload_router
from app.config import settings
from app.db.mysql_session import engine
from app.utils.logger import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 启动时探活一次 MySQL，连不上直接抛错让 uvicorn 不要假装健康。
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("MySQL 探活成功")
    except Exception as exc:  # noqa: BLE001
        logger.warning("MySQL 探活失败：%s。Phase 0 允许先把服务起来，请稍后确认 docker compose up -d。", exc)

    # Phase 4: 把库内品牌列表一次性灌进 QueryRewriter，给 LLM JSON 抽取做白名单。
    try:
        from app.api.deps import get_query_rewriter
        from app.db.product_repo import get_product_repository

        repo = get_product_repository()
        brands = await repo.list_brands()
        rewriter = get_query_rewriter()
        rewriter.set_known_brands(brands)
        logger.info("已加载品牌列表给 QueryRewriter：%d 个", len(brands))
    except Exception as exc:  # noqa: BLE001
        logger.warning("加载品牌列表失败，QueryRewriter 走规则裸跑：%s", exc)

    yield
    await engine.dispose()


app = FastAPI(
    title="Shopping Guide AI Agent",
    description="基于 RAG 的多模态电商智能导购 AI Agent 后端",
    version="0.1.0",
    lifespan=lifespan,
)

# 开发期 CORS 全开，发布前收紧。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()] or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 商品图静态服务：iOS 端拿到 image_path 拼 http://host:port/static/{image_path} 即可。
_dataset_dir = Path(__file__).resolve().parents[2] / "ecommerce_agent_dataset"
if _dataset_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_dataset_dir)), name="static")
else:
    logger.warning("数据集目录不存在：%s（Phase 0 可忽略，Phase 1 灌数据前必须就位）", _dataset_dir)


# Phase 2 路由统一挂在 /api/v1 前缀下（docs/03 §3.1）。
app.include_router(chat_router, prefix="/api/v1")
app.include_router(products_router, prefix="/api/v1")
app.include_router(upload_router, prefix="/api/v1")
app.include_router(audio_router, prefix="/api/v1")


@app.get("/")
async def root() -> dict:
    return {
        "name": "shopping-guide-server",
        "version": "0.1.0",
        "status": "ok",
    }


@app.get("/healthz")
async def healthz() -> dict:
    """健康检查：分别报告应用本身和 MySQL 的状态。"""
    db_ok = True
    db_error: str | None = None
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        db_ok = False
        db_error = str(exc)
    return {
        "app": "ok",
        "db": "ok" if db_ok else "down",
        "db_error": db_error,
    }
