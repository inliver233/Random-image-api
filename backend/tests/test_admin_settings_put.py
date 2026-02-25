from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.main import create_app


def test_admin_put_settings_updates_and_get_reflects(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_put_settings.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        put_resp = client.put(
            "/admin/api/settings",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={
                "settings": {
                    "proxy": {
                        "enabled": True,
                        "fail_closed": False,
                        "route_mode": "allowlist",
                        "allowlist_domains": ["i.pximg.net"],
                    },
                    "random": {
                        "default_attempts": 5,
                        "default_r18_strict": False,
                        "fail_cooldown_ms": 12345,
                        "strategy": "random",
                        "quality_samples": 7,
                        "recommendation": {
                            "pick_mode": "best",
                            "temperature": 2.5,
                            "score_weights": {"bookmark": 10, "view": 1},
                            "multipliers": {"ai": 0.5, "manga": 0},
                        },
                    },
                    "security": {"hide_origin_url_in_public_json": False},
                    "rate_limit": {"proxy_probe_concurrency": 7},
                }
            },
        )
        assert put_resp.status_code == 200
        body1 = put_resp.json()
        assert body1["ok"] is True
        assert body1["updated"] >= 4
        assert body1["request_id"] == "req_test"
        assert put_resp.headers["X-Request-Id"] == "req_test"

        get_resp = client.get(
            "/admin/api/settings",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert get_resp.status_code == 200
        body2 = get_resp.json()
        assert body2["ok"] is True

        settings = body2["settings"]
        assert settings["proxy"]["enabled"] is True
        assert settings["proxy"]["fail_closed"] is False
        assert settings["proxy"]["route_mode"] == "allowlist"
        assert settings["proxy"]["allowlist_domains"] == ["i.pximg.net"]

        assert settings["random"]["default_attempts"] == 5
        assert settings["random"]["default_r18_strict"] is False
        assert settings["random"]["fail_cooldown_ms"] == 12345
        assert settings["random"]["strategy"] == "random"
        assert settings["random"]["quality_samples"] == 7
        assert settings["random"]["dedup"]["enabled"] is True
        assert settings["random"]["dedup"]["window_s"] == 1200
        assert settings["random"]["recommendation"]["pick_mode"] == "best"
        assert settings["random"]["recommendation"]["temperature"] == 2.5
        assert settings["random"]["recommendation"]["freshness_half_life_days"] == 21.0
        assert settings["random"]["recommendation"]["velocity_smooth_days"] == 2.0
        assert settings["random"]["recommendation"]["score_weights"]["bookmark"] == 10.0
        assert settings["random"]["recommendation"]["score_weights"]["view"] == 1.0
        assert settings["random"]["recommendation"]["multipliers"]["ai"] == 0.5
        assert settings["random"]["recommendation"]["multipliers"]["manga"] == 0.0

        assert settings["security"]["hide_origin_url_in_public_json"] is False
        assert settings["rate_limit"]["proxy_probe_concurrency"] == 7


def test_admin_put_settings_rejects_invalid_route_mode(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_put_settings_bad_route_mode.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.put(
            "/admin/api/settings",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"settings": {"proxy": {"route_mode": "nope"}}},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "BAD_REQUEST"
        assert body["request_id"] == "req_test"


def test_admin_put_settings_rejects_invalid_random_recommendation(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_put_settings_bad_recommendation.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.put(
            "/admin/api/settings",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"settings": {"random": {"recommendation": {"pick_mode": "nope"}}}},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "BAD_REQUEST"
        assert body["request_id"] == "req_test"
