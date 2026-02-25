from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.db.models.runtime_settings import RuntimeSetting
from app.db.session import create_sessionmaker
from app.main import create_app


def test_admin_get_settings_returns_defaults_and_runtime_overrides(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_get_settings.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    async def _migrate_and_seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            session.add_all(
                [
                    RuntimeSetting(key="proxy.enabled", value_json="true", description=None, updated_by=None),
                    RuntimeSetting(
                        key="proxy.route_mode",
                        value_json=json.dumps("allowlist", separators=(",", ":"), ensure_ascii=False),
                        description=None,
                        updated_by=None,
                    ),
                    RuntimeSetting(
                        key="proxy.allowlist_domains",
                        value_json=json.dumps(["i.pximg.net"], separators=(",", ":"), ensure_ascii=False),
                        description=None,
                        updated_by=None,
                    ),
                ]
            )
            await session.commit()

    asyncio.run(_migrate_and_seed())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.get(
            "/admin/api/settings",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["request_id"] == "req_test"
        assert resp.headers["X-Request-Id"] == "req_test"

        settings = body["settings"]
        assert settings["proxy"]["enabled"] is True
        assert settings["proxy"]["fail_closed"] is True
        assert settings["proxy"]["route_mode"] == "allowlist"
        assert settings["proxy"]["allowlist_domains"] == ["i.pximg.net"]

        assert settings["random"]["default_attempts"] == 3
        assert settings["random"]["default_r18_strict"] is True
        assert settings["random"]["fail_cooldown_ms"] == 600000
        assert settings["random"]["strategy"] == "quality"
        assert settings["random"]["quality_samples"] == 12
        assert settings["random"]["dedup"]["enabled"] is True
        assert settings["random"]["dedup"]["window_s"] == 1200
        assert settings["random"]["dedup"]["max_images"] == 5000
        assert settings["random"]["dedup"]["max_authors"] == 2000
        assert settings["random"]["dedup"]["strict"] is False
        assert settings["random"]["dedup"]["image_penalty"] == 8.0
        assert settings["random"]["dedup"]["author_penalty"] == 2.5

        recommendation = settings["random"]["recommendation"]
        assert recommendation["pick_mode"] == "weighted"
        assert recommendation["temperature"] == 1.0
        assert recommendation["freshness_half_life_days"] == 21.0
        assert recommendation["velocity_smooth_days"] == 2.0
        assert recommendation["score_weights"]["bookmark"] == 4.0
        assert recommendation["score_weights"]["view"] == 0.5
        assert recommendation["multipliers"]["ai"] == 1.0
        assert recommendation["multipliers"]["manga"] == 1.0
