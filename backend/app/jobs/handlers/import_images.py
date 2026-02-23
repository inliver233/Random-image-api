from __future__ import annotations

import json
import random
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import load_settings
from app.core.data_files import get_sqlite_db_dir, resolve_file_ref
from app.core.pixiv_urls import parse_pixiv_original_url
from app.db.models.images import Image
from app.db.models.imports import Import
from app.db.models.jobs import JobRow
from app.db.session import create_sessionmaker, with_sqlite_busy_retry
from app.jobs.errors import JobPermanentError

_MAX_ERRORS = 200
_CHUNK_SIZE = 200


@dataclass(frozen=True, slots=True)
class ImportLineError:
    line: int
    url: str
    code: str
    message: str


def _as_bool(value: Any, *, default: bool = False) -> bool:
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


def _resolve_payload_file(payload: dict[str, Any]) -> Path | None:
    if "file_ref" not in payload:
        return None

    file_ref = str(payload.get("file_ref") or "").strip()
    if not file_ref:
        raise JobPermanentError("payload.file_ref is required")

    settings = load_settings()
    base_dir = get_sqlite_db_dir(settings.database_url)
    try:
        return resolve_file_ref(file_ref, base_dir=base_dir)
    except Exception as exc:
        raise JobPermanentError("payload.file_ref invalid") from exc


def _iter_lines(payload: dict[str, Any], *, file_path: Path | None) -> Iterable[tuple[int, str]]:
    if file_path is not None:
        try:
            with file_path.open("r", encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f, start=1):
                    yield i, line
        except FileNotFoundError as exc:
            raise JobPermanentError("payload.file_ref not found") from exc
        return

    if "text_lines" in payload:
        raw = payload.get("text_lines")
        if not isinstance(raw, list):
            raise JobPermanentError("payload.text_lines must be a list")
        for i, v in enumerate(raw, start=1):
            yield i, str(v)
        return

    if "text" in payload:
        text = str(payload.get("text") or "")
        for i, line in enumerate(text.splitlines(), start=1):
            yield i, line
        return

    raise JobPermanentError("payload.text_lines or payload.text or payload.file_ref is required")


def _parse_payload(payload_json: str) -> dict[str, Any]:
    try:
        data = json.loads(payload_json)
    except Exception as exc:
        raise JobPermanentError("payload_json is not valid JSON") from exc
    if not isinstance(data, dict):
        raise JobPermanentError("payload_json must be an object")
    return data


def build_import_images_handler(engine: AsyncEngine):
    Session = create_sessionmaker(engine)

    async def _handler(job: dict[str, Any]) -> None:
        payload_json = str(job.get("payload_json") or "")
        payload = _parse_payload(payload_json)

        try:
            import_id = int(payload.get("import_id"))
        except Exception as exc:
            raise JobPermanentError("payload.import_id is required") from exc
        if import_id <= 0:
            raise JobPermanentError("payload.import_id is required")

        hydrate_on_import = _as_bool(payload.get("hydrate_on_import"), default=False)
        file_path = _resolve_payload_file(payload)

        async def _ensure_import_exists() -> None:
            async with Session() as session:
                imp = await session.get(Import, import_id)
                if imp is None:
                    raise JobPermanentError("Import not found")

        await with_sqlite_busy_retry(_ensure_import_exists)

        total = 0
        accepted = 0
        success = 0
        deduped = 0
        error_total = 0
        errors: list[ImportLineError] = []
        seen: set[tuple[int, int]] = set()
        illust_ids: set[int] = set()

        chunk_rows: list[dict[str, Any]] = []
        chunk_keys: list[tuple[int, int]] = []

        async def _persist_chunk(
            rows: list[dict[str, Any]],
            keys: list[tuple[int, int]],
            *,
            total_v: int,
            accepted_v: int,
            success_v: int,
            failed_v: int,
        ) -> None:
            if not rows:
                return

            async def _op() -> None:
                async with Session() as session:
                    now_expr = sa.text("(strftime('%Y-%m-%dT%H:%M:%fZ','now'))")

                    stmt = sqlite_insert(Image).values(rows)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["illust_id", "page_index"],
                        set_={
                            "ext": stmt.excluded.ext,
                            "original_url": stmt.excluded.original_url,
                            "proxy_path": sa.case(
                                (sa.func.length(stmt.excluded.proxy_path) > 0, stmt.excluded.proxy_path),
                                else_=Image.proxy_path,
                            ),
                            "created_import_id": stmt.excluded.created_import_id,
                            "updated_at": now_expr,
                        },
                    )
                    await session.execute(stmt)

                    # Fill proxy_path for (newly inserted) rows that are still empty.
                    if keys:
                        await session.execute(
                            sa.update(Image)
                            .where(Image.created_import_id == int(import_id))
                            .where(Image.proxy_path == "")
                            .where(sa.tuple_(Image.illust_id, Image.page_index).in_(keys))
                            .values(proxy_path=sa.text("'/i/' || id || '.' || ext"))
                        )

                    await session.execute(
                        sa.update(Import)
                        .where(Import.id == int(import_id))
                        .values(
                            total=sa.func.max(Import.total, int(total_v)),
                            accepted=sa.func.max(Import.accepted, int(accepted_v)),
                            success=sa.func.max(Import.success, int(success_v)),
                            failed=sa.func.max(Import.failed, int(failed_v)),
                        )
                    )
                    await session.commit()

            await with_sqlite_busy_retry(_op)

        for line_no, raw in _iter_lines(payload, file_path=file_path):
            url = raw.strip()
            if not url:
                continue
            total += 1
            try:
                parsed = parse_pixiv_original_url(url)
            except Exception as exc:
                error_total += 1
                if len(errors) < _MAX_ERRORS:
                    errors.append(
                        ImportLineError(
                            line=int(line_no),
                            url=url,
                            code="unsupported_url",
                            message=str(exc) or "unsupported_url",
                        )
                    )
                continue

            key = (int(parsed.illust_id), int(parsed.page_index))
            if key in seen:
                deduped += 1
                continue
            seen.add(key)

            accepted += 1
            if hydrate_on_import:
                illust_ids.add(int(parsed.illust_id))

            chunk_keys.append(key)
            chunk_rows.append(
                {
                    "illust_id": int(parsed.illust_id),
                    "page_index": int(parsed.page_index),
                    "ext": str(parsed.ext),
                    "original_url": url,
                    "proxy_path": "",
                    "random_key": float(random.random()),
                    "created_import_id": int(import_id),
                }
            )

            if len(chunk_rows) < _CHUNK_SIZE:
                continue

            success_after = int(success + len(chunk_rows))
            await _persist_chunk(
                chunk_rows,
                chunk_keys,
                total_v=int(total),
                accepted_v=int(accepted),
                success_v=int(success_after),
                failed_v=int(error_total),
            )
            success = success_after
            chunk_rows.clear()
            chunk_keys.clear()

        if chunk_rows:
            success_after = int(success + len(chunk_rows))
            await _persist_chunk(
                chunk_rows,
                chunk_keys,
                total_v=int(total),
                accepted_v=int(accepted),
                success_v=int(success_after),
                failed_v=int(error_total),
            )
            success = success_after
            chunk_rows.clear()
            chunk_keys.clear()

        async def _persist_detail() -> None:
            async with Session() as session:
                detail_json = json.dumps(
                    {"deduped": int(deduped), "errors": [asdict(e) for e in errors[:_MAX_ERRORS]]},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                await session.execute(
                    sa.update(Import)
                    .where(Import.id == int(import_id))
                    .values(
                        total=sa.func.max(Import.total, int(total)),
                        accepted=sa.func.max(Import.accepted, int(accepted)),
                        success=sa.func.max(Import.success, int(success)),
                        failed=sa.func.max(Import.failed, int(error_total)),
                        detail_json=detail_json,
                    )
                )
                await session.commit()

        await with_sqlite_busy_retry(_persist_detail)

        if hydrate_on_import and illust_ids:
            async def _enqueue_hydrate_jobs() -> None:
                async with Session() as session:
                    existing = set(
                        (
                            await session.execute(
                                sa.select(JobRow.ref_id)
                                .where(JobRow.type == "hydrate_metadata")
                                .where(JobRow.ref_type == "import")
                                .where(JobRow.ref_id.like(f"{import_id}:%"))
                            )
                        )
                        .scalars()
                        .all()
                    )

                    added = 0
                    for illust_id in sorted(illust_ids):
                        ref_id = f"{import_id}:{int(illust_id)}"
                        if ref_id in existing:
                            continue
                        session.add(
                            JobRow(
                                type="hydrate_metadata",
                                status="pending",
                                payload_json=json.dumps(
                                    {"illust_id": int(illust_id), "reason": "import"},
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                                ref_type="import",
                                ref_id=ref_id,
                            )
                        )
                        added += 1
                        if added % 500 == 0:
                            await session.commit()
                    if added:
                        await session.commit()

            await with_sqlite_busy_retry(_enqueue_hydrate_jobs)

        if file_path is not None:
            try:
                file_path.unlink(missing_ok=True)
            except Exception:
                pass

    return _handler
