"""POST /api/v1/upload/image —— 接收用户上传图，落盘 + 同步算 vision embedding 缓存。

二段式 API 设计：
1. 客户端先 POST /upload/image (multipart) → 返回 {image_id, preview_url}
2. 再 POST /chat (JSON) 时把 image_id 带上 → orchestrator 走 multimodal 分支

为什么先 embed 再返回（而不是 lazy 等 /chat 时再算）：
- vision API ~600-1200ms，提前算掉让 /chat 路径上不再阻塞首 token；
- 上传时算失败可以直接返 503，比在 SSE 流里失败更易表达。
"""
from __future__ import annotations

import io
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError

from app.api.deps import get_image_embed_cache, get_retriever, get_upload_dir
from app.rag.image_embed_cache import ImageEmbedCache
from app.rag.retriever import RagRetriever
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/upload", tags=["upload"])

_MAX_BYTES = 1 * 1024 * 1024  # 1 MB
_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
_MIME_TO_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


@router.post("/image")
async def upload_image(
    file: UploadFile = File(...),
    cache: ImageEmbedCache = Depends(get_image_embed_cache),
    upload_dir: Path = Depends(get_upload_dir),
    retriever: RagRetriever = Depends(get_retriever),
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
    embedder = retriever.embedder
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
