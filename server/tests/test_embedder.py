"""DoubaoEmbedder 多模态接口单测。

不调真实 API：用 monkeypatch 替换 _embed_one_text / _embed_one_image / _embed_one_multimodal
为 fake 返回，只验证调用路径与参数构造、以及 L2 归一化。
"""
from __future__ import annotations

from pathlib import Path

import math
import pytest

from app.rag.embedder import DoubaoEmbedder, l2_normalize


FIXTURE = Path(__file__).parent / "fixtures" / "red_64.jpg"


def _fake_embedder(monkeypatch, captured: dict, *, vec: list[float]):
    """构造一个 DoubaoEmbedder 实例，把 SDK 调用替换成 fake。"""
    emb = DoubaoEmbedder.__new__(DoubaoEmbedder)  # 绕过 __init__ 真连 Ark
    emb.model = "fake-vision"
    emb.concurrency = 1
    emb.normalize = True
    emb._dim = None

    def fake_call(*, model, input):
        captured["model"] = model
        captured["input"] = input
        # 模拟 Ark SDK 返回结构：resp.data.embedding
        class _Resp:
            class data:
                embedding = list(vec)
        return _Resp

    class _Client:
        class multimodal_embeddings:
            create = staticmethod(fake_call)

    emb.client = _Client
    return emb


def test_embed_image_returns_l2_normalized_vector(monkeypatch):
    captured: dict = {}
    raw = [3.0, 0.0, 4.0]  # |v| = 5
    emb = _fake_embedder(monkeypatch, captured, vec=raw)

    vec = emb.embed_image(str(FIXTURE))

    assert math.isclose(sum(v * v for v in vec), 1.0, abs_tol=1e-6)
    # 验证传给 SDK 的 input 是 image_url 形态
    assert captured["model"] == "fake-vision"
    parts = captured["input"]
    assert len(parts) == 1
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_embed_image_raises_when_file_missing(monkeypatch):
    captured: dict = {}
    emb = _fake_embedder(monkeypatch, captured, vec=[1.0])
    with pytest.raises(FileNotFoundError):
        emb.embed_image("/nonexistent/no.jpg")


def test_embed_multimodal_combines_text_and_image(monkeypatch):
    captured: dict = {}
    emb = _fake_embedder(monkeypatch, captured, vec=[1.0, 0.0])

    vec = emb.embed_multimodal(text="清新风格", image_path=str(FIXTURE))

    assert math.isclose(sum(v * v for v in vec), 1.0, abs_tol=1e-6)
    parts = captured["input"]
    assert len(parts) == 2
    types = [p["type"] for p in parts]
    assert "text" in types and "image_url" in types
    # text 在前，与 docs/02 默认顺序一致（便于 prompt cache）
    assert parts[0]["type"] == "text"
    assert parts[0]["text"] == "清新风格"


def test_embed_multimodal_text_only(monkeypatch):
    captured: dict = {}
    emb = _fake_embedder(monkeypatch, captured, vec=[1.0])

    emb.embed_multimodal(text="只有文字")

    parts = captured["input"]
    assert len(parts) == 1
    assert parts[0]["type"] == "text"


def test_embed_multimodal_both_none_raises(monkeypatch):
    captured: dict = {}
    emb = _fake_embedder(monkeypatch, captured, vec=[1.0])
    with pytest.raises(ValueError):
        emb.embed_multimodal(text=None, image_path=None)


def test_l2_normalize_returns_unit_vector():
    out = l2_normalize([3.0, 0.0, 4.0])
    assert math.isclose(sum(v * v for v in out), 1.0, abs_tol=1e-9)


def test_l2_normalize_zero_vector_returns_zero():
    assert l2_normalize([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]
