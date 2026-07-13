from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


def test_cf_worker_deployment_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "cf_worker_deployment_migration.db"
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + db_path.as_posix()
    try:
        cfg = Config(str(backend_dir / "alembic.ini"))
        command.upgrade(cfg, "head")
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO cf_worker_deployments(account_id,worker_name,kind,state) VALUES(?,?,?,?)",
                ("acct", "same", "api", "intent"),
            )
            try:
                conn.execute(
                    "INSERT INTO cf_worker_deployments(account_id,worker_name,kind,state) VALUES(?,?,?,?)",
                    ("acct", "same", "image", "intent"),
                )
            except sqlite3.IntegrityError:
                pass
            else:
                raise AssertionError("cross-kind account/name uniqueness was not enforced")
        command.downgrade(cfg, "20260713_0020")
        with sqlite3.connect(db_path) as conn:
            exists = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='cf_worker_deployments'"
            ).fetchone()
        assert exists == (0,)
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
