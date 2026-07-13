from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import text

from app.core.config import load_settings
from app.core.random_engine_client import engine_apply_events
from app.core.random_engine_sync import (
    _snapshot_ack_matches,
    build_delete_events,
    build_engine_snapshot_payload,
    build_upsert_events,
    engine_snapshot_content_hash,
    image_row_to_engine_payload,
    maybe_publish_engine_deletes,
    maybe_publish_engine_upserts,
    push_engine_snapshot,
)
from app.db.catalog import SqliteCatalogStore
from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.images import Image
from app.db.session import create_sessionmaker


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


def _snapshot_image(image_id: int) -> dict[str, Any]:
    if image_id == 2:
        return {
            "id": 2,
            "illust_id": 20,
            "page_index": 1,
            "ext": "png",
            "status": 1,
            "random_key": 0.75,
            "user_name": "作者",
            "title": "<猫>",
            "tag_names": ["猫", "blue"],
        }
    return {
        "id": int(image_id),
        "illust_id": 10,
        "page_index": 0,
        "ext": "jpg",
        "status": 1,
        "random_key": 0.25,
        "tag_names": [],
    }


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
        async def count_enabled_images(self, _session: Any) -> int:
            return len(all_ids)

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
        assert built["authoritative_count"] == 5
        assert built["content_hash"] == engine_snapshot_content_hash(built["images"])
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


def test_engine_snapshot_content_hash_is_order_independent_and_rejects_duplicates() -> None:
    expected = "e247ea0d8ba8edffe3d4bd63e3f9475841e17c88b7bc8861e96fc668d41fd0a2"
    assert engine_snapshot_content_hash([_snapshot_image(2), _snapshot_image(1)]) == expected
    changed = _snapshot_image(2)
    changed["title"] = "different"
    assert engine_snapshot_content_hash([changed, _snapshot_image(1)]) != expected
    with pytest.raises(ValueError, match="duplicate"):
        engine_snapshot_content_hash([_snapshot_image(1), _snapshot_image(1)])


def test_snapshot_ack_requires_exact_manifest_and_next_version() -> None:
    expected = {
        "ok": True,
        "ready": True,
        "index_size": 1,
        "revision": "rev-1",
        "content_hash": "abc",
        "state_version": 8,
    }
    assert _snapshot_ack_matches(
        expected,
        revision="rev-1",
        expected_count=1,
        content_hash="abc",
        base_state_version=7,
    )
    for field, value in {
        "ready": False,
        "index_size": 2,
        "revision": "wrong",
        "content_hash": "wrong",
        "state_version": 9,
    }.items():
        mismatch = dict(expected)
        mismatch[field] = value
        assert not _snapshot_ack_matches(
            mismatch,
            revision="rev-1",
            expected_count=1,
            content_hash="abc",
            base_state_version=7,
        )


def test_random_engine_contract_separates_snapshot_and_event_status() -> None:
    contract_path = Path(__file__).resolve().parents[2] / "contracts" / "random-engine.openapi.yaml"
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    schemas = contract["components"]["schemas"]
    snapshot_items = schemas["SnapshotEnvelope"]["properties"]["images"]["items"]
    assert snapshot_items["$ref"].endswith("/SnapshotIndexImage")
    snapshot_status = schemas["SnapshotIndexImage"]["allOf"][1]["properties"]["status"]
    assert snapshot_status["enum"] == [1]
    assert schemas["IndexImage"]["properties"]["status"]["enum"] == [1, 2, 3, 4]
    assert schemas["CatalogEvent"]["properties"]["image"]["$ref"].endswith("/IndexImage")


def test_push_engine_snapshot_requires_authoritative_count_and_manifest() -> None:
    class _Im(_FakeImage):
        def __init__(self, image_id: int) -> None:
            super().__init__()
            self.id = image_id
            self.illust_id = image_id

    class _Tags:
        async def map_tag_names_by_image_ids(
            self, _session: Any, *, image_ids: list[int]
        ) -> dict[int, list[str]]:
            return {image_id: [] for image_id in image_ids}

    class _Catalog:
        def __init__(self, *, authoritative_count: int) -> None:
            self.authoritative_count = authoritative_count

        async def count_enabled_images(self, _session: Any) -> int:
            return self.authoritative_count

        async def list_enabled_images(
            self, _session: Any, *, limit: int | None = None, after_id: int | None = None
        ) -> list[_Im]:
            if after_id is not None:
                return []
            return [_Im(1)]

    class _Resp:
        def __init__(self, data: dict[str, Any]) -> None:
            self.status_code = 200
            self.text = ""
            self._data = data

        def json(self) -> dict[str, Any]:
            return self._data

    class _Client:
        def __init__(self, *, ack_revision: str | None = None) -> None:
            self.posts: list[dict[str, Any]] = []
            self.ack_revision = ack_revision

        async def get(self, *_args: Any, **_kwargs: Any) -> _Resp:
            return _Resp({"ok": True, "state_version": 7})

        async def post(self, _url: str, *, json: dict[str, Any], **_kwargs: Any) -> _Resp:
            self.posts.append(json)
            return _Resp(
                {
                    "ok": True,
                    "ready": True,
                    "index_size": json["expected_count"],
                    "revision": self.ack_revision or json["revision"],
                    "content_hash": json["content_hash"],
                    "state_version": json["base_state_version"] + 1,
                }
            )

    async def _run() -> None:
        engine = create_engine("sqlite+aiosqlite:///:memory:")
        try:
            incomplete_client = _Client()
            incomplete = await push_engine_snapshot(
                engine,
                base_url="http://engine.local",
                client=incomplete_client,
                revision="rev-incomplete",
                catalog=_Catalog(authoritative_count=2),  # type: ignore[arg-type]
                tag_store=_Tags(),  # type: ignore[arg-type]
            )
            assert incomplete is None
            assert incomplete_client.posts == []

            client = _Client()
            result = await push_engine_snapshot(
                engine,
                base_url="http://engine.local",
                client=client,
                revision="rev-complete",
                catalog=_Catalog(authoritative_count=1),  # type: ignore[arg-type]
                tag_store=_Tags(),  # type: ignore[arg-type]
            )
            assert result is not None
            assert len(client.posts) == 1
            body = client.posts[0]
            assert body["complete"] is True
            assert body["expected_count"] == 1
            assert body["base_state_version"] == 7
            assert body["content_hash"] == engine_snapshot_content_hash(body["images"])

            mismatch_client = _Client(ack_revision="wrong-revision")
            mismatch = await push_engine_snapshot(
                engine,
                base_url="http://engine.local",
                client=mismatch_client,
                revision="rev-ack-check",
                catalog=_Catalog(authoritative_count=1),  # type: ignore[arg-type]
                tag_store=_Tags(),  # type: ignore[arg-type]
            )
            assert mismatch is None
            assert len(mismatch_client.posts) == 1
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_snapshot_build_uses_one_sqlite_read_snapshot_during_same_count_replacement(
    tmp_path: Path, monkeypatch: Any
) -> None:
    db_url = "sqlite+aiosqlite:///" + (tmp_path / "engine_snapshot_consistent.db").as_posix()
    first_page_read = asyncio.Event()
    writer_done = asyncio.Event()

    class _Catalog(SqliteCatalogStore):
        async def list_enabled_images(
            self, session: Any, *, limit: int | None = None, after_id: int | None = None
        ) -> list[Image]:
            rows = await super().list_enabled_images(session, limit=limit, after_id=after_id)
            if after_id is None:
                first_page_read.set()
                await writer_done.wait()
            return rows

    class _Tags:
        async def map_tag_names_by_image_ids(
            self, _session: Any, *, image_ids: list[int]
        ) -> dict[int, list[str]]:
            return {image_id: [] for image_id in image_ids}

    class _Resp:
        def __init__(self, data: dict[str, Any]) -> None:
            self.status_code = 200
            self.text = ""
            self._data = data

        def json(self) -> dict[str, Any]:
            return self._data

    class _Client:
        def __init__(self) -> None:
            self.posted_ids: list[int] = []

        async def get(self, *_args: Any, **_kwargs: Any) -> _Resp:
            return _Resp({"ok": True, "state_version": 0})

        async def post(self, _url: str, *, json: dict[str, Any], **_kwargs: Any) -> _Resp:
            self.posted_ids = [int(image["id"]) for image in json["images"]]
            return _Resp(
                {
                    "ok": True,
                    "ready": True,
                    "index_size": json["expected_count"],
                    "revision": json["revision"],
                    "content_hash": json["content_hash"],
                    "state_version": 1,
                }
            )

    def _image(image_id: int) -> Image:
        return Image(
            id=image_id,
            illust_id=image_id * 10,
            page_index=0,
            ext="jpg",
            original_url=f"https://i.pximg.net/{image_id}.jpg",
            proxy_path=f"/{image_id}.jpg",
            random_key=image_id / 10.0,
            status=1,
        )

    async def _run() -> None:
        engine = create_engine(db_url)
        Session = create_sessionmaker(engine)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                await conn.execute(text("PRAGMA journal_mode=WAL"))
            async with Session() as session:
                session.add_all([_image(1), _image(2)])
                await session.commit()

            async def _replace_same_count() -> None:
                await first_page_read.wait()
                async with Session() as session:
                    old = await session.get(Image, 2)
                    assert old is not None
                    await session.delete(old)
                    session.add(_image(3))
                    await session.commit()
                writer_done.set()

            monkeypatch.setattr("app.core.random_engine_sync._ENGINE_SNAPSHOT_PAGE", 1)
            client = _Client()
            writer = asyncio.create_task(_replace_same_count())
            result = await push_engine_snapshot(
                engine,
                base_url="http://engine.local",
                client=client,
                revision="consistent",
                catalog=_Catalog(),
                tag_store=_Tags(),  # type: ignore[arg-type]
            )
            await writer
            assert result is not None
            assert client.posted_ids == [1, 2]
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_push_engine_snapshot_rejects_partial_limit_before_loading_or_posting() -> None:
    class _Client:
        async def post(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("partial snapshot must not be posted")

    async def _run() -> None:
        with pytest.raises(ValueError, match="partial snapshot"):
            await push_engine_snapshot(
                None,  # type: ignore[arg-type]
                base_url="http://engine.local",
                client=_Client(),
                revision="partial-test",
                limit=3,
            )

    asyncio.run(_run())


def test_push_engine_snapshot_rejects_empty_revision_before_preflight() -> None:
    class _Client:
        async def get(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("invalid revision must not reach Engine health")

    async def _run() -> None:
        with pytest.raises(ValueError, match="revision"):
            await push_engine_snapshot(
                None,  # type: ignore[arg-type]
                base_url="http://engine.local",
                client=_Client(),
                revision="  ",
            )

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
