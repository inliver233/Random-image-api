from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request

from app.api.admin.deps import get_admin_claims
from app.core.admin_cursor_query import parse_admin_int_cursor, slice_id_cursor_page
from app.core.admin_json import admin_cursor_list, admin_ok
from app.core.admin_request import parse_optional_choice_filter, parse_optional_str, require_positive_id
from app.core.errors import ApiError, ErrorCode
from app.core.request_id import get_or_create_request_id
from app.core.soft_json import soft_json_value
from app.core.time import iso_utc_ms
from app.db.models.jobs import JobRow
from app.db.session import create_sessionmaker, with_sqlite_busy_retry

router = APIRouter()

_ALLOWED_JOB_STATUSES = {"pending", "running", "paused", "canceled", "completed", "failed", "dlq"}


def _serialize_job_row(row: JobRow, *, include_payload: bool = False) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": str(row.id),
        "type": row.type,
        "status": row.status,
        "priority": int(row.priority),
        "run_after": row.run_after,
        "attempt": int(row.attempt),
        "max_attempts": int(row.max_attempts),
        "last_error": row.last_error,
        "locked_by": row.locked_by,
        "locked_at": row.locked_at,
        "ref_type": row.ref_type,
        "ref_id": row.ref_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
    if include_payload:
        item["payload"] = soft_json_value(row.payload_json)
        item["payload_json"] = str(row.payload_json or "")
    return item


@router.get("/jobs")
async def list_jobs(
    request: Request,
    limit: int = 50,
    cursor: str | None = None,
    status: str | None = None,
    type: str | None = None,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    parsed = parse_admin_int_cursor(limit=limit, cursor=cursor, limit_max=200)
    limit = parsed.limit
    cursor_i = parsed.cursor_i

    status_norm = parse_optional_choice_filter(
        status,
        field="status",
        choices=_ALLOWED_JOB_STATUSES,
        invalid_message="Unsupported status",
    )

    # Job type filter is free-form (not an enum); only length is constrained.
    type_norm = parse_optional_str(type, max_len=100, field="type")

    rid = get_or_create_request_id(request)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    stmt = sa.select(JobRow).order_by(JobRow.id.desc()).limit(limit + 1)
    if cursor_i is not None:
        stmt = stmt.where(JobRow.id < cursor_i)
    if status_norm is not None:
        stmt = stmt.where(JobRow.status == status_norm)
    if type_norm is not None:
        stmt = stmt.where(JobRow.type == type_norm)

    async with Session() as session:
        rows = ((await session.execute(stmt)).scalars().all())

    items_rows, next_cursor = slice_id_cursor_page(list(rows), limit)
    items = [_serialize_job_row(row) for row in items_rows]

    return admin_cursor_list(request, items=items, next_cursor=next_cursor, request_id=rid)


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: int,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    job_id = require_positive_id(job_id, invalid_message="Invalid job id")

    rid = get_or_create_request_id(request)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async with Session() as session:
        row = await session.get(JobRow, job_id)
        if row is None:
            raise ApiError(code=ErrorCode.NOT_FOUND, message="Job not found", status_code=404)

    return admin_ok(
        request,
        payload={"item": _serialize_job_row(row, include_payload=True)},
        request_id=rid,
    )


@router.post("/jobs/{job_id}/retry")
async def retry_job(
    job_id: int,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    job_id = require_positive_id(job_id, invalid_message="Invalid job id")

    rid = get_or_create_request_id(request)
    now = iso_utc_ms()

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async def _op() -> dict[str, Any]:
        async with Session() as session:
            row = await session.get(JobRow, job_id)
            if row is None:
                raise ApiError(code=ErrorCode.NOT_FOUND, message="Job not found", status_code=404)

            if row.status == "running":
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Job is running", status_code=400)

            row.status = "pending"
            row.run_after = None
            row.locked_by = None
            row.locked_at = None
            row.updated_at = now
            await session.commit()

        return admin_ok(
            request,
            payload={"job_id": str(job_id), "status": "pending"},
            request_id=rid,
        )

    return await with_sqlite_busy_retry(_op)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: int,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    job_id = require_positive_id(job_id, invalid_message="Invalid job id")

    rid = get_or_create_request_id(request)
    now = iso_utc_ms()

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async def _op() -> dict[str, Any]:
        async with Session() as session:
            row = await session.get(JobRow, job_id)
            if row is None:
                raise ApiError(code=ErrorCode.NOT_FOUND, message="Job not found", status_code=404)

            row.status = "canceled"
            row.locked_by = None
            row.locked_at = None
            row.updated_at = now
            await session.commit()

        return admin_ok(
            request,
            payload={"job_id": str(job_id), "status": "canceled"},
            request_id=rid,
        )

    return await with_sqlite_busy_retry(_op)


@router.post("/jobs/{job_id}/move-to-dlq")
async def move_job_to_dlq(
    job_id: int,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    job_id = require_positive_id(job_id, invalid_message="Invalid job id")

    rid = get_or_create_request_id(request)
    now = iso_utc_ms()

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async def _op() -> dict[str, Any]:
        async with Session() as session:
            row = await session.get(JobRow, job_id)
            if row is None:
                raise ApiError(code=ErrorCode.NOT_FOUND, message="Job not found", status_code=404)

            row.status = "dlq"
            row.run_after = None
            row.locked_by = None
            row.locked_at = None
            row.updated_at = now
            await session.commit()

        return admin_ok(
            request,
            payload={"job_id": str(job_id), "status": "dlq"},
            request_id=rid,
        )

    return await with_sqlite_busy_retry(_op)
