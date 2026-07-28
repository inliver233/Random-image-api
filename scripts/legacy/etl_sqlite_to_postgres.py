#!/usr/bin/env python3
"""Staged SQLite → PostgreSQL catalog ETL (H7).

Copies the full relational catalog with preserved primary keys so every
foreign key stays valid:

    imports → images → import_images → tags → image_tags
    → pixiv_tokens → proxy_pools → proxy_endpoints
    → proxy_pool_endpoints → token_proxy_bindings

Properties the old migrate_legacy_catalog.py lacked:

- Column lists come from the ORM metadata (``Base.metadata``), so the tool
  cannot drift from the schema migrations create.
- Batched keyset pagination (single or composite PK) — no full-table load.
- PostgreSQL writes use asyncpg binary COPY per batch; a SQLite target is
  supported for rehearsal and tests (same pipeline, executemany sink).
- Checkpoint file after every batch → interrupted runs resume exactly.
- Source orphan preflight (SQLite rarely enforces FKs): fail loud by
  default, or deterministically filter with --skip-orphans (counted).
- PG sequences are reset after load; ``validate`` compares per-table
  counts + PK checksums and re-checks orphans on the target. Known
  blind spot: non-PK column content is not compared — the coercers fail
  loud on lossy conversions instead.

Not migrated (deliberately): jobs (purgeable queue), request_logs,
admin_audit, hydration_runs, runtime_settings, api_keys,
cf_worker_deployments — operational state that should be re-created on
the new instance, not carried over. pixiv_tokens/proxy passwords are
copied encrypted: the target MUST reuse the same FIELD_ENCRYPTION_KEY.

Examples
--------
# Real cutover (target schema must exist: alembic upgrade head first)
python scripts/legacy/etl_sqlite_to_postgres.py run \\
  --source-db ./data/app.db \\
  --target-url "postgresql+asyncpg://ria:ria@127.0.0.1:5432/random_image" \\
  --checkpoint ./data/etl-checkpoint.json

# Validate after (or independently of) a run
python scripts/legacy/etl_sqlite_to_postgres.py validate \\
  --source-db ./data/app.db \\
  --target-url "postgresql+asyncpg://ria:ria@127.0.0.1:5432/random_image"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_REPO = Path(__file__).resolve().parents[2]
_BACKEND = _REPO / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import sqlalchemy as sa  # noqa: E402

from app.db.models.base import Base  # noqa: E402

# FK-safe copy order. Parents strictly before children.
TABLE_ORDER: tuple[str, ...] = (
    "imports",
    "images",
    "import_images",
    "tags",
    "image_tags",
    "pixiv_tokens",
    "proxy_pools",
    "proxy_endpoints",
    "proxy_pool_endpoints",
    "token_proxy_bindings",
)


def _table(name: str) -> sa.Table:
    return Base.metadata.tables[name]


def _derive_orphan_checks() -> tuple[tuple[str, str, str, str, bool], ...]:
    """(child, child_col, parent, parent_col, nullable) for every FK edge,
    derived from ORM metadata so the list cannot drift from the schema."""
    known = set(TABLE_ORDER)
    out: list[tuple[str, str, str, str, bool]] = []
    for t in TABLE_ORDER:
        for col in _table(t).columns:
            for fk in col.foreign_keys:
                parent = fk.column.table.name
                if parent not in known:
                    raise RuntimeError(
                        f"FK {t}.{col.name} references {parent}, which is not in TABLE_ORDER"
                    )
                out.append((t, col.name, parent, fk.column.name, bool(col.nullable)))
    return tuple(out)


ORPHAN_CHECKS = _derive_orphan_checks()

DEFAULT_BATCH_SIZE = 5000


def table_columns(name: str) -> list[str]:
    return [c.name for c in _table(name).columns]


def table_pk(name: str) -> list[str]:
    return [c.name for c in _table(name).primary_key.columns]


def has_serial_id(name: str) -> bool:
    pk = table_pk(name)
    return pk == ["id"]


def _coercer_for(column: sa.Column) -> Callable[[Any], Any]:
    """SQLite is loosely typed; asyncpg is not. Coerce per ORM column type."""
    ctype = column.type

    def _to_int(v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, float) and not v.is_integer():
            raise ValueError(f"non-integral value {v!r} in integer column {column.name!r}")
        return int(v)

    def _to_float(v: Any) -> Any:
        return None if v is None else float(v)

    def _to_str(v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, bytes):
            # SQLite BLOB in a Text column: str(bytes) would silently store
            # the b'…' repr — decode or fail loud instead.
            return v.decode("utf-8")
        return str(v)

    def _to_bool(v: Any) -> Any:
        return None if v is None else bool(v)

    if isinstance(ctype, sa.Boolean):
        return _to_bool
    if isinstance(ctype, (sa.Integer, sa.BigInteger, sa.SmallInteger)):
        return _to_int
    if isinstance(ctype, (sa.Float, sa.Numeric)):
        return _to_float
    if isinstance(ctype, (sa.Text, sa.String)):
        return _to_str
    return lambda v: v


def row_coercers(name: str) -> list[Callable[[Any], Any]]:
    return [_coercer_for(c) for c in _table(name).columns]


# --------------------------------------------------------------------------
# Source (read-only SQLite)
# --------------------------------------------------------------------------


def open_source(source_db: str | Path) -> sqlite3.Connection:
    path = Path(source_db)
    if not path.is_file():
        raise SystemExit(f"source db not found: {path}")
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _keyset_where(pk: list[str], last_key: list[Any] | None) -> tuple[str, list[Any]]:
    if not last_key:
        return "", []
    if len(pk) == 1:
        return f"WHERE {pk[0]} > ?", [last_key[0]]
    # Row-value comparison: index seek on both SQLite (3.15+) and PostgreSQL.
    # The OR-expanded form degrades to a full index scan per batch (O(n²)
    # over 4.2M image_tags rows).
    cols = ", ".join(pk)
    marks = ", ".join("?" for _ in pk)
    return f"WHERE ({cols}) > ({marks})", list(last_key)


def read_batch(
    src: sqlite3.Connection,
    table: str,
    *,
    last_key: list[Any] | None,
    batch_size: int,
) -> list[sqlite3.Row]:
    cols = ", ".join(table_columns(table))
    pk = table_pk(table)
    where, params = _keyset_where(pk, last_key)
    order = ", ".join(pk)
    sql = f"SELECT {cols} FROM {table} {where} ORDER BY {order} LIMIT {int(batch_size)}"
    return list(src.execute(sql, params).fetchall())


def source_count(src: sqlite3.Connection, table: str) -> int:
    return int(src.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def source_orphans(src: sqlite3.Connection) -> dict[str, int]:
    """Rows whose FK target does not exist in the source (SQLite often has
    foreign_keys=OFF, so these are real-world occurrences, and PostgreSQL
    would reject them mid-COPY)."""
    out: dict[str, int] = {}
    for child, col, parent, pcol, nullable in ORPHAN_CHECKS:
        null_guard = f"{child}.{col} IS NOT NULL AND " if nullable else ""
        sql = (
            f"SELECT COUNT(*) FROM {child} "
            f"LEFT JOIN {parent} ON {child}.{col} = {parent}.{pcol} "
            f"WHERE {null_guard}{parent}.{pcol} IS NULL"
        )
        n = int(src.execute(sql).fetchone()[0])
        if n:
            out[f"{child}.{col}→{parent}.{pcol}"] = n
    return out


def _orphan_transform(
    src: sqlite3.Connection, table: str
) -> Callable[[list[Any]], str | None] | None:
    """--skip-orphans row transform.

    Dangling NULLABLE FKs are nulled out (dropping e.g. a whole image because
    its created_import_id audit pointer dangles would cascade new orphans
    into image_tags/import_images and abort the PG COPY mid-run). Rows are
    dropped only when a NOT NULL FK dangles — those rows are unrepresentable
    on the target. Returns None (drop), "nulled", or "ok".
    """
    checks = [c for c in ORPHAN_CHECKS if c[0] == table]
    if not checks:
        return None
    idx = {name: i for i, name in enumerate(table_columns(table))}
    parents: dict[tuple[str, str], set[Any]] = {}
    for _, _, parent, pcol, _ in checks:
        key = (parent, pcol)
        if key not in parents:
            parents[key] = {r[0] for r in src.execute(f"SELECT {pcol} FROM {parent}")}

    def _apply(values: list[Any]) -> str | None:
        status = "ok"
        for _, col, parent, pcol, nullable in checks:
            v = values[idx[col]]
            if v is None:
                if nullable:
                    continue
                return None  # NULL in a NOT NULL FK: unrepresentable, drop.
            if v not in parents[(parent, pcol)]:
                if nullable:
                    values[idx[col]] = None
                    status = "nulled"
                else:
                    return None
        return status

    return _apply


# --------------------------------------------------------------------------
# Sinks
# --------------------------------------------------------------------------


def _delete_after_sql(table: str, last_key: list[Any] | None) -> tuple[str, list[Any]]:
    """Repair SQL for the crash window between a committed batch and its
    checkpoint save: drop target rows past the checkpoint so the batch is
    re-copied exactly once (COPY has no upsert)."""
    pk = table_pk(table)
    if not last_key:
        return f"DELETE FROM {table}", []
    if len(pk) == 1:
        return f"DELETE FROM {table} WHERE {pk[0]} > ?", [last_key[0]]
    a, b = pk
    return (
        f"DELETE FROM {table} WHERE ({a} > ?) OR ({a} = ? AND {b} > ?)",
        [last_key[0], last_key[0], last_key[1]],
    )


class SqliteSink:
    """Rehearsal/test sink: same pipeline, executemany into a fresh SQLite."""

    def __init__(self, target_path: str) -> None:
        self._conn = sqlite3.connect(target_path)

    async def write(self, table: str, rows: list[tuple[Any, ...]]) -> None:
        cols = table_columns(table)
        placeholders = ", ".join("?" for _ in cols)
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
        self._conn.executemany(sql, rows)
        self._conn.commit()

    async def delete_after(self, table: str, last_key: list[Any] | None) -> int:
        sql, params = _delete_after_sql(table, last_key)
        cur = self._conn.execute(sql, params)
        self._conn.commit()
        return int(cur.rowcount or 0)

    async def count(self, table: str) -> int:
        return int(self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    async def reset_sequences(self) -> None:
        # SQLite AUTOINCREMENT follows max(rowid) automatically.
        return None

    async def close(self) -> None:
        self._conn.close()


class PostgresSink:
    """asyncpg binary COPY per batch."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: Any = None

    async def _ensure(self) -> Any:
        if self._conn is None:
            import asyncpg

            self._conn = await asyncpg.connect(self._dsn)
        return self._conn

    async def write(self, table: str, rows: list[tuple[Any, ...]]) -> None:
        conn = await self._ensure()
        await conn.copy_records_to_table(table, records=rows, columns=table_columns(table))

    async def delete_after(self, table: str, last_key: list[Any] | None) -> int:
        conn = await self._ensure()
        sql, params = _delete_after_sql(table, last_key)
        # asyncpg uses $n placeholders.
        for i in range(len(params)):
            sql = sql.replace("?", f"${i + 1}", 1)
        status = await conn.execute(sql, *params)
        try:
            return int(str(status).split()[-1])
        except Exception:
            return 0

    async def count(self, table: str) -> int:
        conn = await self._ensure()
        return int(await conn.fetchval(f"SELECT COUNT(*) FROM {table}") or 0)

    async def reset_sequences(self) -> None:
        conn = await self._ensure()
        for table in TABLE_ORDER:
            if not has_serial_id(table):
                continue
            await conn.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
            )

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None


def _pg_dsn(url: str) -> str:
    u = (url or "").strip()
    for needle in ("+asyncpg", "+psycopg"):
        u = u.replace(needle, "")
    return u


def build_sink(target_url: str) -> SqliteSink | PostgresSink:
    u = (target_url or "").strip()
    low = u.lower()
    if low.startswith("postgres"):
        return PostgresSink(_pg_dsn(u))
    if low.startswith("sqlite"):
        path = u.split("///", 1)[-1]
        return SqliteSink(path)
    raise SystemExit(f"unsupported target url: {target_url}")


# --------------------------------------------------------------------------
# Checkpoint
# --------------------------------------------------------------------------


@dataclass
class Checkpoint:
    path: Path
    source: str
    tables: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load_or_create(cls, path: str | Path, *, source: str) -> "Checkpoint":
        p = Path(path)
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if str(data.get("source")) != str(source):
                raise SystemExit(
                    f"checkpoint {p} belongs to source {data.get('source')!r}, not {source!r}; "
                    "delete it to start a fresh run"
                )
            return cls(path=p, source=str(source), tables=dict(data.get("tables") or {}))
        return cls(path=p, source=str(source))

    def state(self, table: str) -> dict[str, Any]:
        return self.tables.setdefault(
            table,
            {
                "last_key": None,
                "rows_copied": 0,
                "orphans_skipped": 0,
                "orphans_nulled": 0,
                "done": False,
            },
        )

    def save(self) -> None:
        payload = {"source": self.source, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tables": self.tables}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)


# --------------------------------------------------------------------------
# Run / validate
# --------------------------------------------------------------------------


async def etl_run(
    *,
    source_db: str,
    target_url: str,
    checkpoint_path: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    skip_orphans: bool = False,
    allow_wipe: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    src = open_source(source_db)
    try:
        orphans = source_orphans(src)
        if orphans and not skip_orphans:
            raise SystemExit(
                "source contains orphan foreign keys (PostgreSQL would reject them): "
                + json.dumps(orphans, ensure_ascii=False)
                + " — fix the source or rerun with --skip-orphans"
            )

        # Resolved path as identity: './data/app.db' and an absolute spelling
        # of the same file must resume the same checkpoint.
        source_id = str(Path(source_db).resolve())
        ckpt_file_existed = Path(checkpoint_path).is_file()
        ckpt = Checkpoint.load_or_create(checkpoint_path, source=source_id)
        sink = build_sink(target_url)
        try:
            if not ckpt_file_existed:
                # Brand-new run: refuse to silently replace live data. Once the
                # run has begun (checkpoint file exists), leftover rows in a
                # fresh-looking table are OUR crash window and are repaired.
                non_empty = {}
                for t in TABLE_ORDER:
                    n = await sink.count(t)
                    if n:
                        non_empty[t] = n
                if non_empty and not allow_wipe:
                    raise SystemExit(
                        "target already has rows: "
                        + json.dumps(non_empty, ensure_ascii=False)
                        + " — point at an empty schema or rerun with --allow-wipe to replace them"
                    )
                ckpt.save()
            for table in TABLE_ORDER:
                state = ckpt.state(table)
                if state["done"]:
                    log(f"  {table}: already done ({state['rows_copied']} rows), skipping")
                    continue
                # Repair the crash window (a batch may have committed without
                # its checkpoint save) and, on --allow-wipe first runs, clear
                # pre-existing rows: drop everything past the checkpoint so
                # the copy is exactly-once.
                repaired = await sink.delete_after(table, state["last_key"])
                if repaired:
                    log(f"  {table}: removed {repaired} rows past the checkpoint before copying")
                pk = table_pk(table)
                coercers = row_coercers(table)
                transform = _orphan_transform(src, table) if skip_orphans else None
                total = source_count(src, table)
                while True:
                    batch = read_batch(
                        src, table, last_key=state["last_key"], batch_size=batch_size
                    )
                    if not batch:
                        state["done"] = True
                        ckpt.save()
                        break
                    rows: list[tuple[Any, ...]] = []
                    for r in batch:
                        values = list(tuple(r))
                        if transform is not None:
                            status = transform(values)
                            if status is None:
                                state["orphans_skipped"] += 1
                                continue
                            if status == "nulled":
                                state["orphans_nulled"] += 1
                        rows.append(tuple(f(v) for f, v in zip(coercers, values)))
                    if rows:
                        await sink.write(table, rows)
                    last = batch[-1]
                    state["last_key"] = [last[k] for k in pk]
                    state["rows_copied"] += len(rows)
                    ckpt.save()
                extras = ""
                if state["orphans_skipped"]:
                    extras += f", {state['orphans_skipped']} orphan rows skipped"
                if state["orphans_nulled"]:
                    extras += f", {state['orphans_nulled']} dangling nullable FKs nulled"
                log(f"  {table}: {state['rows_copied']}/{total} copied{extras}")
            await sink.reset_sequences()
        finally:
            await sink.close()

        return {
            "tables": {t: dict(ckpt.state(t)) for t in TABLE_ORDER},
            "source_orphans": orphans,
        }
    finally:
        src.close()


async def _target_scalar(target_url: str, sql: str) -> Any:
    low = (target_url or "").strip().lower()
    if low.startswith("postgres"):
        import asyncpg

        conn = await asyncpg.connect(_pg_dsn(target_url))
        try:
            return await conn.fetchval(sql)
        finally:
            await conn.close()
    path = target_url.split("///", 1)[-1]
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def _checksum_sql(table: str) -> str:
    pk = table_pk(table)
    # PK sum is a cheap order-independent content fingerprint given ids are
    # preserved; combined with COUNT it catches lost/duplicated/shifted rows.
    expr = " + ".join(f"COALESCE(SUM({c}), 0)" for c in pk)
    return f"SELECT {expr} FROM {table}"


async def etl_validate(
    *,
    source_db: str,
    target_url: str,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    src = open_source(source_db)
    try:
        report: dict[str, Any] = {"tables": {}, "target_orphans": {}, "ok": True}
        for table in TABLE_ORDER:
            s_count = source_count(src, table)
            s_sum = int(src.execute(_checksum_sql(table)).fetchone()[0] or 0)
            t_count = int(await _target_scalar(target_url, f"SELECT COUNT(*) FROM {table}") or 0)
            t_sum = int(await _target_scalar(target_url, _checksum_sql(table)) or 0)
            match = (s_count == t_count) and (s_sum == t_sum)
            report["tables"][table] = {
                "source_count": s_count,
                "target_count": t_count,
                "source_pk_sum": s_sum,
                "target_pk_sum": t_sum,
                "match": match,
            }
            if not match:
                report["ok"] = False
            log(
                f"  {table}: source={s_count} target={t_count} "
                f"pk_sum {'OK' if s_sum == t_sum else f'{s_sum}!={t_sum}'}"
                + ("" if match else "  ← MISMATCH")
            )

        for child, col, parent, pcol, nullable in ORPHAN_CHECKS:
            null_guard = f"{child}.{col} IS NOT NULL AND " if nullable else ""
            sql = (
                f"SELECT COUNT(*) FROM {child} "
                f"LEFT JOIN {parent} ON {child}.{col} = {parent}.{pcol} "
                f"WHERE {null_guard}{parent}.{pcol} IS NULL"
            )
            n = int(await _target_scalar(target_url, sql) or 0)
            if n:
                report["target_orphans"][f"{child}.{col}→{parent}.{pcol}"] = n
                report["ok"] = False
                log(f"  ORPHANS on target: {child}.{col}→{parent}.{pcol} = {n}")
        return report
    finally:
        src.close()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    summary = asyncio.run(
        etl_run(
            source_db=args.source_db,
            target_url=args.target_url,
            checkpoint_path=args.checkpoint,
            batch_size=max(100, int(args.batch_size)),
            skip_orphans=bool(args.skip_orphans),
            allow_wipe=bool(args.allow_wipe),
        )
    )
    copied = {t: s["rows_copied"] for t, s in summary["tables"].items()}
    print(f"etl run done: {json.dumps(copied, ensure_ascii=False)}")
    print("Next: `validate`, then reuse the SAME FIELD_ENCRYPTION_KEY on the target instance.")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    report = asyncio.run(etl_validate(source_db=args.source_db, target_url=args.target_url))
    print(f"validate: {'OK' if report['ok'] else 'FAILED'}")
    return 0 if report["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Staged SQLite → PostgreSQL catalog ETL (H7)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="Copy all catalog tables (resumable)")
    r.add_argument("--source-db", required=True, help="Path to the source SQLite file")
    r.add_argument("--target-url", required=True, help="postgresql+asyncpg://… (or sqlite:/// rehearsal)")
    r.add_argument("--checkpoint", required=True, help="Checkpoint JSON path (created if missing)")
    r.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    r.add_argument(
        "--skip-orphans",
        action="store_true",
        help="Null dangling NULLABLE FKs and drop rows with dangling NOT NULL FKs (counted per table)",
    )
    r.add_argument(
        "--allow-wipe",
        action="store_true",
        help="Allow replacing rows in non-empty target tables (fresh runs refuse otherwise)",
    )
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("validate", help="Compare counts/PK checksums and re-check orphans")
    v.add_argument("--source-db", required=True)
    v.add_argument("--target-url", required=True)
    v.set_defaults(func=cmd_validate)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
