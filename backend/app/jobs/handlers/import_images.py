from __future__ import annotations

import json
import random
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.admin_request import parse_bool
from app.core.coerce import as_optional_int, as_str, derive_orientation
from app.core.config import Settings, load_settings
from app.core.data_files import get_sqlite_db_dir, resolve_file_ref
from app.core.pixiv_urls import parse_pixiv_original_url
from app.core.r2_prewarm import maybe_enqueue_r2_prewarm
from app.core.random_engine_sync import maybe_publish_engine_upserts
from app.db.catalog import CatalogStore, build_catalog_store
from app.db.models.imports import Import
from app.db.models.jobs import JobRow
from app.db.session import create_sessionmaker, with_sqlite_busy_retry
from app.db.tag_store import TagStore, build_tag_store
from app.jobs.payload import parse_job_payload_object
from app.jobs.errors import JobPermanentError
from app.jobs.queue import new_pending_job

_MAX_ERRORS = 200
_CHUNK_SIZE = 200


@dataclass(frozen=True, slots=True)
class ImportLineError:
    line: int
    url: str
    code: str
    message: str


def _parse_pbd_ai_type(value: Any) -> int | None:
    raw = as_optional_int(value)
    if raw is None:
        return None
    # PixivBatchDownloader: 0 unknown, 1 non-ai, 2 ai
    if raw == 1:
        return 0
    if raw == 2:
        return 1
    return None


def _parse_pbd_illust_type(value: Any) -> int | None:
    raw = as_optional_int(value)
    if raw in {0, 1, 2}:
        return int(raw)
    return None


def _parse_pbd_created_at(value: Any) -> str | None:
    s = as_str(value)
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        if len(s) <= 64:
            return s
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_utc = dt.astimezone(timezone.utc).replace(microsecond=0)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_payload_file(
    payload: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> Path | None:
    if "file_ref" not in payload:
        return None

    file_ref = str(payload.get("file_ref") or "").strip()
    if not file_ref:
        raise JobPermanentError("payload.file_ref is required")

    s = settings if settings is not None else load_settings()
    base_dir = get_sqlite_db_dir(s.database_url)
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


def build_import_images_handler(
    engine: AsyncEngine,
    *,
    catalog: CatalogStore | None = None,
    tag_store: TagStore | None = None,
    settings: Settings | None = None,
):
    s = settings if settings is not None else load_settings()
    Session = create_sessionmaker(engine)
    catalog_store = catalog if catalog is not None else build_catalog_store(database_url=str(engine.url))
    tag_store_port = tag_store if tag_store is not None else build_tag_store(database_url=str(engine.url))

    async def _handler(job: dict[str, Any]) -> None:
        payload_json = str(job.get("payload_json") or "")
        payload = parse_job_payload_object(payload_json)

        try:
            import_id = int(payload.get("import_id"))
        except Exception as exc:
            raise JobPermanentError("payload.import_id is required") from exc
        if import_id <= 0:
            raise JobPermanentError("payload.import_id is required")

        input_format_raw = str(payload.get("input_format") or "text").strip().lower()
        input_format = "pixiv_batch_downloader_json" if input_format_raw in {"pixiv_batch_downloader_json", "pbd_json", "pbd"} else "text"

        hydrate_on_import = parse_bool(payload.get("hydrate_on_import"), default=False)
        if input_format == "pixiv_batch_downloader_json":
            # PixivBatchDownloader export already contains most metadata;
            # keep this import token-free by default.
            hydrate_on_import = False
        file_path = _resolve_payload_file(payload, settings=s)
        if input_format == "pixiv_batch_downloader_json" and file_path is None:
            raise JobPermanentError("payload.file_ref is required for pixiv_batch_downloader_json")

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
        chunk_tags: dict[tuple[int, int], list[str]] = {}

        async def _persist_chunk(
            rows: list[dict[str, Any]],
            keys: list[tuple[int, int]],
            *,
            total_v: int,
            accepted_v: int,
            success_v: int,
            failed_v: int,
            tags_by_key: dict[tuple[int, int], list[str]] | None = None,
        ) -> list[int]:
            if not rows:
                return []

            async def _op() -> list[int]:
                async with Session() as session:
                    published_ids = await catalog_store.bulk_upsert_import_rows(
                        session,
                        rows=rows,
                        keys=keys,
                        import_id=int(import_id),
                    )

                    if tags_by_key and keys:
                        names: list[str] = []
                        seen_names: set[str] = set()
                        for lst in tags_by_key.values():
                            for raw_name in lst[:64]:
                                name = str(raw_name or "").strip()
                                if not name or name in seen_names:
                                    continue
                                seen_names.add(name)
                                names.append(name)

                        if names:
                            tag_id_by_name = await tag_store_port.ensure_tags_by_names(session, names=names)
                            image_id_by_key = await catalog_store.map_image_ids_by_illust_page(session, keys=list(keys))

                            pairs: list[tuple[int, int]] = []
                            for key, lst in tags_by_key.items():
                                image_id = image_id_by_key.get(key)
                                if image_id is None:
                                    continue
                                seen_for_image: set[str] = set()
                                for raw_name in lst[:64]:
                                    name = str(raw_name or "").strip()
                                    if not name or name in seen_for_image:
                                        continue
                                    seen_for_image.add(name)
                                    tag_id = tag_id_by_name.get(name)
                                    if tag_id is None:
                                        continue
                                    pairs.append((int(image_id), int(tag_id)))

                            if pairs:
                                await tag_store_port.link_image_tags(session, pairs=pairs)

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
                    return list(published_ids or [])

            image_ids = await with_sqlite_busy_retry(_op)
            if image_ids:
                # Best-effort: warm random-engine index (+ optional R2 prewarm) after each import chunk.
                await maybe_publish_engine_upserts(
                    engine,
                    image_ids=list(image_ids),
                    settings=s,
                    catalog=catalog_store,
                    tag_store=tag_store_port,
                )
                await maybe_enqueue_r2_prewarm(
                    image_ids=list(image_ids),
                    settings=s,
                    engine=engine,
                    catalog=catalog_store,
                )
            return list(image_ids or [])

        if input_format == "pixiv_batch_downloader_json":
            try:
                with file_path.open("r", encoding="utf-8-sig", errors="replace") as f:
                    data = json.load(f)
            except Exception as exc:
                raise JobPermanentError("payload.file_ref is not valid JSON") from exc

            items: list[Any] | None = None
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                if isinstance(data.get("result"), list):
                    items = data.get("result")
                elif isinstance(data.get("data"), list):
                    items = data.get("data")
            if items is None:
                raise JobPermanentError("payload.file_ref has unsupported JSON shape")

            for idx, raw_item in enumerate(items, start=1):
                if not isinstance(raw_item, dict):
                    continue
                url = as_str(raw_item.get("original"))
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
                                line=int(idx),
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

                width = as_optional_int(raw_item.get("fullWidth"))
                height = as_optional_int(raw_item.get("fullHeight"))
                if width is not None and width <= 0:
                    width = None
                if height is not None and height <= 0:
                    height = None

                aspect_ratio, orientation = derive_orientation(width, height)
                x_restrict = as_optional_int(raw_item.get("xRestrict"))
                if x_restrict not in {0, 1, 2}:
                    x_restrict = None

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
                        "width": width,
                        "height": height,
                        "aspect_ratio": aspect_ratio,
                        "orientation": orientation,
                        "x_restrict": x_restrict,
                        "ai_type": _parse_pbd_ai_type(raw_item.get("aiType")),
                        "illust_type": _parse_pbd_illust_type(raw_item.get("type")),
                        "user_id": as_optional_int(raw_item.get("userId")),
                        "user_name": as_str(raw_item.get("user")),
                        "title": as_str(raw_item.get("title")),
                        "created_at_pixiv": _parse_pbd_created_at(raw_item.get("date")),
                        "bookmark_count": as_optional_int(raw_item.get("bmk")),
                        "view_count": as_optional_int(raw_item.get("viewCount")),
                        "comment_count": as_optional_int(raw_item.get("commentCount")),
                    }
                )

                raw_tags = raw_item.get("tags")
                if isinstance(raw_tags, list) and raw_tags:
                    # Must not bind name `tags` here — it would shadow TagStore closed over by _persist_chunk.
                    tag_names: list[str] = []
                    seen_tags: set[str] = set()
                    for v in raw_tags[:128]:
                        name = str(v or "").strip()
                        if not name or name in seen_tags:
                            continue
                        seen_tags.add(name)
                        tag_names.append(name)
                        if len(tag_names) >= 64:
                            break
                    if tag_names:
                        chunk_tags[key] = tag_names

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
                    tags_by_key=dict(chunk_tags),
                )
                success = success_after
                chunk_rows.clear()
                chunk_keys.clear()
                chunk_tags.clear()
        else:
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
                tags_by_key=dict(chunk_tags) if chunk_tags else None,
            )
            success = success_after
            chunk_rows.clear()
            chunk_keys.clear()
            chunk_tags.clear()

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
                            new_pending_job(
                                type="hydrate_metadata",
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
