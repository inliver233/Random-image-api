#!/usr/bin/env python3
"""Legacy catalog migration helpers (F3 / D2).

Supports two common takeover paths for Random-image-api:

1. **Same-schema SQLite** (e.g. old random-mage Python / prior new-stack file):
   file-level copy is preferred; this script can **verify** counts after copy.

2. **Legacy Postgres (Node / Prisma-style images+imports CSV or live PG)**:
   load `images` (+ optional `imports`, `tags`, `image_tags`) into an empty target
   SQLite or Postgres catalog after `alembic upgrade head`.

Tokens / proxy passwords are **encrypted** with the target's
`FIELD_ENCRYPTION_KEY`. Prefer reusing the old key when copying a same-schema
SQLite that already holds `*_enc` columns; otherwise import plain refresh tokens
via `--tokens-json`.

Does **not** migrate: jobs history, pg-boss, admin_audit, request_logs, runtime_settings,
Memcached, CF runtime overlays.

Examples
--------
# Verify after copying old app.db → data/app.db
python scripts/legacy/migrate_legacy_catalog.py verify \\
  --target-url "sqlite+aiosqlite:///./data/app.db"

# Import Node-export CSVs into empty target (run alembic first)
python scripts/legacy/migrate_legacy_catalog.py import-csv \\
  --images-csv ./backups/images.csv \\
  --imports-csv ./backups/imports.csv \\
  --target-url "sqlite+aiosqlite:///./data/app.db"

# Import plain refresh tokens (env REFRESH_TOKENS shape or simple list)
python scripts/legacy/migrate_legacy_catalog.py import-tokens \\
  --tokens-json ./tokens.json \\
  --field-encryption-key "$FIELD_ENCRYPTION_KEY" \\
  --target-url "sqlite+aiosqlite:///./data/app.db"

# Live PG → target (requires psycopg installed for source)
python scripts/legacy/migrate_legacy_catalog.py import-pg \\
  --source-url "postgresql://user:pass@127.0.0.1:5432/pixivcat" \\
  --target-url "postgresql+asyncpg://ria:ria@127.0.0.1:5432/random_image"
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


def _sync_url(url: str) -> str:
    u = (url or "").strip()
    for needle in ("+aiosqlite", "+asyncpg", "+psycopg"):
        u = u.replace(needle, "")
    if u.startswith("postgres://"):
        u = "postgresql://" + u[len("postgres://") :]
    return u


def _connect(url: str):
    sync = _sync_url(url)
    try:
        from sqlalchemy import create_engine
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("sqlalchemy required: pip install sqlalchemy") from exc
    return create_engine(sync, future=True)


def _table_count(conn, table: str) -> int:
    from sqlalchemy import text

    return int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())


def cmd_verify(args: argparse.Namespace) -> int:
    engine = _connect(args.target_url)
    tables = [
        "images",
        "imports",
        "tags",
        "image_tags",
        "pixiv_tokens",
        "proxy_endpoints",
        "jobs",
    ]
    print(f"target={_sync_url(args.target_url)}")
    with engine.connect() as conn:
        for t in tables:
            try:
                n = _table_count(conn, t)
                print(f"  {t}: {n}")
            except Exception as exc:
                print(f"  {t}: ERROR {exc}")
        # Cold-start risk signal (D3)
        try:
            from sqlalchemy import text

            total = _table_count(conn, "images")
            unknown = int(
                conn.execute(
                    text("SELECT COUNT(*) FROM images WHERE x_restrict IS NULL AND status = 1")
                ).scalar_one()
            )
            if total > 0 and unknown / total >= 0.5:
                print(
                    "WARN: ≥50% enabled images have x_restrict NULL — "
                    "default r18_strict=1 will NO_MATCH. Set admin "
                    "random.defaults.default_r18_strict=false or hydrate metadata."
                )
            print(f"  images.x_restrict_null (status=1): {unknown}/{total}")
        except Exception as exc:
            print(f"  x_restrict check: ERROR {exc}")
    return 0


def _clamp_random_key(v: Any) -> float:
    try:
        x = float(v)
    except Exception:
        x = random.random()
    if not (0.0 <= x < 1.0):
        x = random.random()
    return x


def _nullish(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


def _int_or_none(v: Any) -> int | None:
    v = _nullish(v)
    if v is None:
        return None
    try:
        return int(float(v))
    except Exception:
        return None


def _str_or_none(v: Any) -> str | None:
    v = _nullish(v)
    if v is None:
        return None
    return str(v)


def _row_from_images_csv(row: dict[str, str]) -> dict[str, Any]:
    # Accept both new-stack and loose Node export headers.
    illust_id = _int_or_none(row.get("illust_id") or row.get("illustId"))
    page_index = _int_or_none(row.get("page_index") or row.get("pageIndex") or 0)
    if illust_id is None or page_index is None:
        raise ValueError("illust_id/page_index required")
    ext = str(row.get("ext") or "jpg").lstrip(".").lower() or "jpg"
    original_url = str(row.get("original_url") or row.get("originalUrl") or "").strip()
    proxy_path = str(row.get("proxy_path") or row.get("proxyPath") or "").strip()
    if not original_url and not proxy_path:
        raise ValueError("original_url or proxy_path required")
    if not proxy_path and original_url:
        # Best-effort synthetic path; delivery may heal later.
        proxy_path = f"/i/{illust_id}_{page_index}.{ext}"
    if not original_url:
        original_url = f"https://i.pximg.net{proxy_path}" if proxy_path.startswith("/") else proxy_path

    out: dict[str, Any] = {
        "illust_id": illust_id,
        "page_index": page_index,
        "ext": ext,
        "original_url": original_url,
        "proxy_path": proxy_path,
        "random_key": _clamp_random_key(row.get("random_key") or row.get("randomKey")),
        "width": _int_or_none(row.get("width")),
        "height": _int_or_none(row.get("height")),
        "aspect_ratio": None,
        "orientation": _int_or_none(row.get("orientation")),
        "x_restrict": _int_or_none(row.get("x_restrict") or row.get("xRestrict")),
        "ai_type": _int_or_none(row.get("ai_type") or row.get("aiType")),
        "illust_type": _int_or_none(row.get("illust_type") or row.get("illustType")),
        "user_id": _int_or_none(row.get("user_id") or row.get("userId")),
        "user_name": _str_or_none(row.get("user_name") or row.get("userName")),
        "title": _str_or_none(row.get("title")),
        "created_at_pixiv": _str_or_none(row.get("created_at_pixiv") or row.get("createdAtPixiv")),
        "bookmark_count": _int_or_none(row.get("bookmark_count")),
        "view_count": _int_or_none(row.get("view_count")),
        "comment_count": _int_or_none(row.get("comment_count")),
        "status": _int_or_none(row.get("status")) or 1,
        "fail_count": _int_or_none(row.get("fail_count")) or 0,
        "created_import_id": _int_or_none(row.get("created_import_id")),
    }
    w, h = out["width"], out["height"]
    if w and h and h != 0:
        try:
            out["aspect_ratio"] = float(w) / float(h)
        except Exception:
            out["aspect_ratio"] = None
    return out


def cmd_import_csv(args: argparse.Namespace) -> int:
    from sqlalchemy import text

    engine = _connect(args.target_url)
    images_path = Path(args.images_csv)
    if not images_path.is_file():
        print(f"missing images csv: {images_path}", file=sys.stderr)
        return 2

    imports_inserted = 0
    images_inserted = 0
    bad = 0
    batch: list[dict[str, Any]] = []
    batch_size = max(50, int(args.batch_size))

    insert_sql = text(
        """
        INSERT INTO images (
          illust_id, page_index, ext, original_url, proxy_path, random_key,
          width, height, aspect_ratio, orientation, x_restrict, ai_type, illust_type,
          user_id, user_name, title, created_at_pixiv,
          bookmark_count, view_count, comment_count,
          status, fail_count, created_import_id
        ) VALUES (
          :illust_id, :page_index, :ext, :original_url, :proxy_path, :random_key,
          :width, :height, :aspect_ratio, :orientation, :x_restrict, :ai_type, :illust_type,
          :user_id, :user_name, :title, :created_at_pixiv,
          :bookmark_count, :view_count, :comment_count,
          :status, :fail_count, :created_import_id
        )
        ON CONFLICT DO NOTHING
        """
    )
    # SQLite may not support ON CONFLICT DO NOTHING without constraint name —
    # fall back to plain insert and ignore unique failures.

    with engine.begin() as conn:
        if args.imports_csv:
            ip = Path(args.imports_csv)
            if ip.is_file():
                with ip.open("r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        try:
                            total = _int_or_none(row.get("total")) or 0
                            success = _int_or_none(row.get("success")) or 0
                            failed = _int_or_none(row.get("failed")) or 0
                            accepted = _int_or_none(row.get("accepted"))
                            if accepted is None:
                                accepted = success
                            detail = row.get("detail_json") or row.get("detail")
                            if detail is not None and not isinstance(detail, str):
                                detail = json.dumps(detail, ensure_ascii=False)
                            conn.execute(
                                text(
                                    """
                                    INSERT INTO imports (
                                      created_by, source, total, accepted, success, failed, detail_json
                                    ) VALUES (
                                      :created_by, :source, :total, :accepted, :success, :failed, :detail_json
                                    )
                                    """
                                ),
                                {
                                    "created_by": _str_or_none(row.get("created_by")),
                                    "source": _str_or_none(row.get("source")) or "legacy_migrate",
                                    "total": total,
                                    "accepted": accepted,
                                    "success": success,
                                    "failed": failed,
                                    "detail_json": _str_or_none(detail),
                                },
                            )
                            imports_inserted += 1
                        except Exception as exc:
                            bad += 1
                            if args.verbose:
                                print(f"import row skip: {exc}", file=sys.stderr)

        with images_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    mapped = _row_from_images_csv(row)
                except Exception as exc:
                    bad += 1
                    if args.verbose:
                        print(f"image map skip: {exc}", file=sys.stderr)
                    continue
                batch.append(mapped)
                if len(batch) >= batch_size:
                    images_inserted += _flush_images(conn, insert_sql, batch, verbose=args.verbose)
                    batch.clear()
            if batch:
                images_inserted += _flush_images(conn, insert_sql, batch, verbose=args.verbose)

    print(
        f"import-csv done: imports={imports_inserted} images={images_inserted} bad_rows≈{bad} "
        f"target={_sync_url(args.target_url)}"
    )
    print("Next: run verify; if x_restrict mostly NULL, set default_r18_strict=false before public cutover.")
    return 0


def _flush_images(conn, insert_sql, batch: list[dict[str, Any]], *, verbose: bool) -> int:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    n = 0
    for row in batch:
        try:
            conn.execute(insert_sql, row)
            n += 1
        except Exception:
            try:
                # Plain insert without ON CONFLICT for older SQLite dialects.
                plain = text(
                    """
                    INSERT INTO images (
                      illust_id, page_index, ext, original_url, proxy_path, random_key,
                      width, height, aspect_ratio, orientation, x_restrict, ai_type, illust_type,
                      user_id, user_name, title, created_at_pixiv,
                      bookmark_count, view_count, comment_count,
                      status, fail_count, created_import_id
                    ) VALUES (
                      :illust_id, :page_index, :ext, :original_url, :proxy_path, :random_key,
                      :width, :height, :aspect_ratio, :orientation, :x_restrict, :ai_type, :illust_type,
                      :user_id, :user_name, :title, :created_at_pixiv,
                      :bookmark_count, :view_count, :comment_count,
                      :status, :fail_count, :created_import_id
                    )
                    """
                )
                conn.execute(plain, row)
                n += 1
            except IntegrityError:
                pass
            except Exception as exc:
                if verbose:
                    print(f"insert skip: {exc}", file=sys.stderr)
    return n


def cmd_import_pg(args: argparse.Namespace) -> int:
    """Stream images (+ optional tags) from a live Postgres source into target."""
    from sqlalchemy import text

    src = _connect(args.source_url)
    dst = _connect(args.target_url)
    batch_size = max(50, int(args.batch_size))
    images_inserted = 0
    bad = 0

    select_sql = text(
        """
        SELECT
          illust_id, page_index, ext, original_url, proxy_path, random_key,
          width, height, aspect_ratio, orientation, x_restrict, ai_type, illust_type,
          user_id, user_name, title, created_at_pixiv,
          bookmark_count, view_count, comment_count, status, fail_count, created_import_id
        FROM images
        ORDER BY id
        """
    )
    # Node schema may use camelCase — try fallback query.
    select_fallback = text("SELECT * FROM images ORDER BY id")

    insert_sql = text(
        """
        INSERT INTO images (
          illust_id, page_index, ext, original_url, proxy_path, random_key,
          width, height, aspect_ratio, orientation, x_restrict, ai_type, illust_type,
          user_id, user_name, title, created_at_pixiv,
          bookmark_count, view_count, comment_count,
          status, fail_count, created_import_id
        ) VALUES (
          :illust_id, :page_index, :ext, :original_url, :proxy_path, :random_key,
          :width, :height, :aspect_ratio, :orientation, :x_restrict, :ai_type, :illust_type,
          :user_id, :user_name, :title, :created_at_pixiv,
          :bookmark_count, :view_count, :comment_count,
          :status, :fail_count, :created_import_id
        )
        """
    )

    with src.connect() as sconn, dst.begin() as dconn:
        try:
            result = sconn.execute(select_sql)
            cols = list(result.keys())
            rows_iter: Iterable[Any] = result
            use_map = True
        except Exception:
            result = sconn.execute(select_fallback)
            cols = list(result.keys())
            rows_iter = result
            use_map = False

        batch: list[dict[str, Any]] = []
        for raw in rows_iter:
            mapping = dict(zip(cols, raw)) if not hasattr(raw, "_mapping") else dict(raw._mapping)
            try:
                if use_map and "illust_id" in mapping:
                    row = {
                        "illust_id": int(mapping["illust_id"]),
                        "page_index": int(mapping.get("page_index") or 0),
                        "ext": str(mapping.get("ext") or "jpg"),
                        "original_url": str(mapping.get("original_url") or ""),
                        "proxy_path": str(mapping.get("proxy_path") or ""),
                        "random_key": _clamp_random_key(mapping.get("random_key")),
                        "width": _int_or_none(mapping.get("width")),
                        "height": _int_or_none(mapping.get("height")),
                        "aspect_ratio": mapping.get("aspect_ratio"),
                        "orientation": _int_or_none(mapping.get("orientation")),
                        "x_restrict": _int_or_none(mapping.get("x_restrict")),
                        "ai_type": _int_or_none(mapping.get("ai_type")),
                        "illust_type": _int_or_none(mapping.get("illust_type")),
                        "user_id": _int_or_none(mapping.get("user_id")),
                        "user_name": _str_or_none(mapping.get("user_name")),
                        "title": _str_or_none(mapping.get("title")),
                        "created_at_pixiv": _str_or_none(
                            mapping.get("created_at_pixiv")
                            if mapping.get("created_at_pixiv") is not None
                            else str(mapping.get("created_at_pixiv") or "") or None
                        ),
                        "bookmark_count": _int_or_none(mapping.get("bookmark_count")),
                        "view_count": _int_or_none(mapping.get("view_count")),
                        "comment_count": _int_or_none(mapping.get("comment_count")),
                        "status": _int_or_none(mapping.get("status")) or 1,
                        "fail_count": _int_or_none(mapping.get("fail_count")) or 0,
                        "created_import_id": _int_or_none(mapping.get("created_import_id")),
                    }
                else:
                    row = _row_from_images_csv({k: "" if v is None else str(v) for k, v in mapping.items()})
            except Exception as exc:
                bad += 1
                if args.verbose:
                    print(f"map skip: {exc}", file=sys.stderr)
                continue
            if not row.get("original_url") or not row.get("proxy_path"):
                bad += 1
                continue
            batch.append(row)
            if len(batch) >= batch_size:
                images_inserted += _flush_images(dconn, insert_sql, batch, verbose=args.verbose)
                batch.clear()
        if batch:
            images_inserted += _flush_images(dconn, insert_sql, batch, verbose=args.verbose)

    print(f"import-pg done: images={images_inserted} bad≈{bad}")
    return 0


def cmd_import_tokens(args: argparse.Namespace) -> int:
    from sqlalchemy import text

    key = (args.field_encryption_key or os.environ.get("FIELD_ENCRYPTION_KEY") or "").strip()
    if not key:
        print("FIELD_ENCRYPTION_KEY required", file=sys.stderr)
        return 2

    # Import crypto without requiring full app package path when run from repo root.
    repo_root = Path(__file__).resolve().parents[2]
    backend = repo_root / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from app.core.crypto import FieldEncryptor, mask_secret  # type: ignore

    encryptor = FieldEncryptor.from_key(key)
    path = Path(args.tokens_json)
    raw = json.loads(path.read_text(encoding="utf-8"))
    items: list[dict[str, Any]]
    if isinstance(raw, list):
        items = []
        for i, el in enumerate(raw):
            if isinstance(el, str):
                items.append({"refresh_token": el, "label": f"legacy-{i+1}"})
            elif isinstance(el, dict):
                items.append(el)
    elif isinstance(raw, dict) and "tokens" in raw:
        items = list(raw["tokens"])
    else:
        print("tokens-json must be a list or {tokens:[...]}", file=sys.stderr)
        return 2

    engine = _connect(args.target_url)
    n = 0
    with engine.begin() as conn:
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            token = str(item.get("refresh_token") or item.get("token") or "").strip()
            if not token:
                continue
            label = _str_or_none(item.get("label")) or f"legacy-{i+1}"
            enabled = 1 if item.get("enabled", True) else 0
            weight = float(item.get("weight") or 1.0)
            enc = encryptor.encrypt_text(token)
            masked = mask_secret(token)
            conn.execute(
                text(
                    """
                    INSERT INTO pixiv_tokens (
                      label, enabled, refresh_token_enc, refresh_token_masked, weight
                    ) VALUES (
                      :label, :enabled, :enc, :masked, :weight
                    )
                    """
                ),
                {
                    "label": label,
                    "enabled": enabled,
                    "enc": enc,
                    "masked": masked,
                    "weight": weight,
                },
            )
            n += 1
    print(f"import-tokens done: {n} tokens (encrypted with target key)")
    return 0


def cmd_copy_sqlite(args: argparse.Namespace) -> int:
    """File-level copy for same-schema SQLite (preferred mage takeover)."""
    import shutil

    src = Path(args.source_db)
    dst = Path(args.target_db)
    if not src.is_file():
        print(f"missing source: {src}", file=sys.stderr)
        return 2
    if dst.exists() and not args.force:
        print(f"target exists (use --force): {dst}", file=sys.stderr)
        return 2
    dst.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(src) + suffix) if suffix else src
        if p.is_file():
            out = Path(str(dst) + suffix) if suffix else dst
            shutil.copy2(p, out)
            print(f"copied {p} → {out}")
    print("Also copy FIELD_ENCRYPTION_KEY / field_encryption_key file with the DB.")
    print("Then: alembic upgrade head (if needed) + verify.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Legacy catalog migration (F3)")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("verify", help="Count tables + cold-start x_restrict warning")
    v.add_argument("--target-url", required=True, help="SQLAlchemy URL (async drivers stripped)")
    v.set_defaults(func=cmd_verify)

    c = sub.add_parser("copy-sqlite", help="Same-schema SQLite file copy")
    c.add_argument("--source-db", required=True)
    c.add_argument("--target-db", required=True)
    c.add_argument("--force", action="store_true")
    c.set_defaults(func=cmd_copy_sqlite)

    ic = sub.add_parser("import-csv", help="Import images/imports CSV into target")
    ic.add_argument("--images-csv", required=True)
    ic.add_argument("--imports-csv", default="")
    ic.add_argument("--target-url", required=True)
    ic.add_argument("--batch-size", type=int, default=500)
    ic.add_argument("--verbose", action="store_true")
    ic.set_defaults(func=cmd_import_csv)

    ip = sub.add_parser("import-pg", help="Stream images from live Postgres source")
    ip.add_argument("--source-url", required=True)
    ip.add_argument("--target-url", required=True)
    ip.add_argument("--batch-size", type=int, default=500)
    ip.add_argument("--verbose", action="store_true")
    ip.set_defaults(func=cmd_import_pg)

    it = sub.add_parser("import-tokens", help="Import plain refresh tokens (encrypt into pixiv_tokens)")
    it.add_argument("--tokens-json", required=True)
    it.add_argument("--target-url", required=True)
    it.add_argument("--field-encryption-key", default="")
    it.set_defaults(func=cmd_import_tokens)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
