from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.main import create_app


def test_unhandled_exception_returns_json_with_request_id(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "unhandled_exception.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get(
            "/admin/api/audit?limit=50",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 500
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "INTERNAL_ERROR"
        assert body["request_id"] == "req_test"
        assert isinstance(body.get("details"), dict)
        assert str(body.get("message") or "").strip()
