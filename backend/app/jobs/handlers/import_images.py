from __future__ import annotations

import json
import random
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import load_settings
from app.core.data_files import get_sqlite_db_dir, resolve_file_ref
from app.core.pixiv_urls import parse_pixiv_original_url
from app.db.models.image_tags import ImageTag
from app.db.models.images import Image
from app.db.models.imports import Import
from app.db.models.jobs import JobRow
from app.db.models.tags import Tag
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


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except Exception:
        return None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _parse_pbd_ai_type(value: Any) -> int | None:
    raw = _as_int(value)
    if raw is None:
        return None
    # PixivBatchDownloader: 0 unknown, 1 non-ai, 2 ai
    if raw == 1:
        return 0
    if raw == 2:
        return 1
    return None


def _parse_pbd_illust_type(value: Any) -> int | None:
    raw = _as_int(value)
    if raw in {0, 1, 2}:
        return int(raw)
    return None


def _parse_pbd_created_at(value: Any) -> str | None:
    s = _as_str(value)
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


def _derive_orientation(width: int | None, height: int | None) -> tuple[float | None, int | None]:
    if width is None or height is None or width <= 0 or height <= 0:
        return None, None
    if width > height:
        orientation = 2
    elif height > width:
        orientation = 1
    else:
        orientation = 3
    return float(width) / float(height), orientation


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

        input_format_raw = str(payload.get("input_format") or "text").strip().lower()
        input_format = "pixiv_batch_downloader_json" if input_format_raw in {"pixiv_batch_downloader_json", "pbd_json", "pbd"} else "text"

        hydrate_on_import = _as_bool(payload.get("hydrate_on_import"), default=False)
        if input_format == "pixiv_batch_downloader_json":
            # PixivBatchDownloader export already contains most metadata;
            # keep this import token-free by default.
            hydrate_on_import = False
        file_path = _resolve_payload_file(payload)
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
                            "width": sa.case((stmt.excluded.width.is_not(None), stmt.excluded.width), else_=Image.width),
                            "height": sa.case((stmt.excluded.height.is_not(None), stmt.excluded.height), else_=Image.height),
                            "aspect_ratio": sa.case(
                                (stmt.excluded.aspect_ratio.is_not(None), stmt.excluded.aspect_ratio),
                                else_=Image.aspect_ratio,
                            ),
                            "orientation": sa.case(
                                (stmt.excluded.orientation.is_not(None), stmt.excluded.orientation),
                                else_=Image.orientation,
                            ),
                            "x_restrict": sa.case(
                                (stmt.excluded.x_restrict.is_not(None), stmt.excluded.x_restrict),
                                else_=Image.x_restrict,
                            ),
                            "ai_type": sa.case((stmt.excluded.ai_type.is_not(None), stmt.excluded.ai_type), else_=Image.ai_type),
                            "illust_type": sa.case(
                                (stmt.excluded.illust_type.is_not(None), stmt.excluded.illust_type),
                                else_=Image.illust_type,
                            ),
                            "user_id": sa.case((stmt.excluded.user_id.is_not(None), stmt.excluded.user_id), else_=Image.user_id),
                            "user_name": sa.case(
                                (stmt.excluded.user_name.is_not(None), stmt.excluded.user_name),
                                else_=Image.user_name,
                            ),
                            "title": sa.case((stmt.excluded.title.is_not(None), stmt.excluded.title), else_=Image.title),
                            "created_at_pixiv": sa.case(
                                (stmt.excluded.created_at_pixiv.is_not(None), stmt.excluded.created_at_pixiv),
                                else_=Image.created_at_pixiv,
                            ),
                            "bookmark_count": sa.case(
                                (stmt.excluded.bookmark_count.is_not(None), stmt.excluded.bookmark_count),
                                else_=Image.bookmark_count,
                            ),
                            "view_count": sa.case(
                                (stmt.excluded.view_count.is_not(None), stmt.excluded.view_count),
                                else_=Image.view_count,
                            ),
                            "comment_count": sa.case(
                                (stmt.excluded.comment_count.is_not(None), stmt.excluded.comment_count),
                                else_=Image.comment_count,
                            ),
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
                            tag_stmt = sqlite_insert(Tag).values([{"name": n, "translated_name": None} for n in names])
                            tag_stmt = tag_stmt.on_conflict_do_nothing(index_elements=["name"])
                            await session.execute(tag_stmt)

                            tag_rows = (
                                (await session.execute(sa.select(Tag.id, Tag.name).where(Tag.name.in_(names))))
                                .all()
                            )
                            tag_id_by_name = {str(name): int(tag_id) for (tag_id, name) in tag_rows}

                            img_rows = (
                                (
                                    await session.execute(
                                        sa.select(Image.id, Image.illust_id, Image.page_index).where(
                                            sa.tuple_(Image.illust_id, Image.page_index).in_(keys)
                                        )
                                    )
                                )
                                .all()
                            )
                            image_id_by_key = {(int(illust_id), int(page_index)): int(img_id) for (img_id, illust_id, page_index) in img_rows}

                            image_tag_rows: list[dict[str, Any]] = []
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
                                    image_tag_rows.append({"image_id": int(image_id), "tag_id": int(tag_id)})

                            if image_tag_rows:
                                for offset in range(0, len(image_tag_rows), 5000):
                                    sub = image_tag_rows[offset : offset + 5000]
                                    it_stmt = sqlite_insert(ImageTag).values(sub)
                                    it_stmt = it_stmt.on_conflict_do_nothing(index_elements=["image_id", "tag_id"])
                                    await session.execute(it_stmt)

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
                url = _as_str(raw_item.get("original"))
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

                width = _as_int(raw_item.get("fullWidth"))
                height = _as_int(raw_item.get("fullHeight"))
                if width is not None and width <= 0:
                    width = None
                if height is not None and height <= 0:
                    height = None

                aspect_ratio, orientation = _derive_orientation(width, height)
                x_restrict = _as_int(raw_item.get("xRestrict"))
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
                        "user_id": _as_int(raw_item.get("userId")),
                        "user_name": _as_str(raw_item.get("user")),
                        "title": _as_str(raw_item.get("title")),
                        "created_at_pixiv": _parse_pbd_created_at(raw_item.get("date")),
                        "bookmark_count": _as_int(raw_item.get("bmk")),
                        "view_count": _as_int(raw_item.get("viewCount")),
                        "comment_count": _as_int(raw_item.get("commentCount")),
                    }
                )

                raw_tags = raw_item.get("tags")
                if isinstance(raw_tags, list) and raw_tags:
                    tags: list[str] = []
                    seen_tags: set[str] = set()
                    for v in raw_tags[:128]:
                        name = str(v or "").strip()
                        if not name or name in seen_tags:
                            continue
                        seen_tags.add(name)
                        tags.append(name)
                        if len(tags) >= 64:
                            break
                    if tags:
                        chunk_tags[key] = tags

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
