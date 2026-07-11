from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.dialect import backend_from_database_url
from app.db.tags_get import get_tag_names_for_image
from app.db.tags_links import (
    clear_all_image_tags,
    clear_all_tags,
    delete_image_tags_for_image_ids,
    ensure_tags_by_names,
    image_has_any_tag,
    link_image_tags,
    map_tag_names_by_image_ids,
    replace_image_tags,
    upsert_tags_with_translations,
)
from app.db.tags_list import TagListItem, list_tags as list_tags_helper


@runtime_checkable
class TagStore(Protocol):
    """Tag + image_tag port (SQLite today; Postgres dialect label later).

    Intentionally separate from CatalogStore (Image rows). Callers own commit.
    """

    backend: str

    async def get_tag_names_for_image(self, session: AsyncSession, *, image_id: int) -> list[str]: ...

    async def map_tag_names_by_image_ids(
        self,
        session: AsyncSession,
        *,
        image_ids: Sequence[int],
    ) -> dict[int, list[str]]: ...

    async def image_has_any_tag(self, session: AsyncSession, *, image_id: int) -> bool: ...

    async def list_tags(
        self,
        session: AsyncSession,
        *,
        limit: int,
        cursor: str | None = None,
        q: str | None = None,
    ) -> tuple[list[TagListItem], str | None]: ...

    async def ensure_tags_by_names(
        self,
        session: AsyncSession,
        *,
        names: Sequence[str],
    ) -> dict[str, int]: ...

    async def upsert_tags_with_translations(
        self,
        session: AsyncSession,
        *,
        tags: Sequence[tuple[str, str | None]],
        now: str | None = None,
    ) -> dict[str, int]: ...

    async def link_image_tags(
        self,
        session: AsyncSession,
        *,
        pairs: Sequence[tuple[int, int]],
    ) -> int: ...

    async def replace_image_tags(
        self,
        session: AsyncSession,
        *,
        image_ids: Sequence[int],
        tag_ids: Sequence[int],
    ) -> None: ...

    async def delete_image_tags_for_image_ids(
        self,
        session: AsyncSession,
        *,
        image_ids: Sequence[int],
    ) -> int: ...

    async def clear_all_image_tags(self, session: AsyncSession) -> int: ...

    async def clear_all_tags(self, session: AsyncSession) -> int: ...


class SqliteTagStore:
    """Default tag store: existing SQLite helpers (behavior unchanged)."""

    backend: str = "sqlite"

    async def get_tag_names_for_image(self, session: AsyncSession, *, image_id: int) -> list[str]:
        return await get_tag_names_for_image(session, image_id=image_id)

    async def map_tag_names_by_image_ids(
        self,
        session: AsyncSession,
        *,
        image_ids: Sequence[int],
    ) -> dict[int, list[str]]:
        return await map_tag_names_by_image_ids(session, image_ids=image_ids)

    async def image_has_any_tag(self, session: AsyncSession, *, image_id: int) -> bool:
        return await image_has_any_tag(session, image_id=image_id)

    async def list_tags(
        self,
        session: AsyncSession,
        *,
        limit: int,
        cursor: str | None = None,
        q: str | None = None,
    ) -> tuple[list[TagListItem], str | None]:
        return await list_tags_helper(session, limit=limit, cursor=cursor, q=q)

    async def ensure_tags_by_names(
        self,
        session: AsyncSession,
        *,
        names: Sequence[str],
    ) -> dict[str, int]:
        return await ensure_tags_by_names(session, names=names)

    async def upsert_tags_with_translations(
        self,
        session: AsyncSession,
        *,
        tags: Sequence[tuple[str, str | None]],
        now: str | None = None,
    ) -> dict[str, int]:
        return await upsert_tags_with_translations(session, tags=tags, now=now)

    async def link_image_tags(
        self,
        session: AsyncSession,
        *,
        pairs: Sequence[tuple[int, int]],
    ) -> int:
        return await link_image_tags(session, pairs=pairs)

    async def replace_image_tags(
        self,
        session: AsyncSession,
        *,
        image_ids: Sequence[int],
        tag_ids: Sequence[int],
    ) -> None:
        await replace_image_tags(session, image_ids=image_ids, tag_ids=tag_ids)

    async def delete_image_tags_for_image_ids(
        self,
        session: AsyncSession,
        *,
        image_ids: Sequence[int],
    ) -> int:
        return await delete_image_tags_for_image_ids(session, image_ids=image_ids)

    async def clear_all_image_tags(self, session: AsyncSession) -> int:
        return await clear_all_image_tags(session)

    async def clear_all_tags(self, session: AsyncSession) -> int:
        return await clear_all_tags(session)


class PostgresTagStore(SqliteTagStore):
    backend: str = "postgres"


def tag_backend_from_database_url(database_url: str) -> str:
    """Map DATABASE_URL dialect → tag backend label (shared dialect helper)."""
    return backend_from_database_url(database_url)


def build_tag_store(*, database_url: str = "") -> TagStore:
    backend = tag_backend_from_database_url(database_url)
    if backend == "postgres":
        return PostgresTagStore()
    return SqliteTagStore()


def resolve_tag_store(tag_store: TagStore | None = None) -> TagStore:
    """Prefer injected TagStore; fall back to default SQLite helpers."""
    return tag_store if tag_store is not None else SqliteTagStore()
