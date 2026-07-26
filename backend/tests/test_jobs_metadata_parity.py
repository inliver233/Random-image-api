"""ORM/migration parity for the jobs partial unique index (M4/M5).

- M4: the ORM index declared only ``sqlite_where``; compiled for
  PostgreSQL it silently became an unconditional global unique index over
  (type, ref_type, ref_id), diverging from migration 0019's partial
  index. Both dialects must now compile the same predicate.
- M5: migration 0019 created the unique index without cleaning existing
  duplicate active opportunistic hydrates, so upgrading a pre-0019
  database with duplicates aborted (and with it the API startup chain).
  The migration now cancels all but the oldest duplicate first.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateIndex

from app.db.models.jobs import JobRow

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _index_ddl(dialect) -> str:
    index = next(
        idx for idx in JobRow.__table__.indexes if idx.name == "uq_jobs_active_opportunistic_hydrate"
    )
    return str(CreateIndex(index).compile(dialect=dialect))


def test_jobs_partial_unique_index_has_predicate_on_both_dialects() -> None:
    sqlite_ddl = _index_ddl(sqlite.dialect())
    pg_ddl = _index_ddl(postgresql.dialect())
    for ddl in (sqlite_ddl, pg_ddl):
        assert "WHERE" in ddl.upper(), ddl
        assert "opportunistic_hydrate" in ddl
        assert "pending" in ddl and "running" in ddl


def test_migration_0019_dedupes_active_jobs_before_unique_index(tmp_path: Path) -> None:
    import os

    db_path = tmp_path / "jobs_0019_dedupe.db"
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + db_path.as_posix()
    try:
        cfg = Config(str(BACKEND_DIR / "alembic.ini"))
        command.upgrade(cfg, "20260218_0018")

        with sqlite3.connect(db_path) as conn:
            for _ in range(3):
                conn.execute(
                    """
INSERT INTO jobs (type, status, priority, payload_json, ref_type, ref_id, created_at, updated_at)
VALUES ('hydrate_metadata', 'pending', 0, '{}', 'opportunistic_hydrate', '123',
        '2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z')
""".strip()
                )
            # A different illust must be untouched by the dedupe.
            conn.execute(
                """
INSERT INTO jobs (type, status, priority, payload_json, ref_type, ref_id, created_at, updated_at)
VALUES ('hydrate_metadata', 'running', 0, '{}', 'opportunistic_hydrate', '456',
        '2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z')
""".strip()
            )
            conn.commit()

        # Upgrading over duplicates used to abort on the unique index.
        command.upgrade(cfg, "head")

        with sqlite3.connect(db_path) as conn:
            active = conn.execute(
                """
SELECT ref_id, COUNT(*) FROM jobs
WHERE type='hydrate_metadata' AND ref_type='opportunistic_hydrate'
  AND status IN ('pending','running')
GROUP BY ref_id ORDER BY ref_id
""".strip()
            ).fetchall()
            assert active == [("123", 1), ("456", 1)]

            canceled = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='canceled' AND ref_id='123'"
            ).fetchone()
            assert canceled == (2,)

            survivor = conn.execute(
                """
SELECT MIN(id) FROM jobs
WHERE ref_id='123' AND status='pending'
""".strip()
            ).fetchone()
            # The oldest job (lowest id) survives.
            assert survivor == (1,)
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
