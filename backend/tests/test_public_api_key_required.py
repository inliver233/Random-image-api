from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.api_keys import api_key_hint, hmac_sha256_hex
from app.db.models.api_keys import ApiKey
from app.db.models.base import Base
from app.db.models.images import Image
from app.db.session import create_sessionmaker
from app.main import create_app


def test_public_api_key_required_enforces_and_rate_limits(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "public_api_key_required.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    api_key = "k_" + ("x" * 40)

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("PUBLIC_API_KEY_REQUIRED", "true")
    monkeypatch.setenv("PUBLIC_API_KEY_RPM", "1")
    monkeypatch.setenv("PUBLIC_API_KEY_BURST", "1")

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        key_hash = hmac_sha256_hex(secret_key="secret_test", message=api_key)
        hint = api_key_hint(api_key)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            session.add(
                ApiKey(
                    name="public1",
                    key_hash=key_hash,
                    hint=hint,
                    enabled=1,
                )
            )
            session.add(
                Image(
                    illust_id=123,
                    page_index=0,
                    ext="jpg",
                    original_url="https://example.test/origin.jpg",
                    proxy_path="/i/1.jpg",
                    random_key=0.5,
                    x_restrict=0,
                )
            )
            await session.commit()

        await app.state.engine.dispose()

    asyncio.run(_seed())

    with TestClient(app) as client:
        healthz = client.get("/healthz")
        assert healthz.status_code == 200

        missing = client.get("/random?format=json&attempts=1")
        assert missing.status_code == 401
        missing_body = missing.json()
        assert missing_body["ok"] is False
        assert missing_body["code"] == "UNAUTHORIZED"

        invalid = client.get("/random?format=json&attempts=1", headers={"X-API-Key": "bad"})
        assert invalid.status_code == 401
        invalid_body = invalid.json()
        assert invalid_body["ok"] is False
        assert invalid_body["code"] == "UNAUTHORIZED"

        ok1 = client.get("/random?format=json&attempts=1", headers={"X-API-Key": api_key})
        assert ok1.status_code == 200

        limited = client.get("/random?format=json&attempts=1", headers={"X-API-Key": api_key})
        assert limited.status_code == 429
        limited_body = limited.json()
        assert limited_body["ok"] is False
        assert limited_body["code"] == "RATE_LIMITED"


def test_public_api_key_accepts_query_param_fallback(tmp_path: Path, monkeypatch) -> None:
    """Browser navigations cannot send X-API-Key; ?api_key= is accepted as fallback."""
    db_path = tmp_path / "public_api_key_query.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    api_key = "k_" + ("q" * 40)

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("PUBLIC_API_KEY_REQUIRED", "true")
    monkeypatch.setenv("PUBLIC_API_KEY_RPM", "0")
    monkeypatch.setenv("PUBLIC_API_KEY_BURST", "0")

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        key_hash = hmac_sha256_hex(secret_key="secret_test", message=api_key)
        hint = api_key_hint(api_key)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            session.add(
                ApiKey(
                    name="public_query",
                    key_hash=key_hash,
                    hint=hint,
                    enabled=1,
                )
            )
            session.add(
                Image(
                    illust_id=456,
                    page_index=0,
                    ext="jpg",
                    original_url="https://example.test/origin2.jpg",
                    proxy_path="/i/1.jpg",
                    random_key=0.4,
                    x_restrict=0,
                )
            )
            await session.commit()

        await app.state.engine.dispose()

    asyncio.run(_seed())

    with TestClient(app) as client:
        missing = client.get("/random?format=json&attempts=1")
        assert missing.status_code == 401

        via_query = client.get(f"/random?format=json&attempts=1&api_key={api_key}")
        assert via_query.status_code == 200

        # Header still wins / works independently.
        via_header = client.get("/random?format=json&attempts=1", headers={"X-API-Key": api_key})
        assert via_header.status_code == 200

        bad_query = client.get("/random?format=json&attempts=1&api_key=bad")
        assert bad_query.status_code == 401


def test_wtf_page_injects_public_api_key_required_flag(tmp_path: Path, monkeypatch) -> None:
    """/wtf HTML is exempt, but client JS must know when /feed and /i need api_key."""
    db_path = tmp_path / "wtf_public_api_key.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("PUBLIC_API_KEY_REQUIRED", "true")
    monkeypatch.setenv("PUBLIC_API_KEY_RPM", "0")
    monkeypatch.setenv("PUBLIC_API_KEY_BURST", "0")

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await app.state.engine.dispose()

    asyncio.run(_seed())

    with TestClient(app) as client:
        # Page itself stays exempt from middleware key check.
        resp = client.get("/wtf")
        assert resp.status_code == 200
        body = resp.text
        assert "const PUBLIC_API_KEY_REQUIRED = true;" in body
        assert "function withApiKeyOnParams" in body
        assert "function withApiKeyOnUrl" in body
        assert 'id="apiKeyWrap"' in body
        assert "wtf_public_api_key" in body


def test_favicon_exempt_when_public_api_key_required(tmp_path: Path, monkeypatch) -> None:
    """Browsers auto-request /favicon.ico; it must stay middleware-exempt like /healthz."""
    db_path = tmp_path / "favicon_public_api_key.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("PUBLIC_API_KEY_REQUIRED", "true")
    monkeypatch.setenv("PUBLIC_API_KEY_RPM", "0")
    monkeypatch.setenv("PUBLIC_API_KEY_BURST", "0")

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await app.state.engine.dispose()

    asyncio.run(_seed())

    with TestClient(app) as client:
        fav = client.get("/favicon.ico")
        assert fav.status_code == 204

        # Non-exempt public routes still require a key.
        missing = client.get("/random?format=json&attempts=1")
        assert missing.status_code == 401


def test_docs_page_documents_public_api_key(tmp_path: Path, monkeypatch) -> None:
    """/docs must explain X-API-Key + api_key query when enforcement is on."""
    db_path = tmp_path / "docs_public_api_key.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("PUBLIC_API_KEY_REQUIRED", "true")
    monkeypatch.setenv("PUBLIC_API_KEY_RPM", "0")
    monkeypatch.setenv("PUBLIC_API_KEY_BURST", "0")

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await app.state.engine.dispose()

    asyncio.run(_seed())

    with TestClient(app) as client:
        resp = client.get("/docs")
        assert resp.status_code == 200
        body = resp.text
        assert "PUBLIC_API_KEY_REQUIRED" in body
        assert "X-API-Key" in body
        assert "api_key=" in body
        assert "当前部署已开启" in body

        status = client.get("/status")
        assert status.status_code == 200
        assert "PUBLIC_API_KEY_REQUIRED" in status.text

