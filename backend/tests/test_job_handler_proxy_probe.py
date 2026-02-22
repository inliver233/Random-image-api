from __future__ import annotations

import asyncio
import json
from pathlib import Path

import sqlalchemy as sa
from cryptography.fernet import Fernet

from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.jobs import JobRow
from app.db.models.proxy_endpoints import ProxyEndpoint
from app.db.session import create_sessionmaker
from app.jobs.claim import claim_next_job
from app.jobs.dispatch import JobDispatcher
from app.jobs.executor import execute_claimed_job
from app.jobs.handlers.proxy_probe import ProbeConfig, ProbeResult, ProbeTarget, build_proxy_probe_handler


def _sqlite_url(db_path: Path) -> str:
    return "sqlite+aiosqlite:///" + db_path.as_posix()


def test_job_handler_proxy_probe_updates_endpoint_health(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "handler_proxy_probe.db"
    engine = create_engine(_sqlite_url(db_path))

    field_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", field_key)

    async def prober(target: ProbeTarget, _cfg: ProbeConfig) -> ProbeResult:
        if int(target.endpoint_id) == 1:
            return ProbeResult(endpoint_id=1, ok=True, latency_ms=12.5)
        return ProbeResult(endpoint_id=int(target.endpoint_id), ok=False, latency_ms=33.3, error="boom")

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
                    username="",
                    password_enc="",
                    enabled=1,
                    source="manual",
                )
            )
            session.add(
                ProxyEndpoint(
                    scheme="http",
                    host="2.2.2.2",
                    port=8081,
                    username="",
                    password_enc="",
                    enabled=1,
                    source="manual",
                )
            )
            session.add(
                JobRow(
                    type="proxy_probe",
                    status="pending",
                    payload_json=json.dumps({"scope": "all"}, ensure_ascii=False, separators=(",", ":")),
                    ref_type="proxy_probe",
                    ref_id="all",
                )
            )
            await session.commit()

        dispatcher = JobDispatcher()
        dispatcher.register("proxy_probe", build_proxy_probe_handler(engine, prober=prober))

        claimed = await claim_next_job(engine, worker_id="w1")
        assert claimed is not None
        assert claimed["type"] == "proxy_probe"

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

            ok_ep = endpoints[0]
            fail_ep = endpoints[1]

            assert int(ok_ep.success_count or 0) == 1
            assert ok_ep.last_ok_at is not None and ok_ep.last_ok_at
            assert ok_ep.last_error is None
            assert ok_ep.blacklisted_until is None
            assert ok_ep.last_latency_ms is not None

            assert int(fail_ep.failure_count or 0) == 1
            assert fail_ep.last_fail_at is not None and fail_ep.last_fail_at
            assert fail_ep.last_error is not None and "boom" in str(fail_ep.last_error)
            assert fail_ep.last_latency_ms is not None

        await engine.dispose()

    asyncio.run(_run())

