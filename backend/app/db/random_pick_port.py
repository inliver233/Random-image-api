from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.images import Image
from app.db.random_pick import count_pick_candidates, pick_random_image, pick_random_images


@runtime_checkable
class RandomPickPort(Protocol):
    """SQL ring-pick port for Python fallback (SQLite today; dialect label later).

    Preserves pick_random_image / pick_random_images filter semantics exactly.
    Go random-engine dual-run sits above this port (RandomService), not inside it.
    """

    backend: str

    async def pick_one(
        self,
        session: AsyncSession,
        *,
        r: float,
        exclude_image_ids: Sequence[int] | None = None,
        **pick_kwargs: Any,
    ) -> Image | None: ...

    async def pick_many(
        self,
        session: AsyncSession,
        *,
        r: float,
        limit: int,
        exclude_image_ids: Sequence[int] | None = None,
        **pick_kwargs: Any,
    ) -> list[Image]: ...

    async def count_candidates(
        self,
        session: AsyncSession,
        **pick_kwargs: Any,
    ) -> int: ...


class SqliteRandomPick:
    """Default: existing random_pick SQL helpers (behavior unchanged)."""

    backend: str = "sqlite"

    async def pick_one(
        self,
        session: AsyncSession,
        *,
        r: float,
        exclude_image_ids: Sequence[int] | None = None,
        **pick_kwargs: Any,
    ) -> Image | None:
        return await pick_random_image(
            session,
            r=float(r),
            exclude_image_ids=exclude_image_ids,
            **pick_kwargs,
        )

    async def pick_many(
        self,
        session: AsyncSession,
        *,
        r: float,
        limit: int,
        exclude_image_ids: Sequence[int] | None = None,
        **pick_kwargs: Any,
    ) -> list[Image]:
        return await pick_random_images(
            session,
            r=float(r),
            limit=int(limit),
            exclude_image_ids=exclude_image_ids,
            **pick_kwargs,
        )

    async def count_candidates(
        self,
        session: AsyncSession,
        **pick_kwargs: Any,
    ) -> int:
        return int(await count_pick_candidates(session, **pick_kwargs))


def build_random_pick(*, database_url: str = "") -> RandomPickPort:
    """Factory for pick backends. Only sqlite SQL path is implemented today."""
    _ = database_url  # reserved for postgres dialect label later
    return SqliteRandomPick()


def resolve_random_pick(pick: RandomPickPort | None = None) -> RandomPickPort:
    return pick if pick is not None else SqliteRandomPick()
