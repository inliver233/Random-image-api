from __future__ import annotations

import asyncio
from typing import Any

from app.core.random_delivery import should_mark_image_ok
from app.core.random_engine_pick import (
    image_from_engine_pick_item,
    resolve_engine_pick_images,
    try_pick_via_engine,
)
from app.core.random_strategy import needs_opportunistic_hydrate


def test_image_from_engine_pick_item_happy_path() -> None:
    img = image_from_engine_pick_item(
        {
            "id": 42,
            "illust_id": 100,
            "page_index": 0,
            "ext": "jpg",
            "original_url": "https://i.pximg.net/img-original/img/a.jpg",
            "width": 800,
            "height": 1200,
            "x_restrict": 0,
            "ai_type": 0,
            "illust_type": 0,
            "user_id": 7,
            "bookmark_count": 1,
            "view_count": 2,
            "comment_count": 3,
            "edge_path": "/i/42.jpg",
        }
    )
    assert img is not None
    assert img.id == 42
    assert img.ext == "jpg"
    assert img.original_url.startswith("https://")
    assert img.from_engine_item is True
    assert should_mark_image_ok(img) is False
    assert needs_opportunistic_hydrate(img) is False


def test_image_from_engine_pick_item_requires_url() -> None:
    assert image_from_engine_pick_item({"id": 1, "illust_id": 2, "ext": "jpg"}) is None
    assert image_from_engine_pick_item({"id": 1, "illust_id": 2, "ext": "jpg", "original_url": ""}) is None
    assert image_from_engine_pick_item({"id": 1, "illust_id": 2, "ext": "", "original_url": "x"}) is None


def test_resolve_engine_pick_images_prefers_dto_no_catalog_call() -> None:
    class _BoomStore:
        backend = "sqlite"

        async def get_images_by_ids(self, *_a: Any, **_k: Any) -> list[Any]:
            raise AssertionError("catalog rehydrate should not run when DTO is complete")

    item = {
        "id": 9,
        "illust_id": 90,
        "page_index": 0,
        "ext": "png",
        "original_url": "https://example.test/9.png",
        "width": 1,
        "height": 1,
        "x_restrict": 0,
        "ai_type": 0,
        "illust_type": 0,
        "user_id": 1,
        "bookmark_count": 0,
        "view_count": 0,
        "comment_count": 0,
    }

    async def _run() -> None:
        images, meta = await resolve_engine_pick_images(
            session=object(),  # type: ignore[arg-type]
            items=[item],
            catalog=_BoomStore(),  # type: ignore[arg-type]
        )
        assert len(images) == 1
        assert images[0].id == 9
        assert meta["engine_dto_count"] == 1
        assert meta["engine_rehydrate_count"] == 0
        assert meta["engine_rehydrate"] is False

    asyncio.run(_run())


def test_try_pick_via_engine_uses_dto(monkeypatch) -> None:
    item = {
        "id": 5,
        "illust_id": 50,
        "page_index": 0,
        "ext": "jpg",
        "original_url": "https://example.test/5.jpg",
        "width": 10,
        "height": 20,
        "x_restrict": 0,
        "ai_type": 1,
        "illust_type": 0,
        "user_id": 3,
        "bookmark_count": 4,
        "view_count": 5,
        "comment_count": 6,
    }

    async def _fake_pick(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"ok": True, "code": "OK", "items": [item]}

    monkeypatch.setattr("app.core.random_engine_pick.engine_pick", _fake_pick)

    class _BoomStore:
        backend = "sqlite"

        async def get_image_by_id(self, *_a: Any, **_k: Any) -> Any:
            raise AssertionError("single-id rehydrate should not run")

        async def get_images_by_ids(self, *_a: Any, **_k: Any) -> list[Any]:
            raise AssertionError("batch rehydrate should not run")

    async def _run() -> None:
        image, meta = await try_pick_via_engine(
            client=object(),
            base_url="http://engine.test",
            session=object(),  # type: ignore[arg-type]
            payload={"filters": {}, "strategy": "random", "limit": 1},
            catalog=_BoomStore(),  # type: ignore[arg-type]
        )
        assert image is not None
        assert image.id == 5
        assert meta["engine_status"] == "ok"
        assert meta["engine_dto_count"] == 1
        assert meta.get("engine_rehydrate") is False
        assert meta["picked_by"] == "random_engine"

    asyncio.run(_run())


def test_resolve_engine_pick_images_rehydrates_incomplete() -> None:
    class _FakeRow:
        def __init__(self) -> None:
            self.id = 3
            self.illust_id = 30
            self.page_index = 0
            self.ext = "jpg"
            self.original_url = "https://example.test/from-db.jpg"

    class _Store:
        backend = "sqlite"

        async def get_images_by_ids(self, session: Any, *, image_ids: list[int]) -> list[Any]:
            assert image_ids == [3]
            return [_FakeRow()]

    async def _run() -> None:
        images, meta = await resolve_engine_pick_images(
            session=object(),  # type: ignore[arg-type]
            items=[{"id": 3, "illust_id": 30, "ext": "jpg"}],  # missing original_url
            catalog=_Store(),  # type: ignore[arg-type]
        )
        assert len(images) == 1
        assert images[0].original_url.endswith("from-db.jpg")
        assert meta["engine_dto_count"] == 0
        assert meta["engine_rehydrate_count"] == 1
        assert meta["engine_rehydrate"] is True

    asyncio.run(_run())
