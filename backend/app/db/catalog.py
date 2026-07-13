from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.authors_list import AuthorListItem, list_authors as list_authors_helper
from app.db.dialect import backend_from_database_url
from app.db.images_admin_list import list_admin_images as list_admin_images_helper
from app.db.images_delete import clear_all_images, delete_images_by_ids
from app.db.images_get import (
    count_enabled_images,
    get_image_by_id,
    get_image_by_id_any_status,
    get_images_by_ids,
    get_images_by_ids_any_status,
    get_images_by_illust_id,
    list_enabled_images,
    map_image_ids_by_illust_page,
)
from app.db.images_get_by_illust import get_image_by_illust_page
from app.db.images_list import list_images as list_images_helper
from app.db.images_mark import (
    heal_broken_images_for_illust,
    mark_image_failure,
    mark_image_ok,
    set_status_for_import,
)
from app.db.images_upsert import (
    bulk_upsert_import_rows,
    upsert_hydrated_image_page,
    upsert_image_by_illust_page,
)
from app.db.models.images import Image


@runtime_checkable
class CatalogStore(Protocol):
    """Catalog read/write port (SQLite today; Postgres later without handler rewrite).

    Narrow surface used by import/hydrate/public delivery paths. Full schema migration
    to Postgres remains an ops/cutover task; this port freezes the application boundary.
    """

    backend: str

    async def upsert_image_by_illust_page(
        self,
        session: AsyncSession,
        *,
        illust_id: int,
        page_index: int,
        ext: str,
        original_url: str,
        proxy_path: str,
        random_key: float,
        created_import_id: int | None,
    ) -> int: ...

    async def upsert_hydrated_image_page(
        self,
        session: AsyncSession,
        *,
        illust_id: int,
        page_index: int,
        ext: str,
        original_url: str,
        random_key: float,
        width: int | None,
        height: int | None,
        aspect_ratio: float | None,
        orientation: int | None,
        x_restrict: int | None,
        ai_type: int | None,
        illust_type: int | None,
        user_id: int | None,
        user_name: str | None,
        title: str | None,
        created_at_pixiv: str | None,
        bookmark_count: int | None,
        view_count: int | None,
        comment_count: int | None,
        created_import_id: int | None,
    ) -> int: ...

    async def bulk_upsert_import_rows(
        self,
        session: AsyncSession,
        *,
        rows: list[dict],
        keys: list[tuple[int, int]],
        import_id: int,
    ) -> list[int]: ...

    async def get_image_by_id(self, session: AsyncSession, *, image_id: int) -> Image | None: ...

    async def get_image_by_id_any_status(self, session: AsyncSession, *, image_id: int) -> Image | None: ...

    async def get_images_by_ids(self, session: AsyncSession, *, image_ids: list[int]) -> list[Image]: ...

    async def get_images_by_ids_any_status(
        self,
        session: AsyncSession,
        *,
        image_ids: list[int],
    ) -> list[Image]: ...

    async def get_images_by_illust_id(
        self,
        session: AsyncSession,
        *,
        illust_id: int,
    ) -> list[Image]: ...

    async def list_enabled_images(
        self,
        session: AsyncSession,
        *,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> list[Image]: ...

    async def count_enabled_images(self, session: AsyncSession) -> int: ...

    async def map_image_ids_by_illust_page(
        self,
        session: AsyncSession,
        *,
        keys: list[tuple[int, int]],
    ) -> dict[tuple[int, int], int]: ...

    async def get_image_by_illust_page(
        self,
        session: AsyncSession,
        *,
        illust_id: int,
        page_index: int,
    ) -> Image | None: ...

    async def list_images(
        self,
        session: AsyncSession,
        *,
        limit: int,
        cursor: int | None = None,
        r18: int = 0,
        r18_strict: bool = True,
        orientation: int | None = None,
        ai_type: int | None = None,
        min_width: int = 0,
        min_height: int = 0,
        min_pixels: int = 0,
        included_tags: Sequence[str] | None = None,
        excluded_tags: Sequence[str] | None = None,
        user_id: int | None = None,
        illust_id: int | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
    ) -> tuple[list[Image], int | None]: ...

    async def list_admin_images(
        self,
        session: AsyncSession,
        *,
        limit: int,
        cursor: int | None = None,
        missing_keys: Sequence[str] | None = None,
    ) -> tuple[list[tuple[Image, int]], int | None]: ...

    async def list_authors(
        self,
        session: AsyncSession,
        *,
        limit: int,
        cursor: int | None = None,
        q: str | None = None,
    ) -> tuple[list[AuthorListItem], int | None]: ...

    async def mark_image_ok(self, engine: AsyncEngine, *, image_id: int, now: str) -> None: ...

    async def mark_image_failure(
        self,
        engine: AsyncEngine,
        *,
        image_id: int,
        now: str,
        error_code: str,
        error_message: str,
    ) -> None: ...

    async def heal_broken_images_for_illust(
        self,
        session: AsyncSession,
        *,
        illust_id: int,
        now: str,
    ) -> list[int]: ...

    async def set_status_for_import(
        self,
        session: AsyncSession,
        *,
        import_id: int,
        status: int,
        now_expr: object | None = None,
    ) -> list[int]: ...

    async def delete_images_by_ids(
        self,
        session: AsyncSession,
        *,
        image_ids: list[int],
    ) -> list[int]: ...

    async def clear_all_images(self, session: AsyncSession) -> int: ...


class SqliteCatalogStore:
    """Default catalog: existing SQLite helpers (behavior unchanged)."""

    backend: str = "sqlite"

    async def upsert_image_by_illust_page(
        self,
        session: AsyncSession,
        *,
        illust_id: int,
        page_index: int,
        ext: str,
        original_url: str,
        proxy_path: str,
        random_key: float,
        created_import_id: int | None,
    ) -> int:
        return await upsert_image_by_illust_page(
            session,
            illust_id=illust_id,
            page_index=page_index,
            ext=ext,
            original_url=original_url,
            proxy_path=proxy_path,
            random_key=random_key,
            created_import_id=created_import_id,
        )

    async def upsert_hydrated_image_page(
        self,
        session: AsyncSession,
        *,
        illust_id: int,
        page_index: int,
        ext: str,
        original_url: str,
        random_key: float,
        width: int | None,
        height: int | None,
        aspect_ratio: float | None,
        orientation: int | None,
        x_restrict: int | None,
        ai_type: int | None,
        illust_type: int | None,
        user_id: int | None,
        user_name: str | None,
        title: str | None,
        created_at_pixiv: str | None,
        bookmark_count: int | None,
        view_count: int | None,
        comment_count: int | None,
        created_import_id: int | None,
    ) -> int:
        return await upsert_hydrated_image_page(
            session,
            illust_id=illust_id,
            page_index=page_index,
            ext=ext,
            original_url=original_url,
            random_key=random_key,
            width=width,
            height=height,
            aspect_ratio=aspect_ratio,
            orientation=orientation,
            x_restrict=x_restrict,
            ai_type=ai_type,
            illust_type=illust_type,
            user_id=user_id,
            user_name=user_name,
            title=title,
            created_at_pixiv=created_at_pixiv,
            bookmark_count=bookmark_count,
            view_count=view_count,
            comment_count=comment_count,
            created_import_id=created_import_id,
        )

    async def bulk_upsert_import_rows(
        self,
        session: AsyncSession,
        *,
        rows: list[dict],
        keys: list[tuple[int, int]],
        import_id: int,
    ) -> list[int]:
        return await bulk_upsert_import_rows(
            session,
            rows=rows,
            keys=keys,
            import_id=import_id,
        )

    async def get_image_by_id(self, session: AsyncSession, *, image_id: int) -> Image | None:
        return await get_image_by_id(session, image_id=image_id)

    async def get_image_by_id_any_status(self, session: AsyncSession, *, image_id: int) -> Image | None:
        return await get_image_by_id_any_status(session, image_id=image_id)

    async def get_images_by_ids(self, session: AsyncSession, *, image_ids: list[int]) -> list[Image]:
        return await get_images_by_ids(session, image_ids=image_ids)

    async def get_images_by_ids_any_status(
        self,
        session: AsyncSession,
        *,
        image_ids: list[int],
    ) -> list[Image]:
        return await get_images_by_ids_any_status(session, image_ids=image_ids)

    async def get_images_by_illust_id(
        self,
        session: AsyncSession,
        *,
        illust_id: int,
    ) -> list[Image]:
        return await get_images_by_illust_id(session, illust_id=illust_id)

    async def list_enabled_images(
        self,
        session: AsyncSession,
        *,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> list[Image]:
        return await list_enabled_images(session, limit=limit, after_id=after_id)

    async def count_enabled_images(self, session: AsyncSession) -> int:
        return await count_enabled_images(session)

    async def map_image_ids_by_illust_page(
        self,
        session: AsyncSession,
        *,
        keys: list[tuple[int, int]],
    ) -> dict[tuple[int, int], int]:
        return await map_image_ids_by_illust_page(session, keys=keys)

    async def get_image_by_illust_page(
        self,
        session: AsyncSession,
        *,
        illust_id: int,
        page_index: int,
    ) -> Image | None:
        return await get_image_by_illust_page(session, illust_id=illust_id, page_index=page_index)

    async def list_images(
        self,
        session: AsyncSession,
        *,
        limit: int,
        cursor: int | None = None,
        r18: int = 0,
        r18_strict: bool = True,
        orientation: int | None = None,
        ai_type: int | None = None,
        min_width: int = 0,
        min_height: int = 0,
        min_pixels: int = 0,
        included_tags: Sequence[str] | None = None,
        excluded_tags: Sequence[str] | None = None,
        user_id: int | None = None,
        illust_id: int | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
    ) -> tuple[list[Image], int | None]:
        return await list_images_helper(
            session,
            limit=limit,
            cursor=cursor,
            r18=r18,
            r18_strict=r18_strict,
            orientation=orientation,
            ai_type=ai_type,
            min_width=min_width,
            min_height=min_height,
            min_pixels=min_pixels,
            included_tags=included_tags,
            excluded_tags=excluded_tags,
            user_id=user_id,
            illust_id=illust_id,
            created_from=created_from,
            created_to=created_to,
        )

    async def list_admin_images(
        self,
        session: AsyncSession,
        *,
        limit: int,
        cursor: int | None = None,
        missing_keys: Sequence[str] | None = None,
    ) -> tuple[list[tuple[Image, int]], int | None]:
        return await list_admin_images_helper(
            session,
            limit=limit,
            cursor=cursor,
            missing_keys=missing_keys,
        )

    async def list_authors(
        self,
        session: AsyncSession,
        *,
        limit: int,
        cursor: int | None = None,
        q: str | None = None,
    ) -> tuple[list[AuthorListItem], int | None]:
        return await list_authors_helper(session, limit=limit, cursor=cursor, q=q)

    async def mark_image_ok(self, engine: AsyncEngine, *, image_id: int, now: str) -> None:
        await mark_image_ok(engine, image_id=image_id, now=now)

    async def mark_image_failure(
        self,
        engine: AsyncEngine,
        *,
        image_id: int,
        now: str,
        error_code: str,
        error_message: str,
    ) -> None:
        await mark_image_failure(
            engine,
            image_id=image_id,
            now=now,
            error_code=error_code,
            error_message=error_message,
        )

    async def heal_broken_images_for_illust(
        self,
        session: AsyncSession,
        *,
        illust_id: int,
        now: str,
    ) -> list[int]:
        return await heal_broken_images_for_illust(session, illust_id=illust_id, now=now)

    async def set_status_for_import(
        self,
        session: AsyncSession,
        *,
        import_id: int,
        status: int,
        now_expr: object | None = None,
    ) -> list[int]:
        return await set_status_for_import(
            session,
            import_id=import_id,
            status=status,
            now_expr=now_expr,
        )

    async def delete_images_by_ids(
        self,
        session: AsyncSession,
        *,
        image_ids: list[int],
    ) -> list[int]:
        return await delete_images_by_ids(session, image_ids=image_ids)

    async def clear_all_images(self, session: AsyncSession) -> int:
        return await clear_all_images(session)


# Postgres dialect uses the same SQLAlchemy helpers for now (dialect-neutral where possible).
# A dedicated PostgresCatalogStore can diverge later (e.g. ON CONFLICT syntax, types).
class PostgresCatalogStore(SqliteCatalogStore):
    backend: str = "postgres"


def catalog_backend_from_database_url(database_url: str) -> str:
    """Map DATABASE_URL dialect → catalog backend label (sqlite | postgres | other)."""
    return backend_from_database_url(database_url)


def build_catalog_store(*, database_url: str = "") -> CatalogStore:
    """Build catalog store for the configured DB dialect.

    Today both sqlite and postgres use the shared SQLAlchemy image helpers.
    Backend label is exposed for healthz / ops; no automatic schema migration.
    """
    backend = catalog_backend_from_database_url(database_url)
    if backend == "postgres":
        return PostgresCatalogStore()
    return SqliteCatalogStore()
