from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Literal
from uuid import uuid4

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from app.api.admin.deps import get_admin_claims
from app.core.data_files import get_sqlite_db_dir, make_file_ref
from app.core.errors import ApiError, ErrorCode
from app.core.request_id import get_or_create_request_id
from app.core.time import iso_utc_ms
from app.core.pixiv_urls import parse_pixiv_original_url
from app.db.models.images import Image
from app.db.models.imports import Import
from app.db.models.jobs import JobRow
from app.db.session import create_sessionmaker, with_sqlite_busy_retry
from app.jobs.dispatch import JobDispatcher
from app.jobs.executor import execute_claimed_job
from app.jobs.handlers.import_images import build_import_images_handler

router = APIRouter()


class ImportCreateRequest(BaseModel):
    text: str = Field(min_length=1)
    dry_run: bool = False
    hydrate_on_import: bool = False
    source: str = "manual"


class ImportRollbackRequest(BaseModel):
    mode: Literal["disable", "delete"]


@dataclass(frozen=True, slots=True)
class ImportErrorItem:
    line: int
    url: str
    code: str
    message: str


def _max_import_text_bytes() -> int:
    default = 50 * 1024 * 1024
    raw = (os.environ.get("IMPORT_MAX_BYTES") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except Exception:
        return default
    return max(1024, min(int(value), 50 * 1024 * 1024))


def _import_inline_max_accepted() -> int:
    default = 200
    raw = (os.environ.get("IMPORT_INLINE_MAX_ACCEPTED") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except Exception:
        return default
    return max(0, min(int(value), 10_000))


async def _claim_job_by_id(engine, *, job_id: int, worker_id: str, now: str) -> dict[str, Any] | None:
    sql = """
UPDATE jobs
SET status='running',
    locked_by=:worker_id,
    locked_at=:now,
    updated_at=:now
WHERE id=:id AND status='pending'
RETURNING *;
""".strip()

    async def _op() -> dict[str, Any] | None:
        async with engine.begin() as conn:
            result = await conn.exec_driver_sql(sql, {"id": int(job_id), "worker_id": worker_id, "now": now})
            row = result.mappings().first()
            return dict(row) if row else None

    return await with_sqlite_busy_retry(_op)


def _parse_import_text(
    text: str, *, preview_limit: int = 20
) -> tuple[int, int, int, int, list[ImportErrorItem], list[dict[str, Any]]]:
    total = 0
    accepted = 0
    deduped = 0
    error_total = 0
    errors: list[ImportErrorItem] = []
    seen: set[tuple[int, int]] = set()
    preview: list[dict[str, Any]] = []

    for line_no, raw in enumerate(text.splitlines(), start=1):
        url = raw.strip()
        if not url:
            continue
        total += 1
        try:
            parsed = parse_pixiv_original_url(url)
        except Exception as exc:
            error_total += 1
            if len(errors) < 200:
                errors.append(
                    ImportErrorItem(
                        line=line_no,
                        url=url,
                        code=ErrorCode.UNSUPPORTED_URL.value,
                        message=str(exc) or ErrorCode.UNSUPPORTED_URL.value,
                    )
                )
            continue

        key = (parsed.illust_id, parsed.page_index)
        if key in seen:
            deduped += 1
            continue
        seen.add(key)
        accepted += 1

        if len(preview) < int(preview_limit):
            preview.append(
                {
                    "illust_id": parsed.illust_id,
                    "page_index": parsed.page_index,
                    "ext": parsed.ext,
                    "url": url,
                }
            )

    return total, accepted, deduped, error_total, errors, preview


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "y", "on"}:
            return True
        if v in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _validate_import_create(data: dict[str, Any]) -> ImportCreateRequest:
    try:
        return ImportCreateRequest(**data)
    except ValidationError as exc:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="导入请求体无效", status_code=400) from exc


async def _load_import_request(request: Request) -> ImportCreateRequest:
    content_type = (request.headers.get("content-type") or "").lower()
    max_bytes = _max_import_text_bytes()

    if content_type.startswith("application/json"):
        data = await request.json()
        if not isinstance(data, dict):
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400)
        body = _validate_import_create(data)
        if len(body.text.encode("utf-8", errors="ignore")) > max_bytes:
            raise ApiError(code=ErrorCode.PAYLOAD_TOO_LARGE, message="Payload too large", status_code=413)
        return body

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        file_obj = form.get("file")
        if not isinstance(file_obj, UploadFile):
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Missing file", status_code=400)

        filename = (file_obj.filename or "").strip().lower()
        if filename and not filename.endswith(".txt"):
            raise ApiError(code=ErrorCode.INVALID_UPLOAD_TYPE, message="Unsupported upload type", status_code=400)

        raw = await file_obj.read()
        if len(raw) > max_bytes:
            raise ApiError(code=ErrorCode.PAYLOAD_TOO_LARGE, message="Payload too large", status_code=413)
        text = raw.decode("utf-8", errors="replace")

        return _validate_import_create(
            {
                "text": text,
                "dry_run": _parse_bool(form.get("dry_run"), default=False),
                "hydrate_on_import": _parse_bool(form.get("hydrate_on_import"), default=False),
                "source": str(form.get("source") or "manual"),
            }
        )

    raise ApiError(code=ErrorCode.INVALID_UPLOAD_TYPE, message="Unsupported content type", status_code=400)


@router.post("/imports")
async def create_import(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    body = await _load_import_request(request)
    rid = get_or_create_request_id(request)

    total, accepted, deduped, error_total, errors, preview = _parse_import_text(body.text, preview_limit=20)

    if body.dry_run:
        return {
            "ok": True,
            "import_id": "",
            "job_id": "",
            "accepted": accepted,
            "deduped": deduped,
            "errors": [asdict(e) for e in errors[:200]],
            "preview": preview,
            "request_id": rid,
        }

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    settings = request.app.state.settings
    db_dir = get_sqlite_db_dir(settings.database_url)
    payload_dir = db_dir / "imports_payloads"
    payload_dir.mkdir(parents=True, exist_ok=True)

    payload_path = payload_dir / f"import_payload_{uuid4().hex}.txt"
    try:
        payload_path.write_text(body.text, encoding="utf-8")
    except OSError as exc:
        raise ApiError(code=ErrorCode.INTERNAL_ERROR, message="写入导入内容失败", status_code=500) from exc

    file_ref = make_file_ref(payload_path, base_dir=db_dir)

    async def _op() -> tuple[int, int]:
        async with Session() as session:
            imp = Import(created_by=str(_claims.get("sub") or ""), source=body.source)
            session.add(imp)
            await session.flush()

            imp.total = int(total)
            imp.accepted = int(accepted)
            imp.success = 0
            imp.failed = int(error_total)
            imp.detail_json = json.dumps(
                {
                    "deduped": int(deduped),
                    "errors": [asdict(e) for e in errors[:200]],
                },
                ensure_ascii=False,
            )

            job = JobRow(
                type="import_images",
                status="pending",
                payload_json=json.dumps(
                    {
                        "import_id": int(imp.id),
                        "file_ref": file_ref,
                        "hydrate_on_import": bool(body.hydrate_on_import),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                ref_type="import",
                ref_id=str(imp.id),
            )
            session.add(job)
            await session.flush()

            await session.commit()
            return int(imp.id), int(job.id)

    try:
        import_id, job_id = await with_sqlite_busy_retry(_op)
    except Exception:
        try:
            payload_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise

    inline_max = _import_inline_max_accepted()
    executed_inline = False
    if accepted > 0 and accepted <= inline_max:
        now = iso_utc_ms()
        actor = str(_claims.get("sub") or "admin").strip() or "admin"
        worker_id = f"inline-import:{actor}"
        claimed = await _claim_job_by_id(engine, job_id=int(job_id), worker_id=worker_id, now=now)
        if claimed is not None:
            dispatcher = JobDispatcher()
            dispatcher.register("import_images", build_import_images_handler(engine))
            await execute_claimed_job(engine, dispatcher, job_row=claimed, worker_id=worker_id)
            executed_inline = True

    return {
        "ok": True,
        "import_id": str(import_id),
        "job_id": str(job_id),
        "executed_inline": executed_inline,
        "accepted": accepted,
        "deduped": deduped,
        "errors": [asdict(e) for e in errors[:200]],
        "preview": preview,
        "request_id": rid,
    }


@router.post("/imports/{import_id}/rollback")
async def rollback_import(
    import_id: int,
    body: ImportRollbackRequest,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    if import_id <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid import id", status_code=400)

    rid = get_or_create_request_id(request)

    target_status = 2 if body.mode == "disable" else 4
    now_expr = sa.text("(strftime('%Y-%m-%dT%H:%M:%fZ','now'))")

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async def _op() -> int:
        async with Session() as session:
            imp = await session.get(Import, import_id)
            if imp is None:
                raise ApiError(code=ErrorCode.NOT_FOUND, message="Import not found", status_code=404)

            result = await session.execute(
                sa.update(Image)
                .where(Image.created_import_id == import_id)
                .values(status=target_status, updated_at=now_expr)
            )
            updated = int(result.rowcount or 0)
            await session.commit()
            return int(updated)

    updated = await with_sqlite_busy_retry(_op)

    return {
        "ok": True,
        "mode": body.mode,
        "updated": updated,
        "request_id": rid,
    }


@router.get("/imports/{import_id}")
async def get_import(
    import_id: int,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    if import_id <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid import id", status_code=400)

    rid = get_or_create_request_id(request)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async with Session() as session:
        imp = await session.get(Import, import_id)
        if imp is None:
            raise ApiError(code=ErrorCode.NOT_FOUND, message="Import not found", status_code=404)

        job = (
            (
                await session.execute(
                    sa.select(JobRow)
                    .where(JobRow.ref_type == "import", JobRow.ref_id == str(import_id))
                    .order_by(JobRow.id.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )

    detail: dict[str, Any] = {}
    if imp.detail_json:
        try:
            parsed = json.loads(imp.detail_json)
            if isinstance(parsed, dict):
                detail = parsed
        except Exception:
            detail = {}

    return {
        "ok": True,
        "item": {
            "import": {
                "id": str(imp.id),
                "created_at": imp.created_at,
                "created_by": imp.created_by,
                "source": imp.source,
                "total": int(imp.total or 0),
                "accepted": int(imp.accepted or 0),
                "success": int(imp.success or 0),
                "failed": int(imp.failed or 0),
            },
            "job": (
                {
                    "id": str(job.id),
                    "type": job.type,
                    "status": job.status,
                    "attempt": job.attempt,
                    "max_attempts": job.max_attempts,
                    "last_error": job.last_error,
                }
                if job is not None
                else None
            ),
            "detail": detail,
        },
        "request_id": rid,
    }
