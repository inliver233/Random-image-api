from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request

from app.api.admin.deps import get_admin_claims
from app.core.errors import ApiError, ErrorCode
from app.core.request_id import get_or_create_request_id
from app.core.time import iso_utc_ms
from app.db.models.hydration_runs import HydrationRun
from app.db.models.images import Image
from app.db.models.jobs import JobRow
from app.db.session import create_sessionmaker, with_sqlite_busy_retry

router = APIRouter()

_ALLOWED_RUN_STATUSES = {"pending", "running", "paused", "canceled", "completed", "failed"}


def _parse_json_dict(value: str | None) -> dict[str, Any]:
    raw = str(value or "").strip()
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except Exception:
        return {}
    if isinstance(loaded, dict):
        return loaded
    return {}


def _serialize_job(job: JobRow | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {
        "id": str(job.id),
        "status": job.status,
        "attempt": int(job.attempt),
        "max_attempts": int(job.max_attempts),
        "run_after": job.run_after,
        "last_error": job.last_error,
        "locked_by": job.locked_by,
        "locked_at": job.locked_at,
        "updated_at": job.updated_at,
    }


def _serialize_run(run: HydrationRun, *, latest_job: JobRow | None = None) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "type": run.type,
        "status": run.status,
        "criteria": _parse_json_dict(run.criteria_json),
        "cursor": _parse_json_dict(run.cursor_json),
        "total": int(run.total) if run.total is not None else None,
        "processed": int(run.processed),
        "success": int(run.success),
        "failed": int(run.failed),
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "last_error": run.last_error,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "latest_job": _serialize_job(latest_job),
    }


async def _latest_jobs_by_run_ids(session, *, run_ids: list[str]) -> dict[str, JobRow]:
    ids = [str(v).strip() for v in run_ids if str(v).strip()]
    if not ids:
        return {}

    latest_per_ref = (
        sa.select(JobRow.ref_id.label("ref_id"), sa.func.max(JobRow.id).label("max_job_id"))
        .where(JobRow.ref_type == "hydration_run", JobRow.ref_id.in_(ids))
        .group_by(JobRow.ref_id)
        .subquery()
    )

    rows = (
        await session.execute(
            sa.select(JobRow, latest_per_ref.c.ref_id)
            .join(latest_per_ref, JobRow.id == latest_per_ref.c.max_job_id)
            .order_by(JobRow.id.desc())
        )
    ).all()

    out: dict[str, JobRow] = {}
    for job, ref_id in rows:
        key = str(ref_id or "").strip()
        if not key:
            continue
        out[key] = job
    return out


async def _load_create_json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception as exc:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400) from exc

    if not isinstance(data, dict):
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400)

    run_type = str(data.get("type") or "backfill").strip().lower() or "backfill"
    if run_type not in {"backfill", "manual"}:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid type", status_code=400)

    criteria = data.get("criteria")
    if criteria is None:
        criteria = {}
    if not isinstance(criteria, dict):
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid criteria", status_code=400)

    return {"type": run_type, "criteria": criteria}


async def _load_manual_job_json(request: Request) -> dict[str, int | None]:
    try:
        data = await request.json()
    except Exception as exc:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400) from exc

    if not isinstance(data, dict):
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400)

    illust_id_raw = data.get("illust_id")
    image_id_raw = data.get("image_id")

    if illust_id_raw is None and image_id_raw is None:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Either illust_id or image_id is required", status_code=400)

    if illust_id_raw is not None and image_id_raw is not None:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Provide only one of illust_id or image_id", status_code=400)

    illust_id: int | None = None
    image_id: int | None = None
    if illust_id_raw is not None:
        try:
            illust_id = int(illust_id_raw)
        except Exception as exc:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid illust_id", status_code=400) from exc
        if illust_id <= 0:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid illust_id", status_code=400)

    if image_id_raw is not None:
        try:
            image_id = int(image_id_raw)
        except Exception as exc:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid image_id", status_code=400) from exc
        if image_id <= 0:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid image_id", status_code=400)

    return {"illust_id": illust_id, "image_id": image_id}


@router.get("/hydration-runs")
async def list_hydration_runs(
    request: Request,
    limit: int = 20,
    cursor: str | None = None,
    status: str | None = None,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    if limit < 1 or limit > 200:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported limit", status_code=400)

    cursor_i: int | None = None
    cursor_raw = str(cursor or "").strip()
    if cursor_raw:
        if not cursor_raw.isdigit():
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported cursor", status_code=400)
        cursor_i = int(cursor_raw)
        if cursor_i <= 0:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported cursor", status_code=400)

    status_norm = str(status or "").strip().lower() or None
    if status_norm is not None and status_norm not in _ALLOWED_RUN_STATUSES:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported status", status_code=400)

    rid = get_or_create_request_id(request)
    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async with Session() as session:
        stmt = sa.select(HydrationRun).order_by(HydrationRun.id.desc()).limit(limit + 1)
        if cursor_i is not None:
            stmt = stmt.where(HydrationRun.id < cursor_i)
        if status_norm is not None:
            stmt = stmt.where(HydrationRun.status == status_norm)

        rows = (await session.execute(stmt)).scalars().all()
        current_rows = rows[:limit]
        run_ids = [str(int(r.id)) for r in current_rows]
        jobs_by_run_id = await _latest_jobs_by_run_ids(session, run_ids=run_ids)

    next_cursor = int(current_rows[-1].id) if len(rows) > limit and current_rows else None
    items = [_serialize_run(row, latest_job=jobs_by_run_id.get(str(int(row.id)))) for row in current_rows]

    return {
        "ok": True,
        "items": items,
        "next_cursor": str(next_cursor) if next_cursor is not None else "",
        "request_id": rid,
    }


@router.get("/hydration-runs/{run_id}")
async def get_hydration_run(
    run_id: int,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    if run_id <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid hydration_run id", status_code=400)

    rid = get_or_create_request_id(request)
    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async with Session() as session:
        run = await session.get(HydrationRun, run_id)
        if run is None:
            raise ApiError(code=ErrorCode.NOT_FOUND, message="Hydration run not found", status_code=404)

        jobs = await _latest_jobs_by_run_ids(session, run_ids=[str(run_id)])
        item = _serialize_run(run, latest_job=jobs.get(str(run_id)))

    return {"ok": True, "item": item, "request_id": rid}


@router.post("/hydration-runs/manual")
async def create_manual_hydration_job(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)
    now = iso_utc_ms()
    body = await _load_manual_job_json(request)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async def _op() -> dict[str, Any]:
        async with Session() as session:
            illust_id = body.get("illust_id")
            image_id = body.get("image_id")
            if illust_id is None and image_id is not None:
                image = await session.get(Image, int(image_id))
                if image is None:
                    raise ApiError(code=ErrorCode.NOT_FOUND, message="Image not found", status_code=404)
                illust_id = int(image.illust_id)

            if illust_id is None or int(illust_id) <= 0:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid illust_id", status_code=400)

            existing = (
                (
                    await session.execute(
                        sa.select(JobRow)
                        .where(
                            JobRow.type == "hydrate_metadata",
                            JobRow.ref_type == "manual_hydrate",
                            JobRow.ref_id == str(int(illust_id)),
                            JobRow.status.in_(["pending", "running"]),
                        )
                        .order_by(JobRow.id.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )

            if existing is not None:
                return {
                    "ok": True,
                    "created": False,
                    "job_id": str(int(existing.id)),
                    "illust_id": str(int(illust_id)),
                    "request_id": rid,
                }

            job = JobRow(
                type="hydrate_metadata",
                status="pending",
                payload_json=json.dumps({"illust_id": int(illust_id)}, separators=(",", ":"), ensure_ascii=False),
                last_error=None,
                priority=0,
                run_after=None,
                attempt=0,
                max_attempts=3,
                locked_by=None,
                locked_at=None,
                ref_type="manual_hydrate",
                ref_id=str(int(illust_id)),
                updated_at=now,
            )
            session.add(job)
            await session.flush()
            await session.commit()

            return {
                "ok": True,
                "created": True,
                "job_id": str(int(job.id)),
                "illust_id": str(int(illust_id)),
                "request_id": rid,
            }

    return await with_sqlite_busy_retry(_op)


@router.post("/hydration-runs")
async def create_hydration_run(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)
    now = iso_utc_ms()
    body = await _load_create_json(request)

    run_type = str(body["type"])
    criteria = dict(body["criteria"])

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async def _op() -> tuple[int, int]:
        async with Session() as session:
            run = HydrationRun(
                type=run_type,
                status="pending",
                criteria_json=json.dumps(criteria, separators=(",", ":"), ensure_ascii=False),
                cursor_json=None,
                total=None,
                processed=0,
                success=0,
                failed=0,
                started_at=None,
                finished_at=None,
                last_error=None,
                updated_at=now,
            )
            session.add(run)
            await session.flush()

            job = JobRow(
                type="hydrate_metadata",
                status="pending",
                payload_json=json.dumps(
                    {"hydration_run_id": int(run.id), "criteria": criteria},
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
                last_error=None,
                priority=0,
                run_after=None,
                attempt=0,
                max_attempts=3,
                locked_by=None,
                locked_at=None,
                ref_type="hydration_run",
                ref_id=str(int(run.id)),
                updated_at=now,
            )
            session.add(job)
            await session.flush()
            await session.commit()
            return int(run.id), int(job.id)

    run_id, job_id = await with_sqlite_busy_retry(_op)

    return {
        "ok": True,
        "hydration_run_id": str(run_id),
        "job_id": str(job_id),
        "request_id": rid,
    }


async def _set_run_and_job_status(
    request: Request,
    *,
    run_id: int,
    target_status: str,
    allowed_from: set[str],
    job_status: str,
) -> dict[str, Any]:
    if run_id <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid hydration_run id", status_code=400)

    rid = get_or_create_request_id(request)
    now = iso_utc_ms()

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async def _op() -> dict[str, Any]:
        async with Session() as session:
            run = await session.get(HydrationRun, run_id)
            if run is None:
                raise ApiError(code=ErrorCode.NOT_FOUND, message="Hydration run not found", status_code=404)

            if str(run.status) not in allowed_from:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported status transition", status_code=400)

            run.status = target_status
            run.updated_at = now
            if target_status in {"canceled", "completed", "failed"} and run.finished_at is None:
                run.finished_at = now

            job = (
                (
                    await session.execute(
                        sa.select(JobRow)
                        .where(JobRow.ref_type == "hydration_run", JobRow.ref_id == str(run_id))
                        .order_by(JobRow.id.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if job is not None:
                job.status = job_status
                if job_status in {"pending", "canceled", "paused", "dlq"}:
                    job.run_after = None
                    job.locked_by = None
                    job.locked_at = None
                job.updated_at = now

            await session.commit()

        return {
            "ok": True,
            "hydration_run_id": str(run_id),
            "status": target_status,
            "job_status": job_status if job is not None else "",
            "request_id": rid,
        }

    return await with_sqlite_busy_retry(_op)


@router.post("/hydration-runs/{run_id}/pause")
async def pause_hydration_run(
    run_id: int,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    return await _set_run_and_job_status(
        request,
        run_id=run_id,
        target_status="paused",
        allowed_from={"pending", "running"},
        job_status="paused",
    )


@router.post("/hydration-runs/{run_id}/resume")
async def resume_hydration_run(
    run_id: int,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    return await _set_run_and_job_status(
        request,
        run_id=run_id,
        target_status="pending",
        allowed_from={"paused"},
        job_status="pending",
    )


@router.post("/hydration-runs/{run_id}/cancel")
async def cancel_hydration_run(
    run_id: int,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    return await _set_run_and_job_status(
        request,
        run_id=run_id,
        target_status="canceled",
        allowed_from={"pending", "running", "paused"},
        job_status="canceled",
    )
