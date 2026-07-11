from __future__ import annotations

from typing import Protocol, runtime_checkable

from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.images_get import get_image_by_id, get_images_by_ids
from app.db.images_mark import mark_image_failure, mark_image_ok
from app.db.images_upsert import upsert_image_by_illust_page
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

    async def get_image_by_id(self, session: AsyncSession, *, image_id: int) -> Image | None: ...

    async def get_images_by_ids(self, session: AsyncSession, *, image_ids: list[int]) -> list[Image]: ...

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

    async def get_image_by_id(self, session: AsyncSession, *, image_id: int) -> Image | None:
        return await get_image_by_id(session, image_id=image_id)

    async def get_images_by_ids(self, session: AsyncSession, *, image_ids: list[int]) -> list[Image]:
        return await get_images_by_ids(session, image_ids=image_ids)

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


# Postgres dialect uses the same SQLAlchemy helpers for now (dialect-neutral where possible).
# A dedicated PostgresCatalogStore can diverge later (e.g. ON CONFLICT syntax, types).
class PostgresCatalogStore(SqliteCatalogStore):
    backend: str = "postgres"


def catalog_backend_from_database_url(database_url: str) -> str:
    """Map DATABASE_URL dialect → catalog backend label (sqlite | postgres | other)."""
    raw = (database_url or "").strip()
    if not raw:
        return "sqlite"
    try:
        url = make_url(raw)
        name = (url.get_backend_name() or "").lower()
    except Exception:
        low = raw.lower()
        if low.startswith("postgres") or "postgresql" in low:
            return "postgres"
        if low.startswith("sqlite"):
            return "sqlite"
        return "other"
    if name.startswith("sqlite"):
        return "sqlite"
    if name.startswith("postgres"):
        return "postgres"
    return name or "other"


def build_catalog_store(*, database_url: str = "") -> CatalogStore:
    """Build catalog store for the configured DB dialect.

    Today both sqlite and postgres use the shared SQLAlchemy image helpers.
    Backend label is exposed for healthz / ops; no automatic schema migration.
    """
    backend = catalog_backend_from_database_url(database_url)
    if backend == "postgres":
        return PostgresCatalogStore()
    return SqliteCatalogStore()
