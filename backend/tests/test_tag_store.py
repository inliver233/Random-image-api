from __future__ import annotations

import asyncio
from pathlib import Path

import sqlalchemy as sa

from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.image_tags import ImageTag
from app.db.models.images import Image
from app.db.models.tags import Tag
from app.db.session import create_sessionmaker
from app.db.tag_store import (
    PostgresTagStore,
    SqliteTagStore,
    TagStore,
    build_tag_store,
    resolve_tag_store,
    tag_backend_from_database_url,
)


def test_tag_backend_from_database_url() -> None:
    assert tag_backend_from_database_url("sqlite+aiosqlite:///./data/app.db") == "sqlite"
    assert tag_backend_from_database_url("postgresql+asyncpg://u:p@localhost/db") == "postgres"
    assert tag_backend_from_database_url("") == "sqlite"


def test_tag_and_image_tag_insert_builders_are_dialect_aware() -> None:
    """ensure_tags_by_names / link_image_tags must not hardcode sqlite_insert only."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from app.db.images_upsert import insert_for_dialect

    assert type(insert_for_dialect(Tag, dialect_name="sqlite")) is type(sqlite_insert(Tag))
    assert type(insert_for_dialect(Tag, dialect_name="postgresql")) is type(pg_insert(Tag))
    assert type(insert_for_dialect(ImageTag, dialect_name="postgresql")) is type(pg_insert(ImageTag))


def test_build_tag_store_dialects() -> None:
    s = build_tag_store(database_url="sqlite+aiosqlite:///:memory:")
    assert isinstance(s, SqliteTagStore)
    assert s.backend == "sqlite"
    assert isinstance(s, TagStore)

    p = build_tag_store(database_url="postgresql+asyncpg://u:p@h/db")
    assert isinstance(p, PostgresTagStore)
    assert p.backend == "postgres"


def test_resolve_tag_store_fallback() -> None:
    fallback = resolve_tag_store(None)
    assert isinstance(fallback, SqliteTagStore)
    injected = SqliteTagStore()
    assert resolve_tag_store(injected) is injected


def test_sqlite_tag_store_links_and_names(tmp_path: Path) -> None:
    engine = create_engine("sqlite+aiosqlite:///" + (tmp_path / "t.db").as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = build_tag_store(database_url=str(engine.url))
        Session = create_sessionmaker(engine)
        async with Session() as session:
            img = Image(
                illust_id=1,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/1.jpg",
                proxy_path="/i/1.jpg",
                random_key=0.1,
                status=1,
            )
            session.add(img)
            await session.commit()
            await session.refresh(img)

            assert await store.image_has_any_tag(session, image_id=int(img.id)) is False

            by_name = await store.ensure_tags_by_names(session, names=["a", "b", "a", ""])
            assert set(by_name) == {"a", "b"}
            await store.link_image_tags(
                session,
                pairs=[
                    (int(img.id), by_name["a"]),
                    (int(img.id), by_name["b"]),
                    (int(img.id), by_name["a"]),  # dedupe
                ],
            )
            await session.commit()

            assert await store.image_has_any_tag(session, image_id=int(img.id)) is True
            names = await store.get_tag_names_for_image(session, image_id=int(img.id))
            assert names == ["a", "b"]
            mapped = await store.map_tag_names_by_image_ids(session, image_ids=[int(img.id), 999])
            assert set(mapped[int(img.id)]) == {"a", "b"}
            assert mapped[999] == []

            by_tr = await store.upsert_tags_with_translations(
                session,
                tags=[("a", "A-tr"), ("c", None)],
                now="2020-01-01T00:00:00.000Z",
            )
            await session.commit()
            assert "a" in by_tr and "c" in by_tr
            a_row = (await session.execute(sa.select(Tag.translated_name).where(Tag.name == "a"))).scalar_one()
            assert str(a_row) == "A-tr"

            await store.replace_image_tags(
                session,
                image_ids=[int(img.id)],
                tag_ids=[by_tr["c"]],
            )
            await session.commit()
            names2 = await store.get_tag_names_for_image(session, image_id=int(img.id))
            assert names2 == ["c"]

            deleted_links = await store.delete_image_tags_for_image_ids(session, image_ids=[int(img.id)])
            await session.commit()
            assert deleted_links >= 1
            assert await store.image_has_any_tag(session, image_id=int(img.id)) is False

            await store.link_image_tags(session, pairs=[(int(img.id), by_tr["a"])])
            await session.commit()
            assert await store.clear_all_image_tags(session) >= 1
            await session.commit()
            assert await store.clear_all_tags(session) >= 1
            await session.commit()
            remaining_tags = int((await session.execute(sa.select(sa.func.count()).select_from(Tag))).scalar_one())
            remaining_links = int(
                (await session.execute(sa.select(sa.func.count()).select_from(ImageTag))).scalar_one()
            )
            assert remaining_tags == 0
            assert remaining_links == 0
        await engine.dispose()

    asyncio.run(_run())


def test_map_tag_names_by_image_ids_chunks_large_id_lists(tmp_path: Path) -> None:
    """ENGINE-1: >999 image ids must not hit SQLite 'too many SQL variables'."""
    engine = create_engine("sqlite+aiosqlite:///" + (tmp_path / "t_chunk.db").as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = build_tag_store(database_url=str(engine.url))
        Session = create_sessionmaker(engine)
        async with Session() as session:
            img = Image(
                illust_id=1,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/1.jpg",
                proxy_path="/i/1.jpg",
                random_key=0.1,
                status=1,
            )
            session.add(img)
            await session.flush()
            t1 = Tag(name="zulu", translated_name=None)
            session.add(t1)
            await session.flush()
            session.add(ImageTag(image_id=int(img.id), tag_id=int(t1.id)))
            await session.commit()

            # Real id + many missing ids → single unchunked IN would exceed ~999 binds.
            huge_ids = [int(img.id)] + list(range(10_000, 12_100))
            assert len(huge_ids) > 1000
            mapped = await store.map_tag_names_by_image_ids(session, image_ids=huge_ids)
            assert mapped[int(img.id)] == ["zulu"]
            assert mapped[10_000] == []
            assert len(mapped) == len(huge_ids)
        await engine.dispose()

    asyncio.run(_run())


def test_sqlite_tag_store_list_tags(tmp_path: Path) -> None:
    engine = create_engine("sqlite+aiosqlite:///" + (tmp_path / "t_list.db").as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = build_tag_store(database_url=str(engine.url))
        Session = create_sessionmaker(engine)
        async with Session() as session:
            img = Image(
                illust_id=1,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/1.jpg",
                proxy_path="/i/1.jpg",
                random_key=0.1,
                status=1,
            )
            session.add(img)
            await session.flush()
            t1 = Tag(name="alpha", translated_name=None)
            t2 = Tag(name="beta", translated_name=None)
            session.add_all([t1, t2])
            await session.flush()
            session.add_all(
                [
                    ImageTag(image_id=int(img.id), tag_id=int(t1.id)),
                    ImageTag(image_id=int(img.id), tag_id=int(t2.id)),
                ]
            )
            await session.commit()

            items, next_c = await store.list_tags(session, limit=10)
            assert next_c is None
            assert [i.name for i in items] == ["alpha", "beta"]
            assert items[0].count_images == 1
        await engine.dispose()

    asyncio.run(_run())
