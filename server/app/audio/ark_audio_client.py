"""Volc OpenSpeech audio client for Phase 5C ASR/TTS.

The iOS app talks to simple HTTP endpoints. This module owns the provider
protocol used by Doubao Speech:
- TTS: WebSocket V3 bidirectional endpoint.
- ASR: WebSocket SAUC bigmodel binary endpoint.
"""
from __future__ import annotations

import asyncio
import gzip
import io
import json
import struct
import uuid
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

import websockets
from websockets.exceptions import InvalidStatus, WebSocketException

from app.config import settings


@dataclass(frozen=True)
class VoiceOption:
    id: str
    name: str
    locale: str
    gender: str


TTS_VOICES: tuple[VoiceOption, ...] = (
    VoiceOption("zh_female_vv_uranus_bigtts", "VV 女声", "zh-CN", "female"),
    VoiceOption("saturn_zh_female_cancan_tob", "灿灿", "zh-CN", "female"),
    VoiceOption("saturn_zh_female_keainvsheng_tob", "可爱女声", "zh-CN", "female"),
    VoiceOption("saturn_zh_female_tiaopigongzhu_tob", "调皮公主", "zh-CN", "female"),
    VoiceOption("saturn_zh_male_shuanglangshaonian_tob", "爽朗少年", "zh-CN", "male"),
    VoiceOption("saturn_zh_male_tiancaitongzhuo_tob", "天才同桌", "zh-CN", "male"),
    VoiceOption("zh_female_xiaohe_uranus_bigtts", "小荷", "zh-CN", "female"),
    VoiceOption("zh_male_m191_uranus_bigtts", "M191 男声", "zh-CN", "male"),
    VoiceOption("zh_male_taocheng_uranus_bigtts", "陶成", "zh-CN", "male"),
    VoiceOption("en_male_tim_uranus_bigtts", "Tim", "en-US", "male"),
)
TTS_VOICE_IDS = {voice.id for voice in TTS_VOICES}


@dataclass(frozen=True)
class ASRResult:
    text: str


@dataclass(frozen=True)
class TTSResult:
    audio: bytes
    media_type: str
    voice: str


class AudioServiceError(RuntimeError):
    """Raised when the remote ASR/TTS provider fails or returns malformed data."""


class OpenSpeechMessageType(IntEnum):
    FULL_CLIENT_REQUEST = 0b0001
    AUDIO_ONLY_CLIENT = 0b0010
    FULL_SERVER_RESPONSE = 0b1001
    AUDIO_ONLY_SERVER = 0b1011
    ERROR = 0b1111


class OpenSpeechFlag(IntEnum):
    NO_SEQUENCE = 0b0000
    POSITIVE_SEQUENCE = 0b0001
    LAST_NO_SEQUENCE = 0b0010
    NEGATIVE_SEQUENCE = 0b0011
    WITH_EVENT = 0b0100


class OpenSpeechSerialization(IntEnum):
    RAW = 0
    JSON = 0b0001


class OpenSpeechCompression(IntEnum):
    NONE = 0
    GZIP = 0b0001


class OpenSpeechEvent(IntEnum):
    NONE = 0
    START_CONNECTION = 1
    FINISH_CONNECTION = 2
    CONNECTION_STARTED = 50
    CONNECTION_FAILED = 51
    CONNECTION_FINISHED = 52
    START_SESSION = 100
    CANCEL_SESSION = 101
    FINISH_SESSION = 102
    SESSION_STARTED = 150
    SESSION_CANCELED = 151
    SESSION_FINISHED = 152
    SESSION_FAILED = 153
    TASK_REQUEST = 200
    UPDATE_CONFIG = 201
    TTS_SENTENCE_START = 350
    TTS_SENTENCE_END = 351
    TTS_RESPONSE = 352
    TTS_ENDED = 359
    ASR_INFO = 450
    ASR_RESPONSE = 451
    ASR_ENDED = 459


_CONNECTION_EVENTS = {
    OpenSpeechEvent.START_CONNECTION,
    OpenSpeechEvent.FINISH_CONNECTION,
    OpenSpeechEvent.CONNECTION_STARTED,
    OpenSpeechEvent.CONNECTION_FAILED,
    OpenSpeechEvent.CONNECTION_FINISHED,
}


@dataclass
class OpenSpeechMessage:
    message_type: OpenSpeechMessageType
    flag: OpenSpeechFlag = OpenSpeechFlag.NO_SEQUENCE
    serialization: OpenSpeechSerialization = OpenSpeechSerialization.JSON
    compression: OpenSpeechCompression = OpenSpeechCompression.NONE
    event: OpenSpeechEvent = OpenSpeechEvent.NONE
    session_id: str = ""
    connect_id: str = ""
    sequence: int = 0
    error_code: int = 0
    payload: bytes = b""

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        buf.write(
            bytes(
                [
                    (1 << 4) | 1,
                    (int(self.message_type) << 4) | int(self.flag),
                    (int(self.serialization) << 4) | int(self.compression),
                    0,
                ]
            )
        )
        if self.flag == OpenSpeechFlag.WITH_EVENT:
            buf.write(struct.pack(">i", int(self.event)))
            if self.event not in _CONNECTION_EVENTS:
                sid = self.session_id.encode("utf-8")
                buf.write(struct.pack(">I", len(sid)))
                buf.write(sid)
        elif self.flag in (OpenSpeechFlag.POSITIVE_SEQUENCE, OpenSpeechFlag.NEGATIVE_SEQUENCE):
            buf.write(struct.pack(">i", self.sequence))

        if self.message_type == OpenSpeechMessageType.ERROR:
            buf.write(struct.pack(">I", self.error_code & 0xFFFFFFFF))

        buf.write(struct.pack(">I", len(self.payload)))
        buf.write(self.payload)
        return buf.getvalue()

    @classmethod
    def from_bytes(cls, data: bytes) -> OpenSpeechMessage:
        if len(data) < 4:
            raise AudioServiceError(f"音频服务返回帧过短：{len(data)} bytes")
        version = data[0] >> 4
        header_size = (data[0] & 0x0F) * 4
        if version != 1 or header_size < 4:
            raise AudioServiceError(f"音频服务返回非法协议头：version={version}, header_size={header_size}")

        msg = cls(
            message_type=OpenSpeechMessageType(data[1] >> 4),
            flag=OpenSpeechFlag(data[1] & 0x0F),
            serialization=OpenSpeechSerialization(data[2] >> 4),
            compression=OpenSpeechCompression(data[2] & 0x0F),
        )
        buf = io.BytesIO(data[header_size:])

        if msg.flag == OpenSpeechFlag.WITH_EVENT:
            msg.event = OpenSpeechEvent(struct.unpack(">i", _read_exact(buf, 4))[0])
            if msg.event not in _CONNECTION_EVENTS:
                sid_size = struct.unpack(">I", _read_exact(buf, 4))[0]
                if sid_size:
                    msg.session_id = _read_exact(buf, sid_size).decode("utf-8", errors="replace")
            elif msg.message_type == OpenSpeechMessageType.FULL_SERVER_RESPONSE:
                cid_size_bytes = buf.read(4)
                if cid_size_bytes:
                    cid_size = struct.unpack(">I", cid_size_bytes)[0]
                    if cid_size:
                        msg.connect_id = _read_exact(buf, cid_size).decode("utf-8", errors="replace")
        elif msg.flag in (OpenSpeechFlag.POSITIVE_SEQUENCE, OpenSpeechFlag.NEGATIVE_SEQUENCE):
            msg.sequence = struct.unpack(">i", _read_exact(buf, 4))[0]

        if msg.message_type == OpenSpeechMessageType.ERROR:
            msg.error_code = struct.unpack(">I", _read_exact(buf, 4))[0]

        size_bytes = buf.read(4)
        if size_bytes:
            size = struct.unpack(">I", size_bytes)[0]
            if size:
                msg.payload = _read_exact(buf, size)
        return msg

    def json_payload(self) -> dict[str, Any] | None:
        if not self.payload:
            return None
        raw = self.payload
        if self.compression == OpenSpeechCompression.GZIP:
            raw = gzip.decompress(raw)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None


class ArkAudioService:
    """Thin wrapper around Volc OpenSpeech ASR/TTS APIs."""

    def __init__(
        self,
        *,
        api_key: str,
        asr_model: str = "bigmodel",
        tts_model: str = "seed-tts-2.0",
        tts_endpoint: str | None = None,
        asr_endpoint: str | None = None,
        realtime_base_url: str | None = None,  # kept for backwards-compatible tests/callers
        asr_resource_id: str | None = None,
        tts_resource_id: str | None = None,
        asr_model_name: str | None = None,
        tts_sample_rate: int = 24_000,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.asr_model = asr_model
        self.tts_model = tts_model
        self.asr_model_name = asr_model_name or _normalize_asr_model_name(asr_model)
        self.tts_endpoint = tts_endpoint or _default_tts_endpoint()
        self.asr_endpoint = asr_endpoint or _default_asr_endpoint()
        self.realtime_base_url = realtime_base_url
        self.asr_resource_id = asr_resource_id or "volc.seedasr.sauc.duration"
        self.tts_resource_id = tts_resource_id or "seed-tts-2.0"
        self.tts_sample_rate = tts_sample_rate
        self.timeout = timeout

    async def transcribe_pcm(self, audio: bytes, *, sample_rate: int = 16_000) -> ASRResult:
        if not audio:
            raise AudioServiceError("空音频")

        request_id = str(uuid.uuid4())
        headers = self._headers(resource_id=self.asr_resource_id, request_id=request_id)
        init_payload = {
            "user": {"uid": request_id},
            "audio": {
                "format": "pcm",
                "codec": "raw",
                "rate": sample_rate,
                "bits": 16,
                "channel": 1,
            },
            "request": {
                "model_name": self.asr_model_name,
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": False,
                "show_utterances": True,
                "enable_nonstream": False,
            },
        }

        try:
            async with websockets.connect(
                self.asr_endpoint,
                additional_headers=headers,
                ping_interval=20,
                ping_timeout=20,
                max_size=16 * 1024 * 1024,
            ) as ws:
                seq = 1
                await _send_openspeech_message(
                    ws,
                    OpenSpeechMessage(
                        message_type=OpenSpeechMessageType.FULL_CLIENT_REQUEST,
                        flag=OpenSpeechFlag.POSITIVE_SEQUENCE,
                        serialization=OpenSpeechSerialization.JSON,
                        compression=OpenSpeechCompression.GZIP,
                        sequence=seq,
                        payload=_gzip_json(init_payload),
                    ),
                )
                seq += 1

                chunks = _chunk_bytes(audio, bytes_per_chunk=sample_rate * 2 // 5)
                for index, chunk in enumerate(chunks):
                    is_last = index == len(chunks) - 1
                    await _send_openspeech_message(
                        ws,
                        OpenSpeechMessage(
                            message_type=OpenSpeechMessageType.AUDIO_ONLY_CLIENT,
                            flag=OpenSpeechFlag.NEGATIVE_SEQUENCE
                            if is_last
                            else OpenSpeechFlag.POSITIVE_SEQUENCE,
                            serialization=OpenSpeechSerialization.JSON,
                            compression=OpenSpeechCompression.GZIP,
                            sequence=-seq if is_last else seq,
                            payload=gzip.compress(chunk),
                        ),
                    )
                    seq += 1

                transcript = await self._receive_asr_text(ws)
        except AudioServiceError:
            raise
        except InvalidStatus as exc:
            raise AudioServiceError(f"ASR 连接失败：{_invalid_status_text(exc)}") from exc
        except (OSError, WebSocketException, asyncio.TimeoutError) as exc:
            raise AudioServiceError(f"ASR 连接失败：{exc}") from exc

        if not transcript:
            raise AudioServiceError("ASR 返回空文本")
        return ASRResult(text=transcript)

    async def synthesize_speech(self, text: str, *, voice: str) -> TTSResult:
        clean_text = text.strip()
        if not clean_text:
            raise AudioServiceError("空文本")
        if voice not in TTS_VOICE_IDS:
            raise AudioServiceError(f"未知音色：{voice}")

        request_id = str(uuid.uuid4())
        headers = self._headers(resource_id=self.tts_resource_id, request_id=request_id)
        session_id = str(uuid.uuid4())
        audio = bytearray()

        try:
            async with websockets.connect(
                self.tts_endpoint,
                additional_headers=headers,
                ping_interval=20,
                ping_timeout=20,
                max_size=16 * 1024 * 1024,
            ) as ws:
                await self._start_tts_connection(ws)
                await self._start_tts_session(ws, session_id=session_id, request_id=request_id, voice=voice)
                await self._send_tts_task(
                    ws,
                    session_id=session_id,
                    request_id=request_id,
                    voice=voice,
                    text=clean_text,
                )
                await _send_openspeech_message(
                    ws,
                    OpenSpeechMessage(
                        message_type=OpenSpeechMessageType.FULL_CLIENT_REQUEST,
                        flag=OpenSpeechFlag.WITH_EVENT,
                        event=OpenSpeechEvent.FINISH_SESSION,
                        session_id=session_id,
                        payload=b"{}",
                    ),
                )

                session_finished = False
                while True:
                    msg = await _recv_openspeech_message(ws, timeout=self.timeout)
                    if msg.message_type == OpenSpeechMessageType.AUDIO_ONLY_SERVER:
                        audio.extend(msg.payload)
                        continue
                    if msg.event in (OpenSpeechEvent.TTS_ENDED, OpenSpeechEvent.SESSION_FINISHED):
                        session_finished = msg.event == OpenSpeechEvent.SESSION_FINISHED
                        break
                    if msg.event in (OpenSpeechEvent.SESSION_FAILED, OpenSpeechEvent.CONNECTION_FAILED):
                        raise AudioServiceError(_message_error_text(msg))

                if not session_finished:
                    await _send_openspeech_message(
                        ws,
                        OpenSpeechMessage(
                            message_type=OpenSpeechMessageType.FULL_CLIENT_REQUEST,
                            flag=OpenSpeechFlag.WITH_EVENT,
                            event=OpenSpeechEvent.FINISH_SESSION,
                            session_id=session_id,
                            payload=b"{}",
                        ),
                    )
                    with _suppress_audio_errors():
                        await _recv_openspeech_message(ws, timeout=3.0)

                await _send_openspeech_message(
                    ws,
                    OpenSpeechMessage(
                        message_type=OpenSpeechMessageType.FULL_CLIENT_REQUEST,
                        flag=OpenSpeechFlag.WITH_EVENT,
                        event=OpenSpeechEvent.FINISH_CONNECTION,
                        payload=b"{}",
                    ),
                )
                with _suppress_audio_errors():
                    await _recv_openspeech_message(ws, timeout=3.0)
        except AudioServiceError:
            raise
        except InvalidStatus as exc:
            raise AudioServiceError(f"TTS 连接失败：{_invalid_status_text(exc)}") from exc
        except (OSError, WebSocketException, asyncio.TimeoutError) as exc:
            raise AudioServiceError(f"TTS 连接失败：{exc}") from exc

        if not audio:
            raise AudioServiceError("TTS 返回空音频")
        wav = _pcm16_to_wav(bytes(audio), sample_rate=self.tts_sample_rate, channels=1)
        return TTSResult(audio=wav, media_type="audio/wav", voice=voice)

    def _headers(
        self,
        *,
        resource_id: str | None = None,
        request_id: str | None = None,
        connect_id: str | None = None,
    ) -> dict[str, str]:
        return {
            "X-Api-Key": self.api_key,
            "X-Api-Resource-Id": resource_id or "",
            "X-Api-Request-Id": request_id or str(uuid.uuid4()),
            "X-Api-Connect-Id": connect_id or str(uuid.uuid4()),
        }

    async def _receive_asr_text(self, ws) -> str:
        latest = ""
        while True:
            msg = await _recv_openspeech_message(ws, timeout=self.timeout)
            payload = msg.json_payload()
            if msg.message_type == OpenSpeechMessageType.FULL_SERVER_RESPONSE and isinstance(payload, dict):
                result = payload.get("result")
                if isinstance(result, dict):
                    text = str(result.get("text") or "").strip()
                    if text:
                        latest = text
                    is_final = bool(result.get("is_final")) or msg.flag in (
                        OpenSpeechFlag.LAST_NO_SEQUENCE,
                        OpenSpeechFlag.NEGATIVE_SEQUENCE,
                    )
                    if is_final:
                        return latest
                if msg.event == OpenSpeechEvent.ASR_ENDED:
                    return latest
            if msg.flag in (OpenSpeechFlag.LAST_NO_SEQUENCE, OpenSpeechFlag.NEGATIVE_SEQUENCE):
                return latest

    async def _start_tts_connection(self, ws) -> None:
        await _send_openspeech_message(
            ws,
            OpenSpeechMessage(
                message_type=OpenSpeechMessageType.FULL_CLIENT_REQUEST,
                flag=OpenSpeechFlag.WITH_EVENT,
                event=OpenSpeechEvent.START_CONNECTION,
                payload=b"{}",
            ),
        )
        msg = await _recv_openspeech_message(ws, timeout=self.timeout)
        if msg.event != OpenSpeechEvent.CONNECTION_STARTED:
            raise AudioServiceError(f"TTS 建连失败：{_message_error_text(msg)}")

    async def _start_tts_session(self, ws, *, session_id: str, request_id: str, voice: str) -> None:
        await _send_openspeech_message(
            ws,
            OpenSpeechMessage(
                message_type=OpenSpeechMessageType.FULL_CLIENT_REQUEST,
                flag=OpenSpeechFlag.WITH_EVENT,
                event=OpenSpeechEvent.START_SESSION,
                session_id=session_id,
                payload=_tts_payload(
                    request_id=request_id,
                    event=OpenSpeechEvent.START_SESSION,
                    voice=voice,
                    sample_rate=self.tts_sample_rate,
                ),
            ),
        )
        msg = await _recv_openspeech_message(ws, timeout=self.timeout)
        if msg.event != OpenSpeechEvent.SESSION_STARTED:
            raise AudioServiceError(f"TTS Session 启动失败：{_message_error_text(msg)}")

    async def _send_tts_task(
        self,
        ws,
        *,
        session_id: str,
        request_id: str,
        voice: str,
        text: str,
    ) -> None:
        await _send_openspeech_message(
            ws,
            OpenSpeechMessage(
                message_type=OpenSpeechMessageType.FULL_CLIENT_REQUEST,
                flag=OpenSpeechFlag.WITH_EVENT,
                event=OpenSpeechEvent.TASK_REQUEST,
                session_id=session_id,
                payload=_tts_payload(
                    request_id=request_id,
                    event=OpenSpeechEvent.TASK_REQUEST,
                    voice=voice,
                    sample_rate=self.tts_sample_rate,
                    text=text,
                ),
            ),
        )


def build_audio_service_from_settings() -> ArkAudioService:
    api_key = settings.ark_audio_api_key or settings.ark_embedding_api_key or settings.ark_api_key
    return ArkAudioService(
        api_key=api_key,
        asr_model=settings.ark_asr_model,
        asr_model_name=settings.ark_asr_model_name,
        tts_model=settings.ark_tts_model,
        tts_endpoint=settings.ark_tts_endpoint,
        asr_endpoint=settings.ark_asr_endpoint,
        asr_resource_id=settings.ark_asr_resource_id,
        tts_resource_id=settings.ark_tts_resource_id,
        tts_sample_rate=settings.ark_tts_sample_rate,
    )


def _default_tts_endpoint() -> str:
    return "wss://openspeech.bytedance.com/api/v3/tts/bidirection"


def _default_asr_endpoint() -> str:
    return "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"


def _normalize_asr_model_name(value: str) -> str:
    return "bigmodel" if value.startswith("Speech_Recognition_Seed") else value


def _read_exact(buf: io.BytesIO, size: int) -> bytes:
    data = buf.read(size)
    if len(data) != size:
        raise AudioServiceError("音频服务返回帧不完整")
    return data


def _gzip_json(payload: dict[str, Any]) -> bytes:
    return gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _tts_payload(
    *,
    request_id: str,
    event: OpenSpeechEvent,
    voice: str,
    sample_rate: int,
    text: str | None = None,
) -> bytes:
    req_params: dict[str, Any] = {
        "speaker": voice,
        "audio_params": {
            "format": "pcm",
            "sample_rate": sample_rate,
            "speech_rate": 0,
        },
    }
    if text is not None:
        req_params["text"] = text
    return json.dumps(
        {
            "user": {"uid": request_id},
            "namespace": "BidirectionalTTS",
            "event": int(event),
            "req_params": req_params,
        },
        ensure_ascii=False,
    ).encode("utf-8")


async def _send_openspeech_message(ws, msg: OpenSpeechMessage) -> None:
    await ws.send(msg.to_bytes())


async def _recv_openspeech_message(ws, *, timeout: float) -> OpenSpeechMessage:
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise AudioServiceError(f"等待音频服务响应超时：{timeout}s") from exc
    if isinstance(raw, str):
        raise AudioServiceError(f"音频服务返回非二进制帧：{raw[:120]}")
    msg = OpenSpeechMessage.from_bytes(raw)
    if msg.message_type == OpenSpeechMessageType.ERROR:
        raise AudioServiceError(_message_error_text(msg))
    if msg.event in (OpenSpeechEvent.CONNECTION_FAILED, OpenSpeechEvent.SESSION_FAILED):
        raise AudioServiceError(_message_error_text(msg))
    return msg


def _message_error_text(msg: OpenSpeechMessage) -> str:
    payload = msg.json_payload()
    if payload:
        return str(payload.get("message") or payload.get("error") or payload)
    if msg.payload:
        return msg.payload.decode("utf-8", errors="replace")
    if msg.error_code:
        return f"OpenSpeech error code {msg.error_code}"
    return f"OpenSpeech unexpected event {msg.event.name}"


def _invalid_status_text(exc: InvalidStatus) -> str:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None) or getattr(response, "status", None)
    headers = getattr(response, "headers", {}) or {}
    logid = headers.get("X-Tt-Logid") if hasattr(headers, "get") else None
    parts = [f"HTTP {status}" if status else str(exc)]
    if logid:
        parts.append(f"X-Tt-Logid={logid}")
    return "，".join(parts)


class _suppress_audio_errors:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is not None and issubclass(exc_type, AudioServiceError)


def _chunk_bytes(data: bytes, *, bytes_per_chunk: int) -> list[bytes]:
    if bytes_per_chunk <= 0:
        return [data]
    return [data[i : i + bytes_per_chunk] for i in range(0, len(data), bytes_per_chunk)]


def _pcm16_to_wav(pcm: bytes, *, sample_rate: int, channels: int) -> bytes:
    bits_per_sample = 16
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_size = len(pcm)
    header = b"".join(
        [
            b"RIFF",
            struct.pack("<I", 36 + data_size),
            b"WAVE",
            b"fmt ",
            struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits_per_sample),
            b"data",
            struct.pack("<I", data_size),
        ]
    )
    return header + pcm
