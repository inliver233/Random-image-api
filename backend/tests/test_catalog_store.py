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
from app.db.dialect import backend_from_database_url
from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.session import create_sessionmaker


def test_backend_from_database_url() -> None:
    assert backend_from_database_url("sqlite+aiosqlite:///./data/app.db") == "sqlite"
    assert backend_from_database_url("postgresql+asyncpg://u:p@localhost/db") == "postgres"
    assert backend_from_database_url("postgres://u:p@localhost/db") == "postgres"
    assert backend_from_database_url("") == "sqlite"
    # Catalog re-export stays stable for existing callers.
    assert catalog_backend_from_database_url("postgresql+asyncpg://u:p@h/db") == "postgres"


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


def test_sqlite_catalog_store_get_by_illust_page(tmp_path: Path) -> None:
    engine = create_engine("sqlite+aiosqlite:///" + (tmp_path / "c_illust.db").as_posix())

    async def _run() -> None:
        from app.db.models.images import Image

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = build_catalog_store(database_url=str(engine.url))
        Session = create_sessionmaker(engine)
        async with Session() as session:
            session.add(
                Image(
                    illust_id=12,
                    page_index=0,
                    ext="jpg",
                    original_url="https://example.test/12.jpg",
                    proxy_path="/i/1.jpg",
                    random_key=0.1,
                    status=1,
                )
            )
            session.add(
                Image(
                    illust_id=12,
                    page_index=1,
                    ext="png",
                    original_url="https://example.test/12_p1.png",
                    proxy_path="/i/2.png",
                    random_key=0.2,
                    status=3,  # broken — not returned by public illust lookup
                )
            )
            await session.commit()
            active = await store.get_image_by_illust_page(session, illust_id=12, page_index=0)
            assert active is not None
            assert int(active.illust_id) == 12
            assert int(active.page_index) == 0
            broken = await store.get_image_by_illust_page(session, illust_id=12, page_index=1)
            assert broken is None
            missing = await store.get_image_by_illust_page(session, illust_id=99, page_index=0)
            assert missing is None
        await engine.dispose()

    asyncio.run(_run())


def test_sqlite_catalog_store_heal_broken_images(tmp_path: Path) -> None:
    engine = create_engine("sqlite+aiosqlite:///" + (tmp_path / "c_heal.db").as_posix())

    async def _run() -> None:
        import sqlalchemy as sa

        from app.db.models.images import Image

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = build_catalog_store(database_url=str(engine.url))
        Session = create_sessionmaker(engine)
        async with Session() as session:
            broken = Image(
                illust_id=55,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/55.jpg",
                proxy_path="/i/1.jpg",
                random_key=0.5,
                status=3,
                last_error_code="UPSTREAM_404",
                last_error_msg="gone",
            )
            ok = Image(
                illust_id=55,
                page_index=1,
                ext="jpg",
                original_url="https://example.test/55_p1.jpg",
                proxy_path="/i/2.jpg",
                random_key=0.6,
                status=1,
            )
            other = Image(
                illust_id=99,
                page_index=0,
                ext="png",
                original_url="https://example.test/99.png",
                proxy_path="/i/3.png",
                random_key=0.7,
                status=3,
            )
            session.add_all([broken, ok, other])
            await session.commit()
            await session.refresh(broken)
            await session.refresh(ok)
            await session.refresh(other)

            healed = await store.heal_broken_images_for_illust(
                session,
                illust_id=55,
                now="2020-01-01T00:00:00.000Z",
            )
            await session.commit()
            assert healed == [int(broken.id)]

            statuses = (
                await session.execute(
                    sa.select(Image.id, Image.status, Image.last_error_code, Image.last_ok_at)
                    .where(Image.id.in_([int(broken.id), int(ok.id), int(other.id)]))
                    .order_by(Image.id)
                )
            ).all()
            by_id = {int(r.id): r for r in statuses}
            assert int(by_id[int(broken.id)].status) == 1
            assert by_id[int(broken.id)].last_error_code is None
            assert str(by_id[int(broken.id)].last_ok_at) == "2020-01-01T00:00:00.000Z"
            assert int(by_id[int(ok.id)].status) == 1
            assert int(by_id[int(other.id)].status) == 3  # different illust untouched
        await engine.dispose()

    asyncio.run(_run())


def test_sqlite_catalog_store_bulk_upsert_import(tmp_path: Path) -> None:
    engine = create_engine("sqlite+aiosqlite:///" + (tmp_path / "c_import.db").as_posix())

    async def _run() -> None:
        import sqlalchemy as sa

        from app.db.models.images import Image
        from app.db.models.imports import Import

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = build_catalog_store(database_url=str(engine.url))
        Session = create_sessionmaker(engine)
        async with Session() as session:
            imp = Import(created_by="admin", source="manual")
            session.add(imp)
            await session.commit()
            await session.refresh(imp)
            import_id = int(imp.id)

            keys = [(100, 0), (100, 1)]
            rows = [
                {
                    "illust_id": 100,
                    "page_index": 0,
                    "ext": "jpg",
                    "original_url": "https://example.test/100_p0.jpg",
                    "proxy_path": "",
                    "random_key": 0.1,
                    "created_import_id": import_id,
                    "width": 640,
                    "height": 480,
                    "title": "a",
                },
                {
                    "illust_id": 100,
                    "page_index": 1,
                    "ext": "png",
                    "original_url": "https://example.test/100_p1.png",
                    "proxy_path": "",
                    "random_key": 0.2,
                    "created_import_id": import_id,
                    "width": None,
                    "height": None,
                    "title": "b",
                },
            ]
            ids = await store.bulk_upsert_import_rows(
                session,
                rows=rows,
                keys=keys,
                import_id=import_id,
            )
            await session.commit()
            assert len(ids) == 2
            got = (
                await session.execute(
                    sa.select(Image.id, Image.illust_id, Image.page_index, Image.proxy_path, Image.width, Image.title)
                    .where(sa.tuple_(Image.illust_id, Image.page_index).in_(keys))
                    .order_by(Image.page_index)
                )
            ).all()
            assert len(got) == 2
            assert str(got[0].proxy_path) == f"/i/{got[0].id}.jpg"
            assert int(got[0].width or 0) == 640
            assert str(got[0].title) == "a"
            assert str(got[1].proxy_path) == f"/i/{got[1].id}.png"

            # Re-upsert: nullable CASE merge keeps existing width when incoming is None;
            # non-null title overwrites.
            ids2 = await store.bulk_upsert_import_rows(
                session,
                rows=[
                    {
                        "illust_id": 100,
                        "page_index": 0,
                        "ext": "jpg",
                        "original_url": "https://example.test/100_p0b.jpg",
                        "proxy_path": "",
                        "random_key": 0.9,
                        "created_import_id": import_id,
                        "width": None,
                        "height": None,
                        "title": "a2",
                    }
                ],
                keys=[(100, 0)],
                import_id=import_id,
            )
            await session.commit()
            assert len(ids2) == 1
            assert int(ids2[0]) == int(got[0].id)
            row = (
                await session.execute(
                    sa.select(Image.width, Image.title, Image.original_url, Image.proxy_path).where(
                        Image.id == int(got[0].id)
                    )
                )
            ).one()
            assert int(row.width or 0) == 640  # preserved via CASE
            assert str(row.title) == "a2"
            assert "100_p0b" in str(row.original_url)
            # empty proxy_path on conflict keeps existing (CASE length > 0)
            assert str(row.proxy_path) == f"/i/{got[0].id}.jpg"
        await engine.dispose()

    asyncio.run(_run())


def test_sqlite_catalog_store_import_map_and_status(tmp_path: Path) -> None:
    engine = create_engine("sqlite+aiosqlite:///" + (tmp_path / "c_import_map.db").as_posix())

    async def _run() -> None:
        import sqlalchemy as sa

        from app.db.models.images import Image
        from app.db.models.imports import Import

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = build_catalog_store(database_url=str(engine.url))
        Session = create_sessionmaker(engine)
        async with Session() as session:
            imp = Import(created_by="admin", source="manual")
            session.add(imp)
            await session.commit()
            await session.refresh(imp)
            import_id = int(imp.id)

            a = Image(
                illust_id=30,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/30.jpg",
                proxy_path="/i/1.jpg",
                random_key=0.1,
                status=1,
                created_import_id=import_id,
            )
            b = Image(
                illust_id=30,
                page_index=1,
                ext="png",
                original_url="https://example.test/30_p1.png",
                proxy_path="/i/2.png",
                random_key=0.2,
                status=1,
                created_import_id=import_id,
            )
            other = Image(
                illust_id=31,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/31.jpg",
                proxy_path="/i/3.jpg",
                random_key=0.3,
                status=1,
                created_import_id=None,
            )
            session.add_all([a, b, other])
            await session.commit()
            await session.refresh(a)
            await session.refresh(b)
            await session.refresh(other)

            mapping = await store.map_image_ids_by_illust_page(
                session,
                keys=[(30, 0), (30, 1), (999, 0)],
            )
            assert mapping == {(30, 0): int(a.id), (30, 1): int(b.id)}

            any_row = await store.get_image_by_id_any_status(session, image_id=int(a.id))
            assert any_row is not None
            assert int(any_row.illust_id) == 30
            assert await store.get_image_by_id_any_status(session, image_id=999999) is None

            updated = await store.set_status_for_import(session, import_id=import_id, status=2)
            await session.commit()
            assert updated == 2
            statuses = dict(
                (
                    await session.execute(
                        sa.select(Image.id, Image.status).where(Image.id.in_([int(a.id), int(b.id), int(other.id)]))
                    )
                ).all()
            )
            assert int(statuses[int(a.id)]) == 2
            assert int(statuses[int(b.id)]) == 2
            assert int(statuses[int(other.id)]) == 1
        await engine.dispose()

    asyncio.run(_run())


def test_sqlite_catalog_store_engine_loads(tmp_path: Path) -> None:
    engine = create_engine("sqlite+aiosqlite:///" + (tmp_path / "c_engine.db").as_posix())

    async def _run() -> None:
        from app.db.models.images import Image

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = build_catalog_store(database_url=str(engine.url))
        Session = create_sessionmaker(engine)
        async with Session() as session:
            ok = Image(
                illust_id=10,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/10.jpg",
                proxy_path="/i/1.jpg",
                random_key=0.1,
                status=1,
            )
            broken = Image(
                illust_id=10,
                page_index=1,
                ext="png",
                original_url="https://example.test/10_p1.png",
                proxy_path="/i/2.png",
                random_key=0.2,
                status=3,
            )
            other = Image(
                illust_id=20,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/20.jpg",
                proxy_path="/i/3.jpg",
                random_key=0.3,
                status=1,
            )
            session.add_all([ok, broken, other])
            await session.commit()
            await session.refresh(ok)
            await session.refresh(broken)
            await session.refresh(other)

            # Public get skips status!=1; engine any-status includes broken.
            public_only = await store.get_images_by_ids(
                session,
                image_ids=[int(ok.id), int(broken.id)],
            )
            assert [int(r.id) for r in public_only] == [int(ok.id)]

            any_status = await store.get_images_by_ids_any_status(
                session,
                image_ids=[int(broken.id), int(ok.id), 999999],
            )
            assert [int(r.id) for r in any_status] == [int(broken.id), int(ok.id)]

            by_illust = await store.get_images_by_illust_id(session, illust_id=10)
            assert [int(r.page_index) for r in by_illust] == [0, 1]
            assert {int(r.status) for r in by_illust} == {1, 3}

            enabled = await store.list_enabled_images(session)
            assert [int(r.id) for r in enabled] == [int(ok.id), int(other.id)]
            limited = await store.list_enabled_images(session, limit=1)
            assert [int(r.id) for r in limited] == [int(ok.id)]
        await engine.dispose()

    asyncio.run(_run())


def test_sqlite_catalog_store_list_images(tmp_path: Path) -> None:
    engine = create_engine("sqlite+aiosqlite:///" + (tmp_path / "c_list.db").as_posix())

    async def _run() -> None:
        from app.db.models.images import Image

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = build_catalog_store(database_url=str(engine.url))
        Session = create_sessionmaker(engine)
        async with Session() as session:
            session.add_all(
                [
                    Image(
                        illust_id=1,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/1.jpg",
                        proxy_path="/i/1.jpg",
                        random_key=0.1,
                        status=1,
                        x_restrict=0,
                    ),
                    Image(
                        illust_id=2,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/2.jpg",
                        proxy_path="/i/2.jpg",
                        random_key=0.2,
                        status=1,
                        x_restrict=1,
                    ),
                    Image(
                        illust_id=3,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/3.jpg",
                        proxy_path="/i/3.jpg",
                        random_key=0.3,
                        status=3,
                        x_restrict=0,
                    ),
                ]
            )
            await session.commit()

            rows, next_cursor = await store.list_images(session, limit=10, r18=0, r18_strict=True)
            assert next_cursor is None
            assert [int(r.illust_id) for r in rows] == [1]

            page, next_c = await store.list_images(session, limit=1, r18=2, r18_strict=False)
            assert len(page) == 1
            assert next_c is not None
            page2, next_c2 = await store.list_images(
                session,
                limit=1,
                cursor=next_c,
                r18=2,
                r18_strict=False,
            )
            assert len(page2) == 1
            assert next_c2 is None
            assert {int(page[0].id), int(page2[0].id)} == {
                int(page[0].id),
                int(page2[0].id),
            }
            assert int(page[0].id) != int(page2[0].id)
        await engine.dispose()

    asyncio.run(_run())


def test_sqlite_catalog_store_list_authors(tmp_path: Path) -> None:
    engine = create_engine("sqlite+aiosqlite:///" + (tmp_path / "c_authors.db").as_posix())

    async def _run() -> None:
        from app.db.models.images import Image

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = build_catalog_store(database_url=str(engine.url))
        Session = create_sessionmaker(engine)
        async with Session() as session:
            session.add_all(
                [
                    Image(
                        illust_id=1,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/1.jpg",
                        proxy_path="/i/1.jpg",
                        random_key=0.1,
                        status=1,
                        user_id=10,
                        user_name="alice",
                    ),
                    Image(
                        illust_id=2,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/2.jpg",
                        proxy_path="/i/2.jpg",
                        random_key=0.2,
                        status=1,
                        user_id=10,
                        user_name="alice",
                    ),
                    Image(
                        illust_id=3,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/3.jpg",
                        proxy_path="/i/3.jpg",
                        random_key=0.3,
                        status=1,
                        user_id=20,
                        user_name="bob",
                    ),
                    Image(
                        illust_id=4,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/4.jpg",
                        proxy_path="/i/4.jpg",
                        random_key=0.4,
                        status=3,
                        user_id=30,
                        user_name="ghost",
                    ),
                ]
            )
            await session.commit()

            items, next_c = await store.list_authors(session, limit=10)
            assert next_c is None
            assert [(i.user_id, i.count_images) for i in items] == [(10, 2), (20, 1)]

            page, next_c = await store.list_authors(session, limit=1)
            assert len(page) == 1 and int(page[0].user_id) == 10
            assert next_c == 10
            page2, next_c2 = await store.list_authors(session, limit=1, cursor=next_c)
            assert len(page2) == 1 and int(page2[0].user_id) == 20
            assert next_c2 is None

            q_items, _ = await store.list_authors(session, limit=10, q="ali")
            assert [i.user_id for i in q_items] == [10]
        await engine.dispose()

    asyncio.run(_run())


def test_sqlite_catalog_store_list_admin_images(tmp_path: Path) -> None:
    engine = create_engine("sqlite+aiosqlite:///" + (tmp_path / "c_admin_list.db").as_posix())

    async def _run() -> None:
        from app.db.models.image_tags import ImageTag
        from app.db.models.images import Image
        from app.db.models.tags import Tag

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = build_catalog_store(database_url=str(engine.url))
        Session = create_sessionmaker(engine)
        async with Session() as session:
            incomplete = Image(
                illust_id=1,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/1.jpg",
                proxy_path="/i/1.jpg",
                random_key=0.1,
                status=1,
                width=None,
                height=None,
                x_restrict=None,
                title=None,
            )
            complete = Image(
                illust_id=2,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/2.jpg",
                proxy_path="/i/2.jpg",
                random_key=0.2,
                status=1,
                width=100,
                height=200,
                x_restrict=0,
                ai_type=0,
                illust_type=0,
                user_id=7,
                title="ok",
                created_at_pixiv="2020-01-01T00:00:00Z",
                bookmark_count=1,
                view_count=2,
                comment_count=3,
            )
            disabled = Image(
                illust_id=3,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/3.jpg",
                proxy_path="/i/3.jpg",
                random_key=0.3,
                status=3,
            )
            session.add_all([incomplete, complete, disabled])
            await session.flush()
            tag = Tag(name="t1", translated_name=None)
            session.add(tag)
            await session.flush()
            session.add(ImageTag(image_id=int(complete.id), tag_id=int(tag.id)))
            await session.commit()
            await session.refresh(incomplete)
            await session.refresh(complete)

            rows, next_cursor = await store.list_admin_images(session, limit=10)
            assert next_cursor is None
            assert [int(img.id) for img, _ in rows] == [int(complete.id), int(incomplete.id)]
            by_id = {int(img.id): int(tc) for img, tc in rows}
            assert by_id[int(complete.id)] == 1
            assert by_id[int(incomplete.id)] == 0

            missing_tags, _ = await store.list_admin_images(session, limit=10, missing_keys=["tags"])
            assert [int(img.id) for img, _ in missing_tags] == [int(incomplete.id)]

            missing_geo, _ = await store.list_admin_images(session, limit=10, missing_keys=["geometry"])
            assert [int(img.id) for img, _ in missing_geo] == [int(incomplete.id)]

            page1, next_c = await store.list_admin_images(session, limit=1)
            assert len(page1) == 1
            assert int(page1[0][0].id) == int(complete.id)
            assert next_c == int(complete.id)
            page2, next_c2 = await store.list_admin_images(session, limit=1, cursor=next_c)
            assert len(page2) == 1
            assert int(page2[0][0].id) == int(incomplete.id)
            assert next_c2 is None
        await engine.dispose()

    asyncio.run(_run())


def test_sqlite_catalog_store_clear_all_images(tmp_path: Path) -> None:
    engine = create_engine("sqlite+aiosqlite:///" + (tmp_path / "c_clear.db").as_posix())

    async def _run() -> None:
        import sqlalchemy as sa

        from app.db.models.images import Image

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = build_catalog_store(database_url=str(engine.url))
        Session = create_sessionmaker(engine)
        async with Session() as session:
            session.add_all(
                [
                    Image(
                        illust_id=1,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/1.jpg",
                        proxy_path="/i/1.jpg",
                        random_key=0.1,
                    ),
                    Image(
                        illust_id=2,
                        page_index=0,
                        ext="png",
                        original_url="https://example.test/2.png",
                        proxy_path="/i/2.png",
                        random_key=0.2,
                    ),
                ]
            )
            await session.commit()
            deleted = await store.clear_all_images(session)
            await session.commit()
            assert deleted == 2
            remaining = int((await session.execute(sa.select(sa.func.count()).select_from(Image))).scalar_one())
            assert remaining == 0
            assert await store.clear_all_images(session) == 0
        await engine.dispose()

    asyncio.run(_run())


def test_sqlite_catalog_store_delete_images_by_ids(tmp_path: Path) -> None:
    engine = create_engine("sqlite+aiosqlite:///" + (tmp_path / "c_delete.db").as_posix())

    async def _run() -> None:
        import sqlalchemy as sa

        from app.db.models.images import Image

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = build_catalog_store(database_url=str(engine.url))
        Session = create_sessionmaker(engine)
        async with Session() as session:
            a = Image(
                illust_id=1,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/1.jpg",
                proxy_path="/i/1.jpg",
                random_key=0.1,
            )
            b = Image(
                illust_id=2,
                page_index=0,
                ext="png",
                original_url="https://example.test/2.png",
                proxy_path="/i/2.png",
                random_key=0.2,
            )
            session.add_all([a, b])
            await session.commit()
            await session.refresh(a)
            await session.refresh(b)

            found = await store.delete_images_by_ids(
                session,
                image_ids=[int(a.id), 999999, int(a.id)],
            )
            await session.commit()
            assert found == [int(a.id)]

            remaining = list((await session.execute(sa.select(Image.id).order_by(Image.id))).scalars().all())
            assert remaining == [int(b.id)]

            empty = await store.delete_images_by_ids(session, image_ids=[])
            assert empty == []
        await engine.dispose()

    asyncio.run(_run())


def test_resolve_catalog_store_fallback() -> None:
    from app.core.random_delivery import resolve_catalog_store

    fallback = resolve_catalog_store(None)
    assert isinstance(fallback, SqliteCatalogStore)
    injected = SqliteCatalogStore()
    assert resolve_catalog_store(injected) is injected
