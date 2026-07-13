from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.db.cf_worker_deployments import (
    CfWorkerIdentityConflict,
    CfWorkerOperationConflict,
    mark_cf_worker_deployment,
    reserve_cf_worker_deployment,
)
from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.cf_worker_deployments import CfWorkerDeployment
from app.db.session import create_sessionmaker


def test_cf_worker_deployment_identity_is_unique_across_kinds(tmp_path: Path) -> None:
    db_url = "sqlite+aiosqlite:///" + (tmp_path / "cf_worker_deployments.db").as_posix()

    async def _run() -> None:
        engine = create_engine(db_url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            deployment_id, operation_version = await reserve_cf_worker_deployment(
                engine,
                account_id="acct-a",
                worker_name="same-name",
                kind="image",
            )
            with pytest.raises(CfWorkerOperationConflict, match="already state=intent"):
                await reserve_cf_worker_deployment(
                    engine,
                    account_id="acct-a",
                    worker_name="same-name",
                    kind="image",
                )
            with pytest.raises(CfWorkerIdentityConflict):
                await reserve_cf_worker_deployment(
                    engine,
                    account_id="acct-a",
                    worker_name="same-name",
                    kind="api",
                )
            other_id, other_version = await reserve_cf_worker_deployment(
                engine,
                account_id="acct-b",
                worker_name="same-name",
                kind="api",
            )
            assert other_id != deployment_id
            concurrent = await asyncio.gather(
                reserve_cf_worker_deployment(
                    engine, account_id="acct-c", worker_name="race", kind="api"
                ),
                reserve_cf_worker_deployment(
                    engine, account_id="acct-c", worker_name="race", kind="api"
                ),
                return_exceptions=True,
            )
            assert sum(isinstance(item, tuple) for item in concurrent) == 1
            assert sum(isinstance(item, CfWorkerOperationConflict) for item in concurrent) == 1
            await mark_cf_worker_deployment(
                engine,
                deployment_id=other_id,
                operation_version=other_version,
                expected_state="intent",
                state="failed",
                last_error=(
                    "Bearer bearer-secret api_token=token-secret "
                    "https://proxy-user:proxy-password@proxy.example:443"
                ),
            )
            await mark_cf_worker_deployment(
                engine,
                deployment_id=deployment_id,
                operation_version=operation_version,
                expected_state="intent",
                state="complete",
                base_url="https://same-name.example.workers.dev",
            )
            Session = create_sessionmaker(engine)
            async with Session() as session:
                row = await session.get(CfWorkerDeployment, deployment_id)
                assert row is not None
                assert row.state == "complete"
                assert row.base_url == "https://same-name.example.workers.dev"
                other = await session.get(CfWorkerDeployment, other_id)
                assert other is not None and other.last_error is not None
                assert "bearer-secret" not in other.last_error
                assert "token-secret" not in other.last_error
                assert "proxy-password" not in other.last_error
        finally:
            await engine.dispose()

    asyncio.run(_run())
