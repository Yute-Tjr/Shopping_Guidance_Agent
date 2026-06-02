"""Phase 5C audio API tests: ASR upload, TTS synthesis, and voice options."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.audio import get_audio_service
from app.audio.ark_audio_client import (
    ASRResult,
    TTSResult,
    ArkAudioService,
    AudioServiceError,
    OpenSpeechEvent,
    OpenSpeechFlag,
    OpenSpeechMessage,
    OpenSpeechMessageType,
    _default_asr_endpoint,
    _default_tts_endpoint,
)
from app.main import app


class FakeAudioService:
    def __init__(self) -> None:
        self.asr_calls: list[tuple[bytes, int]] = []
        self.tts_calls: list[tuple[str, str]] = []

    async def transcribe_pcm(self, audio: bytes, *, sample_rate: int = 16_000) -> ASRResult:
        self.asr_calls.append((audio, sample_rate))
        return ASRResult(text="推荐蓝牙耳机")

    async def synthesize_speech(self, text: str, *, voice: str) -> TTSResult:
        self.tts_calls.append((text, voice))
        return TTSResult(audio=b"RIFFfake-wave", media_type="audio/wav", voice=voice)


@pytest.fixture()
def fake_audio_service():
    service = FakeAudioService()
    app.dependency_overrides[get_audio_service] = lambda: service
    try:
        yield service
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def client(fake_audio_service):  # noqa: ARG001
    return TestClient(app)


def test_list_tts_voices(client: TestClient):
    resp = client.get("/api/v1/audio/voices")
    assert resp.status_code == 200
    body = resp.json()
    voice_ids = [v["id"] for v in body["voices"]]
    assert "saturn_zh_female_cancan_tob" in voice_ids
    assert "zh_female_vv_uranus_bigtts" in voice_ids
    assert body["default_voice"] in voice_ids


def test_default_audio_endpoints_use_openspeech_gateway():
    assert _default_tts_endpoint() == "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
    assert _default_asr_endpoint() == "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"


def test_openspeech_client_uses_x_api_headers_and_resource_ids():
    service = ArkAudioService(
        api_key="audio-key",
        asr_model="bigmodel",
        tts_model="seed-tts-2.0",
        tts_endpoint="wss://openspeech.bytedance.com/api/v3/tts/bidirection",
        asr_endpoint="wss://openspeech.bytedance.com/api/v3/sauc/bigmodel",
        asr_resource_id="volc.seedasr.sauc.duration",
        tts_resource_id="seed-tts-2.0",
    )

    assert service.tts_endpoint == "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
    assert service.asr_endpoint == "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
    assert service._headers(resource_id="seed-tts-2.0", request_id="rid", connect_id="cid") == {
        "X-Api-Key": "audio-key",
        "X-Api-Resource-Id": "seed-tts-2.0",
        "X-Api-Request-Id": "rid",
        "X-Api-Connect-Id": "cid",
    }


def test_openspeech_binary_message_roundtrip():
    msg = OpenSpeechMessage(
        message_type=OpenSpeechMessageType.FULL_CLIENT_REQUEST,
        flag=OpenSpeechFlag.WITH_EVENT,
        event=OpenSpeechEvent.START_SESSION,
        session_id="session-1",
        payload=b'{"ok":true}',
    )

    parsed = OpenSpeechMessage.from_bytes(msg.to_bytes())

    assert parsed.message_type == OpenSpeechMessageType.FULL_CLIENT_REQUEST
    assert parsed.flag == OpenSpeechFlag.WITH_EVENT
    assert parsed.event == OpenSpeechEvent.START_SESSION
    assert parsed.session_id == "session-1"
    assert parsed.payload == b'{"ok":true}'


@pytest.mark.asyncio
async def test_tts_wraps_websocket_connect_failure(monkeypatch):
    class FailingConnect:
        async def __aenter__(self):
            raise OSError("network down")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    service = ArkAudioService(
        api_key="audio-key",
        asr_model="bigmodel",
        tts_model="seed-tts-2.0",
    )
    monkeypatch.setattr(
        "app.audio.ark_audio_client.websockets.connect",
        lambda *args, **kwargs: FailingConnect(),
    )

    with pytest.raises(AudioServiceError, match="TTS 连接失败"):
        await service.synthesize_speech("你好", voice="saturn_zh_female_cancan_tob")


def test_asr_uploads_pcm_and_returns_transcript(client: TestClient, fake_audio_service: FakeAudioService):
    pcm = b"\x00\x00" * 1600
    resp = client.post(
        "/api/v1/audio/asr",
        files={"file": ("speech.pcm", pcm, "audio/pcm")},
    )

    assert resp.status_code == 200
    assert resp.json() == {"text": "推荐蓝牙耳机"}
    assert fake_audio_service.asr_calls == [(pcm, 16_000)]


def test_asr_rejects_empty_audio(client: TestClient):
    resp = client.post(
        "/api/v1/audio/asr",
        files={"file": ("speech.pcm", b"", "audio/pcm")},
    )
    assert resp.status_code == 400


def test_tts_returns_wav_for_selected_voice(client: TestClient, fake_audio_service: FakeAudioService):
    resp = client.post(
        "/api/v1/audio/tts",
        json={"text": "为你推荐这两款耳机", "voice": "saturn_zh_male_shuanglangshaonian_tob"},
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("audio/wav")
    assert resp.headers["x-voice-id"] == "saturn_zh_male_shuanglangshaonian_tob"
    assert resp.content.startswith(b"RIFF")
    assert fake_audio_service.tts_calls == [
        ("为你推荐这两款耳机", "saturn_zh_male_shuanglangshaonian_tob")
    ]


def test_tts_rejects_unknown_voice(client: TestClient):
    resp = client.post(
        "/api/v1/audio/tts",
        json={"text": "你好", "voice": "unknown_voice"},
    )
    assert resp.status_code == 422
