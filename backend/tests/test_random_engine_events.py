from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import load_settings
from app.core.random_engine_client import engine_apply_events
from app.core.random_engine_sync import (
    build_delete_events,
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

        async def post(self, url: str, json: dict[str, Any], timeout: float) -> _Resp:
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
