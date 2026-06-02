"""Phase 5C audio endpoints: ASR upload and TTS synthesis."""
from __future__ import annotations

from functools import lru_cache
from typing import Protocol

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field

from app.audio.ark_audio_client import (
    ASRResult,
    TTSResult,
    TTS_VOICE_IDS,
    TTS_VOICES,
    AudioServiceError,
    build_audio_service_from_settings,
)
from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/audio", tags=["audio"])

_MAX_AUDIO_BYTES = 4 * 1024 * 1024
_ALLOWED_ASR_MIME = {
    "audio/pcm",
    "audio/x-pcm",
    "application/octet-stream",
}


class AudioService(Protocol):
    async def transcribe_pcm(self, audio: bytes, *, sample_rate: int = 16_000) -> ASRResult: ...

    async def synthesize_speech(self, text: str, *, voice: str) -> TTSResult: ...


class VoiceOptionResponse(BaseModel):
    id: str
    name: str
    locale: str
    gender: str


class VoicesResponse(BaseModel):
    voices: list[VoiceOptionResponse]
    default_voice: str


class ASRResponse(BaseModel):
    text: str


class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=1200)
    voice: str | None = None


@lru_cache(maxsize=1)
def get_audio_service() -> AudioService:
    return build_audio_service_from_settings()


@router.get("/voices", response_model=VoicesResponse)
async def list_voices() -> VoicesResponse:
    voice_ids = TTS_VOICE_IDS
    default_voice = settings.ark_tts_default_voice
    if default_voice not in voice_ids:
        default_voice = TTS_VOICES[0].id
    return VoicesResponse(
        voices=[VoiceOptionResponse(**voice.__dict__) for voice in TTS_VOICES],
        default_voice=default_voice,
    )


@router.post("/asr", response_model=ASRResponse)
async def transcribe_audio(
    file: UploadFile = File(...),
    service: AudioService = Depends(get_audio_service),
) -> ASRResponse:
    if file.content_type not in _ALLOWED_ASR_MIME:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"音频格式不支持，仅接受 {sorted(_ALLOWED_ASR_MIME)}",
        )
    body = await file.read()
    if not body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="空音频")
    if len(body) > _MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"音频过大（{len(body)} bytes > {_MAX_AUDIO_BYTES} bytes 上限）",
        )
    try:
        result = await service.transcribe_pcm(body, sample_rate=16_000)
    except AudioServiceError as exc:
        logger.warning("ASR 失败：%s", exc)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return ASRResponse(text=result.text)


@router.post("/tts")
async def synthesize_tts(
    payload: TTSRequest,
    service: AudioService = Depends(get_audio_service),
) -> Response:
    voice = payload.voice or settings.ark_tts_default_voice
    if voice not in TTS_VOICE_IDS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"未知音色：{voice}")
    try:
        result = await service.synthesize_speech(payload.text, voice=voice)
    except AudioServiceError as exc:
        logger.warning("TTS 失败：%s", exc)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return Response(
        content=result.audio,
        media_type=result.media_type,
        headers={"X-Voice-Id": result.voice},
    )
