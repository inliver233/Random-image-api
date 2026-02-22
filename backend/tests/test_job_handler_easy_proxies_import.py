from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import sqlalchemy as sa
from cryptography.fernet import Fernet

from app.core.crypto import FieldEncryptor
from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.jobs import JobRow
from app.db.models.proxy_endpoints import ProxyEndpoint
from app.db.session import create_sessionmaker
from app.jobs.claim import claim_next_job
from app.jobs.dispatch import JobDispatcher
from app.jobs.executor import execute_claimed_job
from app.jobs.handlers.easy_proxies_import import build_easy_proxies_import_handler


def _sqlite_url(db_path: Path) -> str:
    return "sqlite+aiosqlite:///" + db_path.as_posix()


def test_job_handler_easy_proxies_import_skips_non_easy_sources_by_default(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "handler_easy_proxies_import.db"
    engine = create_engine(_sqlite_url(db_path))

    field_key = Fernet.generate_key().decode("ascii")
    encryptor = FieldEncryptor.from_key(field_key)

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", field_key)

    base_url = "http://easy-proxies:9090"
    password = "pw"

    old_password = "old"

    def handler(req: httpx.Request) -> httpx.Response:
        if str(req.url) == f"{base_url}/api/auth":
            return httpx.Response(200, json={"token": "t"})
        if str(req.url) == f"{base_url}/api/export":
            assert req.headers.get("Authorization") == "Bearer t"
            return httpx.Response(
                200,
                text="\n".join(
                    [
                        "http://u:p@1.2.3.4:8080",
                        "http://2.2.2.2:8081",
                        "not_a_uri",
                        "",
                    ]
                ),
            )
        return httpx.Response(500, text="unexpected")

    transport = httpx.MockTransport(handler)

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            session.add(
                ProxyEndpoint(
                    scheme="http",
                    host="1.2.3.4",
                    port=8080,
                    username="u",
                    password_enc=encryptor.encrypt_text(old_password),
                    enabled=1,
                    source="manual",
                    source_ref=None,
                )
            )
            session.add(
                JobRow(
                    type="easy_proxies_import",
                    status="pending",
                    payload_json=json.dumps(
                        {
                            "base_url": base_url,
                            "password": password,
                            "conflict_policy": "skip_non_easy_proxies",
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
            )
            await session.commit()

        dispatcher = JobDispatcher()
        dispatcher.register(
            "easy_proxies_import",
            build_easy_proxies_import_handler(engine, transport=transport),
        )

        claimed = await claim_next_job(engine, worker_id="w1")
        assert claimed is not None
        assert claimed["type"] == "easy_proxies_import"

        transition = await execute_claimed_job(engine, dispatcher, job_row=claimed, worker_id="w1")
        assert transition is not None
        assert transition.status.value == "completed"

        async with Session() as session:
            endpoints = (
                (await session.execute(sa.select(ProxyEndpoint).order_by(ProxyEndpoint.id.asc())))
                .scalars()
                .all()
            )
            assert len(endpoints) == 2

            existing = endpoints[0]
            assert existing.source == "manual"
            assert encryptor.decrypt_text(str(existing.password_enc)) == old_password

            created = endpoints[1]
            assert created.source == "easy_proxies"
            assert created.source_ref == base_url
            assert created.host == "2.2.2.2"
            assert int(created.port) == 8081

        await engine.dispose()

    asyncio.run(_run())


def test_job_handler_easy_proxies_import_uses_env_password_when_missing(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "handler_easy_proxies_import_env_password.db"
    engine = create_engine(_sqlite_url(db_path))

    field_key = Fernet.generate_key().decode("ascii")

    base_url = "http://easy-proxies:9090"
    env_password = "pw_env_secret"

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", field_key)
    monkeypatch.setenv("EASY_PROXIES_PASSWORD", env_password)

    def handler(req: httpx.Request) -> httpx.Response:
        if str(req.url) == f"{base_url}/api/auth":
            body = json.loads((req.content or b"{}").decode("utf-8"))
            assert body.get("password") == env_password
            return httpx.Response(200, json={"token": "t"})
        if str(req.url) == f"{base_url}/api/export":
            assert req.headers.get("Authorization") == "Bearer t"
            return httpx.Response(200, text="http://2.2.2.2:8081\n")
        return httpx.Response(500, text="unexpected")

    transport = httpx.MockTransport(handler)

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            session.add(
                JobRow(
                    type="easy_proxies_import",
                    status="pending",
                    payload_json=json.dumps(
                        {"base_url": base_url, "conflict_policy": "overwrite"},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
            )
            await session.commit()

        dispatcher = JobDispatcher()
        dispatcher.register(
            "easy_proxies_import",
            build_easy_proxies_import_handler(engine, transport=transport),
        )

        claimed = await claim_next_job(engine, worker_id="w1")
        assert claimed is not None
        assert claimed["type"] == "easy_proxies_import"

        transition = await execute_claimed_job(engine, dispatcher, job_row=claimed, worker_id="w1")
        assert transition is not None
        assert transition.status.value == "completed"

        async with Session() as session:
            endpoints = (
                (await session.execute(sa.select(ProxyEndpoint).order_by(ProxyEndpoint.id.asc())))
                .scalars()
                .all()
            )
            assert len(endpoints) == 1
            created = endpoints[0]
            assert created.source == "easy_proxies"
            assert created.source_ref == base_url
            assert created.host == "2.2.2.2"
            assert int(created.port) == 8081

        await engine.dispose()

    asyncio.run(_run())


def test_job_handler_easy_proxies_import_rewrites_placeholder_host(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "handler_easy_proxies_import_rewrite_host.db"
    engine = create_engine(_sqlite_url(db_path))

    field_key = Fernet.generate_key().decode("ascii")
    encryptor = FieldEncryptor.from_key(field_key)

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", field_key)

    base_url = "http://easy-proxies:9090"
    password = "pw"

    def handler(req: httpx.Request) -> httpx.Response:
        if str(req.url) == f"{base_url}/api/auth":
            return httpx.Response(200, json={"token": "t"})
        if str(req.url) == f"{base_url}/api/export":
            assert req.headers.get("Authorization") == "Bearer t"
            return httpx.Response(200, text="http://u:p@0.0.0.0:8080\n")
        return httpx.Response(500, text="unexpected")

    transport = httpx.MockTransport(handler)

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            session.add(
                ProxyEndpoint(
                    scheme="http",
                    host="0.0.0.0",
                    port=8080,
                    username="u",
                    password_enc=encryptor.encrypt_text("old"),
                    enabled=1,
                    source="easy_proxies",
                    source_ref=base_url,
                    failure_count=7,
                    blacklisted_until="2099-01-01T00:00:00Z",
                    last_error="ConnectError: old",
                )
            )
            session.add(
                JobRow(
                    type="easy_proxies_import",
                    status="pending",
                    payload_json=json.dumps(
                        {
                            "base_url": base_url,
                            "password": password,
                            "conflict_policy": "skip_non_easy_proxies",
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
            )
            await session.commit()

        dispatcher = JobDispatcher()
        dispatcher.register(
            "easy_proxies_import",
            build_easy_proxies_import_handler(engine, transport=transport),
        )

        claimed = await claim_next_job(engine, worker_id="w1")
        assert claimed is not None
        transition = await execute_claimed_job(engine, dispatcher, job_row=claimed, worker_id="w1")
        assert transition is not None
        assert transition.status.value == "completed"

        async with Session() as session:
            endpoints = (
                (await session.execute(sa.select(ProxyEndpoint).order_by(ProxyEndpoint.id.asc())))
                .scalars()
                .all()
            )
            assert len(endpoints) == 1
            row = endpoints[0]
            assert row.host == "easy-proxies"
            assert int(row.port) == 8080
            assert row.blacklisted_until is None
            assert row.last_error is None

        await engine.dispose()

    asyncio.run(_run())
