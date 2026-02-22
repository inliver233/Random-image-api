from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.public.version import router as version_router


def test_version_returns_fields(monkeypatch) -> None:
    monkeypatch.setenv("APP_BUILD_TIME", "2026-02-10T00:00:00Z")
    monkeypatch.setenv("APP_VERSION", "1.2.3")
    monkeypatch.setenv("APP_COMMIT", "abc123")

    app = FastAPI()
    app.include_router(version_router)
    client = TestClient(app)

    resp = client.get("/version", headers={"X-Request-Id": "req_test"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["build_time"] == "2026-02-10T00:00:00Z"
    assert body["version"] == "1.2.3"
    assert body["git_commit"] == "abc123"
    assert body["request_id"] == "req_test"
    assert resp.headers["X-Request-Id"] == "req_test"

