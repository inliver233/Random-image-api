from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import load_settings
from app.core.random_engine_client import engine_apply_events
from app.core.random_engine_sync import (
    build_delete_events,
    build_engine_snapshot_payload,
    build_upsert_events,
    image_row_to_engine_payload,
    maybe_publish_engine_deletes,
    maybe_publish_engine_upserts,
)


class _FakeImage:
    def __init__(self) -> None:
        self.id = 7
        self.illust_id = 100
        self.page_index = 0
        self.ext = "jpg"
        self.status = 1
        self.random_key = 0.42
        self.width = 100
        self.height = 200
        self.orientation = 1
        self.x_restrict = 0
        self.ai_type = 0
        self.illust_type = 0
        self.user_id = 9
        self.user_name = "u"
        self.title = "t"
        self.created_at_pixiv = "2020-01-01T00:00:00Z"
        self.added_at = "2020-02-01T00:00:00Z"
        self.bookmark_count = 1
        self.view_count = 2
        self.comment_count = 3
        self.original_url = "https://example.com/a.jpg"
        self.last_fail_at = None
        self.last_error_code = None


def test_image_row_to_engine_payload_shape() -> None:
    payload = image_row_to_engine_payload(_FakeImage(), tag_names=["foo", " bar ", ""])
    assert payload["id"] == 7
    assert payload["illust_id"] == 100
    assert payload["tag_names"] == ["foo", "bar"]
    assert payload["status"] == 1
    assert payload["random_key"] == 0.42
    assert payload["added_at"] == "2020-02-01T00:00:00Z"
    assert payload["created_at_pixiv"] == "2020-01-01T00:00:00Z"


def test_build_upsert_and_delete_events() -> None:
    ups = build_upsert_events([{"id": 1, "status": 1}, {"no_id": True}, {"id": 2}])
    assert ups == [
        {"type": "image_upserted", "image": {"id": 1, "status": 1}},
        {"type": "image_upserted", "image": {"id": 2}},
    ]
    dels = build_delete_events([1, 1, 0, -3, 2, "x", 2])
    assert dels == [
        {"type": "image_deleted", "image_id": 1},
        {"type": "image_deleted", "image_id": 2},
    ]


def test_build_engine_snapshot_payload_keyset_pages(monkeypatch: Any) -> None:
    """ENGINE-1: full snapshot walks list_enabled_images with after_id pages."""

    class _Im:
        def __init__(self, i: int) -> None:
            self.id = i
            self.illust_id = i
            self.page_index = 0
            self.ext = "jpg"
            self.status = 1
            self.random_key = 0.1
            self.width = 1
            self.height = 1
            self.orientation = 1
            self.x_restrict = 0
            self.ai_type = 0
            self.illust_type = 0
            self.user_id = 1
            self.user_name = "u"
            self.title = "t"
            self.created_at_pixiv = None
            self.added_at = None
            self.bookmark_count = 0
            self.view_count = 0
            self.comment_count = 0
            self.original_url = "https://i.pximg.net/a.jpg"
            self.last_fail_at = None
            self.last_error_code = None

    all_ids = [1, 2, 3, 4, 5]
    calls: list[dict[str, Any]] = []

    class _Store:
        async def list_enabled_images(
            self, _session: Any, *, limit: int | None = None, after_id: int | None = None
        ) -> list[_Im]:
            calls.append({"limit": limit, "after_id": after_id})
            start = 0
            if after_id is not None:
                for idx, i in enumerate(all_ids):
                    if i > int(after_id):
                        start = idx
                        break
                else:
                    return []
            batch = all_ids[start:]
            if limit is not None and int(limit) > 0:
                batch = batch[: int(limit)]
            return [_Im(i) for i in batch]

        async def get_images_by_ids_any_status(self, *_a: Any, **_k: Any) -> list[Any]:
            return []

        async def get_images_by_illust_id(self, *_a: Any, **_k: Any) -> list[Any]:
            return []

    class _Tags:
        async def map_tag_names_by_image_ids(
            self, _session: Any, *, image_ids: list[int]
        ) -> dict[int, list[str]]:
            return {int(i): [f"t{i}"] for i in image_ids}

    async def _run() -> None:
        built = await build_engine_snapshot_payload(
            None,  # type: ignore[arg-type]
            catalog=_Store(),  # type: ignore[arg-type]
            tag_store=_Tags(),  # type: ignore[arg-type]
            page_size=2,
        )
        assert built["count"] == 5
        assert [im["id"] for im in built["images"]] == all_ids
        assert [im["tag_names"] for im in built["images"]] == [["t1"], ["t2"], ["t3"], ["t4"], ["t5"]]
        # Three full pages of 2 then short final page of 1 (or 3 pages if last is size 1).
        assert len(calls) >= 3
        assert calls[0]["after_id"] is None
        assert calls[1]["after_id"] == 2
        assert calls[2]["after_id"] == 4

        limited = await build_engine_snapshot_payload(
            None,  # type: ignore[arg-type]
            catalog=_Store(),  # type: ignore[arg-type]
            tag_store=_Tags(),  # type: ignore[arg-type]
            page_size=2,
            limit=3,
        )
        assert limited["count"] == 3
        assert [im["id"] for im in limited["images"]] == [1, 2, 3]

    asyncio.run(_run())


def test_engine_apply_events_empty() -> None:
    async def _run() -> None:
        # No HTTP call for empty events.
        out = await engine_apply_events(None, "http://127.0.0.1:9", events=[])  # type: ignore[arg-type]
        assert out == {"ok": True, "applied": 0}

    asyncio.run(_run())


def test_maybe_publish_noop_without_url(monkeypatch: Any) -> None:
    async def _run() -> None:
        s = load_settings({"RANDOM_ENGINE_URL": "", "RANDOM_ENGINE_ENABLED": "1"})
        assert s.random_engine_url == ""
        out = await maybe_publish_engine_upserts(
            None,  # type: ignore[arg-type]
            image_ids=[1],
            settings=s,
        )
        assert out is None
        out2 = await maybe_publish_engine_deletes(image_ids=[1, 2], settings=s)
        assert out2 is None

    asyncio.run(_run())


def test_engine_apply_events_posts_json() -> None:
    class _Resp:
        status_code = 200
        text = '{"ok":true}'

        def json(self) -> dict[str, Any]:
            return {"ok": True, "applied": 1}

    class _Client:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        async def post(self, url: str, json: dict[str, Any], timeout: float, **_kw: Any) -> _Resp:
            self.calls.append((url, json))
            return _Resp()

    async def _run() -> None:
        client = _Client()
        out = await engine_apply_events(
            client,  # type: ignore[arg-type]
            "http://engine.local",
            events=[{"type": "image_deleted", "image_id": 9}],
            timeout_s=1.0,
        )
        assert out == {"ok": True, "applied": 1}
        assert client.calls[0][0] == "http://engine.local/v1/admin/events"
        assert client.calls[0][1]["events"][0]["image_id"] == 9

    asyncio.run(_run())
