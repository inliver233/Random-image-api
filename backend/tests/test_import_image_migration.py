from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


def test_import_image_migration_backfills_legacy_ownership_as_unverified(tmp_path: Path) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "import_image_migration.db"
    database_url = "sqlite+aiosqlite:///" + db_path.as_posix()
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        cfg = Config(str(backend_dir / "alembic.ini"))
        command.upgrade(cfg, "20260711_0019")

        with sqlite3.connect(db_path) as conn:
            conn.execute("INSERT INTO imports(id, created_by, source) VALUES(7, 'test', 'legacy')")
            conn.execute(
                """
INSERT INTO images(
  id, illust_id, page_index, ext, original_url, proxy_path, random_key, status, created_import_id
) VALUES(11, 123, 0, 'jpg', 'https://i.pximg.net/x/123_p0.jpg', '/i/11.jpg', 0.5, 1, 7)
""".strip()
            )
            conn.commit()

        command.upgrade(cfg, "head")
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT import_id, image_id, was_created, previous_status FROM import_images"
            ).fetchone()
        assert row == (7, 11, 0, None)

        command.downgrade(cfg, "20260711_0019")
        with sqlite3.connect(db_path) as conn:
            table_count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='import_images'"
            ).fetchone()
        assert table_count == (0,)
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
