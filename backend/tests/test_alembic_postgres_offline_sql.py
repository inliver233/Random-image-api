"""Offline alembic SQL for Postgres: no SQLite-only strftime/FTS5 in rendered DDL.

Does not require a live database. Complements dual-dialect UtcNow unit tests.
Live ``alembic upgrade head`` against empty Postgres remains an ops dry-run.
"""

from __future__ import annotations

import os
import sys
from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config


def test_alembic_upgrade_head_offline_sql_is_postgres_safe() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    ini = backend_dir / "alembic.ini"
    assert ini.is_file()

    prev = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = "postgresql://ria:ria@127.0.0.1:5432/random_image"
    try:
        cfg = Config(str(ini))
        buf = StringIO()
        old_out = sys.stdout
        sys.stdout = buf
        try:
            command.upgrade(cfg, "head", sql=True)
        finally:
            sys.stdout = old_out
        sql = buf.getvalue()
    finally:
        if prev is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev

    assert "CREATE TABLE" in sql
    assert "images" in sql
    assert "strftime" not in sql
    assert "fts5" not in sql.lower()
    assert "to_char" in sql
    assert "20260711_0019" in sql or "uq_jobs_active_opportunistic_hydrate" in sql
