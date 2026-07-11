from __future__ import annotations

import asyncio
from pathlib import Path

from app.db.catalog import (
    CatalogStore,
    PostgresCatalogStore,
    SqliteCatalogStore,
    build_catalog_store,
    catalog_backend_from_database_url,
)
from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.session import create_sessionmaker


def test_catalog_backend_from_database_url() -> None:
    assert catalog_backend_from_database_url("sqlite+aiosqlite:///./data/app.db") == "sqlite"
    assert catalog_backend_from_database_url("postgresql+asyncpg://u:p@localhost/db") == "postgres"
    assert catalog_backend_from_database_url("postgres://u:p@localhost/db") == "postgres"
    assert catalog_backend_from_database_url("") == "sqlite"


def test_build_catalog_store_dialects() -> None:
    s = build_catalog_store(database_url="sqlite+aiosqlite:///:memory:")
    assert isinstance(s, SqliteCatalogStore)
    assert s.backend == "sqlite"
    assert isinstance(s, CatalogStore)

    p = build_catalog_store(database_url="postgresql+asyncpg://u:p@h/db")
    assert isinstance(p, PostgresCatalogStore)
    assert p.backend == "postgres"


def test_sqlite_catalog_store_upsert(tmp_path: Path) -> None:
    engine = create_engine("sqlite+aiosqlite:///" + (tmp_path / "c.db").as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = build_catalog_store(database_url=str(engine.url))
        Session = create_sessionmaker(engine)
        async with Session() as session:
            image_id = await store.upsert_image_by_illust_page(
                session,
                illust_id=9,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/a.jpg",
                proxy_path="/i/9.jpg",
                random_key=0.2,
                created_import_id=None,
            )
            await session.commit()
            row = await store.get_image_by_id(session, image_id=image_id)
            assert row is not None
            assert int(row.illust_id) == 9
            rows = await store.get_images_by_ids(session, image_ids=[image_id, 999999])
            assert len(rows) == 1
            assert int(rows[0].id) == int(image_id)
        await store.mark_image_ok(engine, image_id=int(image_id), now="2020-01-01T00:00:00.000Z")
        await store.mark_image_failure(
            engine,
            image_id=int(image_id),
            now="2020-01-01T00:00:01.000Z",
            error_code="UPSTREAM_403",
            error_message="probe",
        )
        await engine.dispose()

    asyncio.run(_run())


def test_sqlite_catalog_store_upsert_hydrated(tmp_path: Path) -> None:
    engine = create_engine("sqlite+aiosqlite:///" + (tmp_path / "c_hydrate.db").as_posix())

    async def _run() -> None:
        import sqlalchemy as sa

        from app.db.models.images import Image

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = build_catalog_store(database_url=str(engine.url))
        Session = create_sessionmaker(engine)
        async with Session() as session:
            image_id = await store.upsert_hydrated_image_page(
                session,
                illust_id=42,
                page_index=0,
                ext="png",
                original_url="https://example.test/b.png",
                random_key=0.3,
                width=800,
                height=600,
                aspect_ratio=800 / 600,
                orientation=2,
                x_restrict=0,
                ai_type=0,
                illust_type=0,
                user_id=7,
                user_name="u",
                title="t",
                created_at_pixiv="2020-01-01T00:00:00+00:00",
                bookmark_count=10,
                view_count=100,
                comment_count=1,
                created_import_id=None,
            )
            await session.commit()
            row = (
                await session.execute(
                    sa.select(
                        Image.illust_id,
                        Image.width,
                        Image.bookmark_count,
                        Image.proxy_path,
                    ).where(Image.id == int(image_id))
                )
            ).one()
            assert int(row.illust_id) == 42
            assert int(row.width or 0) == 800
            assert int(row.bookmark_count or 0) == 10
            assert str(row.proxy_path) == f"/i/{image_id}.png"
            # Update metadata on conflict; proxy_path stays id-based.
            image_id2 = await store.upsert_hydrated_image_page(
                session,
                illust_id=42,
                page_index=0,
                ext="png",
                original_url="https://example.test/b2.png",
                random_key=0.9,
                width=900,
                height=700,
                aspect_ratio=900 / 700,
                orientation=2,
                x_restrict=0,
                ai_type=0,
                illust_type=0,
                user_id=7,
                user_name="u2",
                title="t2",
                created_at_pixiv="2020-01-02T00:00:00+00:00",
                bookmark_count=20,
                view_count=200,
                comment_count=2,
                created_import_id=None,
            )
            await session.commit()
            assert int(image_id2) == int(image_id)
            row2 = (
                await session.execute(
                    sa.select(Image.width, Image.bookmark_count, Image.title).where(Image.id == int(image_id))
                )
            ).one()
            assert int(row2.width or 0) == 900
            assert int(row2.bookmark_count or 0) == 20
            assert str(row2.title) == "t2"
        await engine.dispose()

    asyncio.run(_run())


def test_resolve_catalog_store_fallback() -> None:
    from app.core.random_delivery import resolve_catalog_store

    fallback = resolve_catalog_store(None)
    assert isinstance(fallback, SqliteCatalogStore)
    injected = SqliteCatalogStore()
    assert resolve_catalog_store(injected) is injected
