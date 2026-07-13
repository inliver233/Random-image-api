from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.coerce import truncate_text
from app.core.redact import redact_text
from app.core.time import iso_utc_ms
from app.db.models.cf_worker_deployments import CfWorkerDeployment
from app.db.session import create_sessionmaker


class CfWorkerIdentityConflict(ValueError):
    pass


class CfWorkerOperationConflict(ValueError):
    pass


async def reserve_cf_worker_deployment(
    engine: AsyncEngine,
    *,
    account_id: str,
    worker_name: str,
    kind: str,
) -> tuple[int, int]:
    """Persist deploy intent and fence one account/name to exactly one Worker kind."""
    account_id = str(account_id or "").strip().lower()
    worker_name = str(worker_name or "").strip().lower()
    kind = str(kind or "").strip().lower()
    if not account_id or not worker_name or kind not in {"api", "image"}:
        raise ValueError("invalid CF Worker deployment identity")
    Session = create_sessionmaker(engine)
    async with Session() as session:
        row = (
            await session.execute(
                sa.select(CfWorkerDeployment)
                .where(
                    CfWorkerDeployment.account_id == account_id,
                    CfWorkerDeployment.worker_name == worker_name,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            row = CfWorkerDeployment(
                account_id=account_id,
                worker_name=worker_name,
                kind=kind,
                state="intent",
                operation_version=1,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                row = (
                    await session.execute(
                        sa.select(CfWorkerDeployment).where(
                            CfWorkerDeployment.account_id == account_id,
                            CfWorkerDeployment.worker_name == worker_name,
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    raise CfWorkerOperationConflict("CF Worker deployment reservation raced")
            else:
                await session.refresh(row)
                return int(row.id), 1
        if str(row.kind) != kind:
            raise CfWorkerIdentityConflict(
                f"Worker {worker_name!r} in this account is already reserved for kind={row.kind}"
            )
        if str(row.state) not in {"failed", "deleted"}:
            raise CfWorkerOperationConflict(
                f"Worker {worker_name!r} deployment is already state={row.state}"
            )
        previous_version = int(row.operation_version)
        result = await session.execute(
            sa.update(CfWorkerDeployment)
            .where(
                CfWorkerDeployment.id == int(row.id),
                CfWorkerDeployment.operation_version == previous_version,
                CfWorkerDeployment.state.in_(["failed", "deleted"]),
            )
            .values(
                state="intent",
                operation_version=previous_version + 1,
                last_error=None,
                updated_at=iso_utc_ms(),
            )
        )
        if int(result.rowcount or 0) != 1:
            await session.rollback()
            raise CfWorkerOperationConflict("CF Worker deployment operation is already in progress")
        await session.commit()
        return int(row.id), previous_version + 1


async def mark_cf_worker_deployment(
    engine: AsyncEngine,
    *,
    deployment_id: int,
    operation_version: int,
    expected_state: str,
    state: str,
    base_url: str | None = None,
    last_error: str | None = None,
) -> None:
    Session = create_sessionmaker(engine)
    safe_error = truncate_text(redact_text(str(last_error or "")), max_len=500) or None
    values: dict[str, object] = {
        "state": state,
        "last_error": safe_error,
        "updated_at": iso_utc_ms(),
    }
    if base_url is not None:
        values["base_url"] = base_url
    async with Session() as session:
        result = await session.execute(
            sa.update(CfWorkerDeployment)
            .where(
                CfWorkerDeployment.id == int(deployment_id),
                CfWorkerDeployment.operation_version == int(operation_version),
                CfWorkerDeployment.state == expected_state,
            )
            .values(**values)
        )
        if int(result.rowcount or 0) != 1:
            await session.rollback()
            raise CfWorkerOperationConflict("stale CF Worker deployment transition")
        await session.commit()
