from __future__ import annotations

import asyncio
import random
from types import SimpleNamespace

from app.core.random_strategy import pick_many_with_strategy


def _image(image_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=image_id,
        ai_type=0,
        illust_type=0,
        bookmark_count=image_id * 10,
        view_count=image_id * 100,
        comment_count=0,
        width=1000,
        height=1000,
        created_at_pixiv=None,
        added_at=None,
        user_id=image_id,
    )


class _FakePick:
    backend = "fake"

    def __init__(self, images: list[SimpleNamespace]) -> None:
        self.images = images
        self.calls: list[dict[str, object]] = []

    async def pick_many(self, _session, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(dict(kwargs))
        excluded = {int(value) for value in kwargs.get("exclude_image_ids") or []}
        limit = int(kwargs["limit"])
        return [image for image in self.images if int(image.id) not in excluded][:limit]

    async def pick_one(self, _session, **kwargs):  # type: ignore[no-untyped-def]
        rows = await self.pick_many(_session, limit=1, **kwargs)
        return rows[0] if rows else None

    async def count_candidates(self, _session, **_kwargs) -> int:  # type: ignore[no-untyped-def]
        return len(self.images)


def _context(*, strategy: str, strict: bool, samples: int = 4) -> SimpleNamespace:
    return SimpleNamespace(
        strategy_norm=strategy,
        rng=random.Random("feed-batch-golden-v1"),
        anti_repeat_enabled=True,
        recent_exclude_image_ids=[1, 2, 3],
        recent_image_ids={1, 2, 3},
        recent_author_ids=set(),
        dedup_strict=strict,
        dedup_image_penalty=1.0,
        dedup_author_penalty=1.0,
        pick_kwargs={},
        quality_samples_i=samples,
        multipliers={
            "ai": 1.0,
            "non_ai": 1.0,
            "unknown_ai": 1.0,
            "illust": 1.0,
            "manga": 1.0,
            "ugoira": 1.0,
            "unknown_illust_type": 1.0,
        },
        pick_mode_raw="best",
        temperature=1.0,
        score_weights={
            "bookmark": 1.0,
            "view": 0.0,
            "comment": 0.0,
            "pixels": 0.0,
            "bookmark_rate": 0.0,
            "freshness": 0.0,
            "bookmark_velocity": 0.0,
        },
        freshness_half_life_days=21.0,
        velocity_smooth_days=2.0,
        time_boost_enabled=False,
        debug_base={},
    )


def test_random_batch_soft_dedup_fills_partial_from_recent_rows() -> None:
    port = _FakePick([_image(i) for i in range(1, 6)])

    rows = asyncio.run(
        pick_many_with_strategy(
            session=object(),
            pick_ctx=_context(strategy="random", strict=False),
            limit=4,
            pick=port,
        )
    )

    assert [row.id for row in rows] == [4, 5, 1, 2]
    assert len(port.calls) == 2
    assert port.calls[0]["exclude_image_ids"] == [1, 2, 3]
    assert port.calls[1]["exclude_image_ids"] == [4, 5]


def test_random_batch_strict_dedup_returns_partial_without_fallback() -> None:
    port = _FakePick([_image(i) for i in range(1, 6)])

    rows = asyncio.run(
        pick_many_with_strategy(
            session=object(),
            pick_ctx=_context(strategy="random", strict=True),
            limit=4,
            pick=port,
        )
    )

    assert [row.id for row in rows] == [4, 5]
    assert len(port.calls) == 1


def test_quality_batch_has_bounded_shared_window_and_soft_fill() -> None:
    port = _FakePick([_image(i) for i in range(1, 7)])

    rows = asyncio.run(
        pick_many_with_strategy(
            session=object(),
            pick_ctx=_context(strategy="quality", strict=False, samples=6),
            limit=4,
            pick=port,
        )
    )

    assert [row.id for row in rows] == [6, 5, 4, 3]
    assert len(port.calls) == 2
    assert port.calls[0]["limit"] == 6
    assert port.calls[1]["limit"] == 3


def test_random_batch_fixed_seed_delegates_one_ring_start() -> None:
    port = _FakePick([_image(i) for i in range(1, 7)])
    ctx = _context(strategy="random", strict=False)
    ctx.anti_repeat_enabled = False
    ctx.recent_exclude_image_ids = []
    expected_r = random.Random("feed-batch-golden-v1").random()

    rows = asyncio.run(pick_many_with_strategy(session=object(), pick_ctx=ctx, limit=4, pick=port))

    assert [row.id for row in rows] == [1, 2, 3, 4]
    assert len(port.calls) == 1
    assert float(port.calls[0]["r"]) == expected_r
