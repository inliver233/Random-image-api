"""Staged SQLite→PostgreSQL ETL (H7).

The pipeline (metadata-derived columns, FK-ordered tables, keyset batches,
checkpoint/resume, orphan preflight/filter, validation) is exercised end to
end against a fresh SQLite target created by ``alembic upgrade head`` —
identical code paths to the PostgreSQL run except the sink write and
sequence reset, which the TEST_POSTGRES_URL-gated test covers live.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "scripts" / "legacy"
if str(_REPO / "backend") not in sys.path:
    sys.path.insert(0, str(_REPO / "backend"))
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import etl_sqlite_to_postgres as etl  # noqa: E402

LIVE_PG_URL = (os.environ.get("TEST_POSTGRES_URL") or "").strip()


def _upgrade_head(db_path: Path) -> None:
    from alembic import command
    from alembic.config import Config

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + db_path.as_posix()
    try:
        cfg = Config(str(_REPO / "backend" / "alembic.ini"))
        command.upgrade(cfg, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def _seed_source(db_path: Path, *, with_orphans: bool = False) -> None:
    _upgrade_head(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO imports (id, source, total, accepted, success, failed) "
            "VALUES (1, 'legacy', 2, 2, 2, 0)"
        )
        for i in (1, 2, 3):
            conn.execute(
                "INSERT INTO images (id, illust_id, page_index, ext, original_url, proxy_path,"
                " random_key, status, fail_count, created_import_id) "
                "VALUES (?, ?, 0, 'jpg', ?, ?, ?, 1, 0, ?)",
                (i, 100 + i, f"https://i.pximg.net/{i}.jpg", f"/i/{i}.jpg", i / 10.0, 1 if i < 3 else None),
            )
        conn.execute("INSERT INTO import_images (import_id, image_id, was_created) VALUES (1, 1, 1)")
        conn.execute("INSERT INTO tags (id, name) VALUES (1, 'cat'), (2, 'dog')")
        conn.execute("INSERT INTO image_tags (image_id, tag_id) VALUES (1, 1), (1, 2), (2, 1)")
        conn.execute(
            "INSERT INTO pixiv_tokens (id, label, enabled, refresh_token_enc, refresh_token_masked, weight)"
            " VALUES (1, 't1', 1, 'enc-blob', '***', 1.0)"
        )
        conn.execute("INSERT INTO proxy_pools (id, name, enabled) VALUES (1, 'pool1', 1)")
        conn.execute(
            "INSERT INTO proxy_endpoints (id, scheme, host, port, enabled)"
            " VALUES (1, 'http', '10.0.0.1', 8080, 1)"
        )
        conn.execute(
            "INSERT INTO proxy_pool_endpoints (pool_id, endpoint_id, weight) VALUES (1, 1, 1)"
        )
        conn.execute(
            "INSERT INTO token_proxy_bindings (id, token_id, pool_id, primary_proxy_id) VALUES (1, 1, 1, 1)"
        )
        if with_orphans:
            # SQLite does not enforce FKs by default: dangling tag link + image.
            conn.execute("INSERT INTO image_tags (image_id, tag_id) VALUES (999, 1)")
            conn.execute("INSERT INTO image_tags (image_id, tag_id) VALUES (1, 999)")
        conn.commit()
    finally:
        conn.close()


def _target_url(db_path: Path) -> str:
    return "sqlite:///" + db_path.as_posix()


def _run(source: Path, target: Path, ckpt: Path, **kw) -> dict:
    return asyncio.run(
        etl.etl_run(
            source_db=str(source),
            target_url=_target_url(target),
            checkpoint_path=str(ckpt),
            batch_size=kw.pop("batch_size", 2),
            log=lambda _m: None,
            **kw,
        )
    )


def test_etl_full_copy_and_validate(tmp_path: Path) -> None:
    source, target = tmp_path / "src.db", tmp_path / "dst.db"
    _seed_source(source)
    _upgrade_head(target)

    summary = _run(source, target, tmp_path / "ckpt.json")
    copied = {t: s["rows_copied"] for t, s in summary["tables"].items()}
    assert copied == {
        "imports": 1,
        "images": 3,
        "import_images": 1,
        "tags": 2,
        "image_tags": 3,
        "pixiv_tokens": 1,
        "proxy_pools": 1,
        "proxy_endpoints": 1,
        "proxy_pool_endpoints": 1,
        "token_proxy_bindings": 1,
    }

    report = asyncio.run(
        etl.etl_validate(source_db=str(source), target_url=_target_url(target), log=lambda _m: None)
    )
    assert report["ok"] is True
    assert report["target_orphans"] == {}

    # ids preserved → FKs still valid on the target.
    with sqlite3.connect(target) as conn:
        row = conn.execute(
            "SELECT created_import_id FROM images WHERE illust_id = 101"
        ).fetchone()
        assert row == (1,)
        enc = conn.execute("SELECT refresh_token_enc FROM pixiv_tokens WHERE id = 1").fetchone()
        assert enc == ("enc-blob",)


def test_etl_resume_from_checkpoint(tmp_path: Path) -> None:
    source, target = tmp_path / "src.db", tmp_path / "dst.db"
    _seed_source(source)
    _upgrade_head(target)
    ckpt = tmp_path / "ckpt.json"

    # Interrupt after the first images batch by poisoning the sink once.
    original_write = etl.SqliteSink.write
    calls = {"n": 0}

    async def _failing_write(self, table, rows):  # type: ignore[no-untyped-def]
        await original_write(self, table, rows)
        calls["n"] += 1
        if table == "images" and calls["n"] >= 2:
            raise RuntimeError("simulated crash after images batch")

    etl.SqliteSink.write = _failing_write  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError, match="simulated crash"):
            _run(source, target, ckpt, batch_size=2)
    finally:
        etl.SqliteSink.write = original_write  # type: ignore[method-assign]

    state = json.loads(ckpt.read_text(encoding="utf-8"))
    assert state["tables"]["imports"]["done"] is True
    # The crashed images batch committed rows but never checkpointed: the
    # on-disk state must NOT claim them, and resume must repair + re-copy
    # them exactly once.
    assert "images" not in state["tables"] or state["tables"]["images"]["done"] is False

    # Resume: no duplicates, everything completes, validation passes.
    _run(source, target, ckpt, batch_size=2)
    report = asyncio.run(
        etl.etl_validate(source_db=str(source), target_url=_target_url(target), log=lambda _m: None)
    )
    assert report["ok"] is True


def test_etl_checkpoint_refuses_other_source(tmp_path: Path) -> None:
    source, other, target = tmp_path / "src.db", tmp_path / "other.db", tmp_path / "dst.db"
    _seed_source(source)
    _seed_source(other)
    _upgrade_head(target)
    ckpt = tmp_path / "ckpt.json"
    _run(source, target, ckpt)

    with pytest.raises(SystemExit, match="belongs to source"):
        _run(other, target, ckpt)


def test_etl_orphans_fail_loud_by_default(tmp_path: Path) -> None:
    source, target = tmp_path / "src.db", tmp_path / "dst.db"
    _seed_source(source, with_orphans=True)
    _upgrade_head(target)

    with pytest.raises(SystemExit, match="orphan"):
        _run(source, target, tmp_path / "ckpt.json")


def test_etl_skip_orphans_filters_and_counts(tmp_path: Path) -> None:
    source, target = tmp_path / "src.db", tmp_path / "dst.db"
    _seed_source(source, with_orphans=True)
    _upgrade_head(target)

    summary = _run(source, target, tmp_path / "ckpt.json", skip_orphans=True)
    assert summary["tables"]["image_tags"]["rows_copied"] == 3
    assert summary["tables"]["image_tags"]["orphans_skipped"] == 2
    assert summary["source_orphans"]  # reported, not hidden

    with sqlite3.connect(target) as conn:
        n = conn.execute("SELECT COUNT(*) FROM image_tags").fetchone()[0]
        assert n == 3
        dangling = conn.execute(
            "SELECT COUNT(*) FROM image_tags it LEFT JOIN images i ON it.image_id = i.id "
            "WHERE i.id IS NULL"
        ).fetchone()[0]
        assert dangling == 0


def test_etl_resume_after_checkpointed_composite_batch_crash(tmp_path: Path) -> None:
    """Crash AFTER a checkpointed image_tags batch: resume must repair the
    committed-but-uncheckpointed tail via the composite-PK delete branch and
    re-copy it exactly once."""
    source, target = tmp_path / "src.db", tmp_path / "dst.db"
    _seed_source(source)
    _upgrade_head(target)
    ckpt = tmp_path / "ckpt.json"

    original_write = etl.SqliteSink.write
    tag_writes = {"n": 0}

    async def _failing_write(self, table, rows):  # type: ignore[no-untyped-def]
        await original_write(self, table, rows)
        if table == "image_tags":
            tag_writes["n"] += 1
            if tag_writes["n"] >= 2:
                raise RuntimeError("simulated crash after second image_tags batch")

    etl.SqliteSink.write = _failing_write  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError, match="simulated crash"):
            _run(source, target, ckpt, batch_size=2)
    finally:
        etl.SqliteSink.write = original_write  # type: ignore[method-assign]

    # First image_tags batch (2 rows) was checkpointed; the crashed batch
    # committed 1 row past the checkpoint.
    state = json.loads(ckpt.read_text(encoding="utf-8"))
    assert state["tables"]["image_tags"]["done"] is False
    assert state["tables"]["image_tags"]["rows_copied"] == 2
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT COUNT(*) FROM image_tags").fetchone() == (3,)

    _run(source, target, ckpt, batch_size=2)
    report = asyncio.run(
        etl.etl_validate(source_db=str(source), target_url=_target_url(target), log=lambda _m: None)
    )
    assert report["ok"] is True


def test_etl_skip_orphans_nulls_dangling_nullable_fk(tmp_path: Path) -> None:
    """A dangling created_import_id must be NULLED, not drop the image —
    dropping would cascade fresh orphans into image_tags and abort the
    PostgreSQL COPY mid-run."""
    source, target = tmp_path / "src.db", tmp_path / "dst.db"
    _seed_source(source)
    with sqlite3.connect(source) as conn:
        conn.execute(
            "INSERT INTO images (id, illust_id, page_index, ext, original_url, proxy_path,"
            " random_key, status, fail_count, created_import_id) "
            "VALUES (4, 104, 0, 'jpg', 'https://i.pximg.net/4.jpg', '/i/4.jpg', 0.4, 1, 0, 99)"
        )
        conn.execute("INSERT INTO image_tags (image_id, tag_id) VALUES (4, 1)")
        conn.commit()
    _upgrade_head(target)

    summary = _run(source, target, tmp_path / "ckpt.json", skip_orphans=True)
    assert summary["tables"]["images"]["rows_copied"] == 4
    assert summary["tables"]["images"]["orphans_nulled"] == 1
    assert summary["tables"]["images"]["orphans_skipped"] == 0
    assert summary["tables"]["image_tags"]["rows_copied"] == 4

    with sqlite3.connect(target) as conn:
        row = conn.execute("SELECT created_import_id FROM images WHERE id = 4").fetchone()
        assert row == (None,)
        kept = conn.execute("SELECT COUNT(*) FROM image_tags WHERE image_id = 4").fetchone()
        assert kept == (1,)

    report = asyncio.run(
        etl.etl_validate(source_db=str(source), target_url=_target_url(target), log=lambda _m: None)
    )
    # Counts and PK sums still match (row kept), and the target has no orphans.
    assert report["target_orphans"] == {}


def test_etl_fresh_run_refuses_non_empty_target(tmp_path: Path) -> None:
    source, target = tmp_path / "src.db", tmp_path / "dst.db"
    _seed_source(source)
    _upgrade_head(target)
    with sqlite3.connect(target) as conn:
        conn.execute("INSERT INTO tags (id, name) VALUES (77, 'preexisting')")
        conn.commit()

    with pytest.raises(SystemExit, match="already has"):
        _run(source, target, tmp_path / "ckpt.json")

    summary = _run(source, target, tmp_path / "ckpt2.json", allow_wipe=True)
    assert summary["tables"]["tags"]["rows_copied"] == 2
    with sqlite3.connect(target) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM tags")}
        assert names == {"cat", "dog"}


def test_etl_validate_detects_target_drift(tmp_path: Path) -> None:
    source, target = tmp_path / "src.db", tmp_path / "dst.db"
    _seed_source(source)
    _upgrade_head(target)
    _run(source, target, tmp_path / "ckpt.json")

    with sqlite3.connect(target) as conn:
        conn.execute("DELETE FROM image_tags WHERE image_id = 2")
        conn.commit()

    report = asyncio.run(
        etl.etl_validate(source_db=str(source), target_url=_target_url(target), log=lambda _m: None)
    )
    assert report["ok"] is False
    assert report["tables"]["image_tags"]["match"] is False


def test_table_order_covers_all_fk_parents_first() -> None:
    # Structural guard: every FK child appears after its parent.
    order = {t: i for i, t in enumerate(etl.TABLE_ORDER)}
    for child, _col, parent, _pcol, _nullable in etl.ORPHAN_CHECKS:
        assert order[parent] < order[child], (child, parent)


@pytest.mark.skipif(not LIVE_PG_URL, reason="TEST_POSTGRES_URL not set (live PostgreSQL integration)")
def test_etl_live_postgres_end_to_end(tmp_path: Path) -> None:
    source = tmp_path / "src.db"
    _seed_source(source)

    # Target schema via alembic on the live PG, then run + validate + sequence check.
    from alembic import command
    from alembic.config import Config

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = LIVE_PG_URL
    try:
        cfg = Config(str(_REPO / "backend" / "alembic.ini"))
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous

    asyncio.run(
        etl.etl_run(
            source_db=str(source),
            target_url=LIVE_PG_URL,
            checkpoint_path=str(tmp_path / "ckpt.json"),
            batch_size=2,
            log=lambda _m: None,
        )
    )
    report = asyncio.run(
        etl.etl_validate(source_db=str(source), target_url=LIVE_PG_URL, log=lambda _m: None)
    )
    assert report["ok"] is True

    async def _next_image_id() -> int:
        import asyncpg

        conn = await asyncpg.connect(etl._pg_dsn(LIVE_PG_URL))
        try:
            return int(await conn.fetchval("SELECT nextval(pg_get_serial_sequence('images','id'))"))
        finally:
            await conn.close()

    # Sequence reset: the next generated id must not collide with copied ids.
    assert asyncio.run(_next_image_id()) > 3
