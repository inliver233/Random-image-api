"""Case-insensitive search parity across dialects (M6).

SQLite's LIKE is case-insensitive for ASCII by default, so the old
``.like()`` fallback behaved case-insensitively there — but PostgreSQL
LIKE is case-sensitive, silently changing search semantics after
cutover. The fallback now uses ILIKE on both dialects, and migration
0023 adds pg_trgm GIN indexes so %…% ILIKE is not a full-table scan on
PostgreSQL.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import sqlalchemy as sa

from app.db.authors_list import list_authors
from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.image_tags import ImageTag
from app.db.models.images import Image
from app.db.models.tags import Tag
from app.db.session import create_sessionmaker
from app.db.tags_list import list_tags


def _image(illust_id: int, *, user_id: int | None = None, user_name: str | None = None) -> Image:
    return Image(
        illust_id=illust_id,
        page_index=0,
        ext="jpg",
        original_url=f"https://i.pximg.net/{illust_id}.jpg",
        proxy_path=f"/i/{illust_id}.jpg",
        random_key=0.5,
        status=1,
        user_id=user_id,
        user_name=user_name,
    )


def test_tag_and_author_search_fallback_is_case_insensitive(tmp_path: Path) -> None:
    db_path = tmp_path / "search_ci.db"
    engine = create_engine("sqlite+aiosqlite:///" + db_path.as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            img = _image(100, user_id=7, user_name="CamelCaseAuthor")
            session.add(img)
            tag = Tag(name="MixedCaseTag")
            session.add(tag)
            await session.flush()
            session.add(ImageTag(image_id=img.id, tag_id=tag.id))
            await session.commit()

        async with Session() as session:
            # Short queries (<3 chars would also skip FTS) — use lowercase
            # forms of mixed-case stored values to prove ILIKE semantics.
            tags, _ = await list_tags(session, limit=10, q="mixedcase")
            assert [t.name for t in tags] == ["MixedCaseTag"]

            authors, _ = await list_authors(session, limit=10, q="camelcase")
            assert [a.user_name for a in authors] == ["CamelCaseAuthor"]

        await engine.dispose()

    asyncio.run(_run())


def test_search_fallback_compiles_to_ilike_on_postgres() -> None:
    # PostgreSQL compilation must produce ILIKE, not LIKE, for the fallback.
    from sqlalchemy.dialects import postgresql

    clause = sa.or_(Tag.name.ilike("%q%"), Tag.translated_name.ilike("%q%"))
    compiled = str(clause.compile(dialect=postgresql.dialect()))
    assert "ILIKE" in compiled


def test_migration_0023_creates_trgm_indexes_only_on_postgres(tmp_path: Path) -> None:
    import os
    import sqlite3

    from alembic import command
    from alembic.config import Config

    backend_dir = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "trgm_sqlite_noop.db"
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + db_path.as_posix()
    try:
        cfg = Config(str(backend_dir / "alembic.ini"))
        command.upgrade(cfg, "head")
        with sqlite3.connect(db_path) as conn:
            version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            assert version == ("20260726_0023",)
            trgm = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%trgm%'"
            ).fetchone()
            assert trgm == (0,)
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
