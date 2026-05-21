"""Phase 0 烟雾测试：保证 import 链路完整、健康检查可用。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_root_endpoint() -> None:
    with TestClient(app) as client:
        resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "shopping-guide-server"
    assert body["status"] == "ok"


def test_healthz_responds() -> None:
    # Phase 0 不强制 MySQL 已起，只要 endpoint 不崩。
    with TestClient(app) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["app"] == "ok"
    assert body["db"] in {"ok", "down"}
