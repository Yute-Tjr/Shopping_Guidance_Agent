"""全局配置：从 server/.env 读取，业务代码统一 from app.config import settings 引用。"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ---- LLM ----
    ark_api_key: str = "ark-placeholder"
    ark_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    ark_model: str = "ep-20260514111645-lmgt2"

    # ---- Embedding ----
    # 留空时回退到 ark_api_key；当 LLM 与 Embedding 不在同一个方舟账号下时，
    # 在 .env 里单独配 ARK_EMBEDDING_API_KEY 以走自己的账号调 Embedding 端点。
    ark_embedding_api_key: str | None = None
    embedding_model: str = "doubao-embedding-text-240715"
    vision_embedding_model: str = "doubao-embedding-vision-241215"
    embedding_dim: int = 2048

    # ---- Storage ----
    milvus_db_path: str = "./data/milvus_lite.db"
    mysql_dsn: str = (
        "mysql+asyncmy://shopping_user:shopping_pwd"
        "@127.0.0.1:3306/shopping_guide?charset=utf8mb4"
    )
    mysql_pool_size: int = 10
    mysql_pool_recycle: int = 1800

    # ---- Server ----
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    cors_origins: str = "*"

    # ---- Optional ----
    redis_url: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
