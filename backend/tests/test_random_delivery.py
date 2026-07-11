from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks
from fastapi.responses import RedirectResponse

from app.core.errors import ApiError, ErrorCode
from app.core.random_delivery import (
    attach_background,
    build_edge_redirect_response,
    deliver_random_image_stream,
    schedule_edge_side_effects,
)


def test_build_edge_redirect_response_headers() -> None:
    resp = build_edge_redirect_response(edge_url="https://img.example.com/u/1/sig/path")
    assert resp.status_code == 302
    assert resp.headers.get("location") == "https://img.example.com/u/1/sig/path"
    assert resp.headers.get("x-image-edge") == "1"
    assert resp.headers.get("cache-control") == "no-store"


def test_attach_background_sets_when_missing() -> None:
    resp = RedirectResponse(url="/x", status_code=302)
    bg = BackgroundTasks()
    out = attach_background(resp, bg)
    assert out is resp
    assert getattr(resp, "background", None) is bg


def test_schedule_edge_side_effects_skips_mark_ok_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple] = []
    bg = BackgroundTasks()

    def _record(self, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(("recent", kwargs))

    monkeypatch.setattr("app.core.recent_dedup.MemoryRecentDedup.record", _record)

    schedule_edge_side_effects(
        background_tasks=bg,
        engine=object(),
        image_id=7,
        illust_id=99,
        user_id=3,
        anti_repeat_enabled=True,
        dedup_window_s=60.0,
        dedup_max_images=100,
        dedup_max_authors=50,
        needs_hydrate=True,
        hydrate_reason="random",
        mark_ok_on_edge=False,
        should_mark_ok=True,
    )
    assert any(c[0] == "recent" for c in calls)
    # hydrate is scheduled as background task; mark_ok must not be scheduled when mark_ok_on_edge=False
    assert len(bg.tasks) == 1
    task_fn = bg.tasks[0].func
    assert getattr(task_fn, "__name__", "") == "best_effort"


def test_observe_image_delivery_counts_edge_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.metrics import IMAGE_DELIVERY_TOTAL, observe_image_delivery

    before = IMAGE_DELIVERY_TOTAL.labels(path="edge_redirect")._value.get()
    observe_image_delivery(path="edge_redirect")
    observe_image_delivery(path="not_a_path")
    after = IMAGE_DELIVERY_TOTAL.labels(path="edge_redirect")._value.get()
    assert after == before + 1.0

    before_miss = IMAGE_DELIVERY_TOTAL.labels(path="edge_unavailable")._value.get()
    observe_image_delivery(path="edge_unavailable")
    after_miss = IMAGE_DELIVERY_TOTAL.labels(path="edge_unavailable")._value.get()
    assert after_miss == before_miss + 1.0


def test_deliver_random_image_stream_edge_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    image = SimpleNamespace(
        id=1,
        illust_id=10,
        original_url="https://i.pximg.net/img-original/img/2021/02/03/04/05/06/10_p0.jpg",
        user_id=5,
        last_ok_at=None,
        last_error_code="UPSTREAM_403",
        width=100,
        height=100,
        x_restrict=0,
        ai_type=0,
        user_name="u",
        title="t",
        created_at_pixiv="2021-01-01T00:00:00Z",
        bookmark_count=1,
        view_count=1,
        comment_count=0,
    )

    class _SessionCtx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            return False

    def Session():  # noqa: N802
        return _SessionCtx()

    async def pick(*, session, exclude_image_ids=None):  # type: ignore[no-untyped-def]
        return image, {"picked_by": "test"}

    monkeypatch.setattr(
        "app.core.random_delivery.resolve_image_edge_redirect_url",
        lambda **kwargs: "https://img.example.com/u/1/sig/b64",
    )
    monkeypatch.setattr("app.core.random_delivery.needs_opportunistic_hydrate", lambda _img: False)

    bg = BackgroundTasks()

    def no_match() -> ApiError:
        return ApiError(code=ErrorCode.NOT_FOUND, message="none", status_code=404)

    async def _run():
        return await deliver_random_image_stream(
            pick=pick,
            Session=Session,
            engine=object(),
            settings=object(),
            runtime=object(),
            httpx_transport=None,
            httpx_client=None,
            range_header=None,
            attempts=1,
            prefer_edge_redirect=True,
            use_pixiv_cat=False,
            mirror_host="i.pixiv.cat",
            anti_repeat_enabled=False,
            dedup_window_s=60.0,
            dedup_max_images=10,
            dedup_max_authors=10,
            background_tasks=bg,
            no_match_error=no_match,
        )

    from app.core.metrics import IMAGE_DELIVERY_TOTAL

    before = IMAGE_DELIVERY_TOTAL.labels(path="edge_redirect")._value.get()
    resp = asyncio.run(_run())
    assert isinstance(resp, RedirectResponse)
    assert resp.status_code == 302
    assert resp.headers.get("x-image-edge") == "1"
    assert (resp.headers.get("location") or "").startswith("https://img.example.com/")
    # No mark_ok background task when edge 302 (hydrate false, anti_repeat false)
    assert len(bg.tasks) == 0
    after = IMAGE_DELIVERY_TOTAL.labels(path="edge_redirect")._value.get()
    assert after == before + 1.0


def test_deliver_random_image_stream_edge_unavailable_falls_to_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """prefer_edge=True but no edge URL → count edge_unavailable then local_stream."""
    image = SimpleNamespace(
        id=2,
        illust_id=20,
        original_url="https://i.pximg.net/img-original/img/2021/02/03/04/05/06/20_p0.jpg",
        user_id=5,
        last_ok_at=None,
        last_error_code=None,
        width=100,
        height=100,
        x_restrict=0,
        ai_type=0,
        user_name="u",
        title="t",
        created_at_pixiv="2021-01-01T00:00:00Z",
        bookmark_count=1,
        view_count=1,
        comment_count=0,
    )

    class _SessionCtx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            return False

    def Session():  # noqa: N802
        return _SessionCtx()

    async def pick(*, session, exclude_image_ids=None):  # type: ignore[no-untyped-def]
        return image, {"picked_by": "test"}

    monkeypatch.setattr(
        "app.core.random_delivery.resolve_image_edge_redirect_url",
        lambda **kwargs: None,
    )
    monkeypatch.setattr("app.core.random_delivery.needs_opportunistic_hydrate", lambda _img: False)

    async def _prepare(**kwargs):  # type: ignore[no-untyped-def]
        return ("https://origin.example/img.jpg", None)

    monkeypatch.setattr("app.core.random_delivery.prepare_origin_stream", _prepare)

    class _FakeResp:
        status_code = 200

    async def _stream_url(*args, **kwargs):  # type: ignore[no-untyped-def]
        return _FakeResp()

    monkeypatch.setattr("app.core.random_delivery.stream_url", _stream_url)

    bg = BackgroundTasks()

    def no_match() -> ApiError:
        return ApiError(code=ErrorCode.NOT_FOUND, message="none", status_code=404)

    async def _run():
        return await deliver_random_image_stream(
            pick=pick,
            Session=Session,
            engine=object(),
            settings=object(),
            runtime=object(),
            httpx_transport=None,
            httpx_client=None,
            range_header=None,
            attempts=1,
            prefer_edge_redirect=True,
            use_pixiv_cat=False,
            mirror_host="i.pixiv.cat",
            anti_repeat_enabled=False,
            dedup_window_s=60.0,
            dedup_max_images=10,
            dedup_max_authors=10,
            background_tasks=bg,
            no_match_error=no_match,
        )

    from app.core.metrics import IMAGE_DELIVERY_TOTAL

    before_miss = IMAGE_DELIVERY_TOTAL.labels(path="edge_unavailable")._value.get()
    before_local = IMAGE_DELIVERY_TOTAL.labels(path="local_stream")._value.get()
    resp = asyncio.run(_run())
    assert isinstance(resp, _FakeResp)
    after_miss = IMAGE_DELIVERY_TOTAL.labels(path="edge_unavailable")._value.get()
    after_local = IMAGE_DELIVERY_TOTAL.labels(path="local_stream")._value.get()
    assert after_miss == before_miss + 1.0
    assert after_local == before_local + 1.0
    # Catalog row with last_ok_at=None → mark_ok scheduled after local stream.
    assert len(bg.tasks) == 1


def test_deliver_random_image_stream_engine_dto_marks_ok_on_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Engine DTO last_ok_at='engine' skips mark on edge/JSON but local stream proves bytes."""
    image = SimpleNamespace(
        id=3,
        illust_id=30,
        original_url="https://i.pximg.net/img-original/img/2021/02/03/04/05/06/30_p0.jpg",
        user_id=5,
        last_ok_at="engine",
        last_error_code=None,
        from_engine_item=True,
        width=100,
        height=100,
        x_restrict=0,
        ai_type=0,
        user_name="u",
        title="t",
        created_at_pixiv="2021-01-01T00:00:00Z",
        bookmark_count=1,
        view_count=1,
        comment_count=0,
    )

    class _SessionCtx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            return False

    def Session():  # noqa: N802
        return _SessionCtx()

    async def pick(*, session, exclude_image_ids=None):  # type: ignore[no-untyped-def]
        return image, {"picked_by": "engine"}

    monkeypatch.setattr(
        "app.core.random_delivery.resolve_image_edge_redirect_url",
        lambda **kwargs: None,
    )
    monkeypatch.setattr("app.core.random_delivery.needs_opportunistic_hydrate", lambda _img: False)

    async def _prepare(**kwargs):  # type: ignore[no-untyped-def]
        return ("https://origin.example/img.jpg", None)

    monkeypatch.setattr("app.core.random_delivery.prepare_origin_stream", _prepare)

    class _FakeResp:
        status_code = 200

    async def _stream_url(*args, **kwargs):  # type: ignore[no-untyped-def]
        return _FakeResp()

    monkeypatch.setattr("app.core.random_delivery.stream_url", _stream_url)

    bg = BackgroundTasks()

    def no_match() -> ApiError:
        return ApiError(code=ErrorCode.NOT_FOUND, message="none", status_code=404)

    async def _run():
        return await deliver_random_image_stream(
            pick=pick,
            Session=Session,
            engine=object(),
            settings=object(),
            runtime=object(),
            httpx_transport=None,
            httpx_client=None,
            range_header=None,
            attempts=1,
            prefer_edge_redirect=False,
            use_pixiv_cat=False,
            mirror_host="i.pixiv.cat",
            anti_repeat_enabled=False,
            dedup_window_s=60.0,
            dedup_max_images=10,
            dedup_max_authors=10,
            background_tasks=bg,
            no_match_error=no_match,
        )

    resp = asyncio.run(_run())
    assert isinstance(resp, _FakeResp)
    # Forced mark_ok for engine DTO after local stream proves bytes.
    assert len(bg.tasks) == 1
    assert getattr(bg.tasks[0].func, "__name__", "") == "best_effort"


def test_deliver_random_image_stream_engine_dto_skips_mark_ok_on_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even with from_engine_item, edge 302 must not schedule mark_ok."""
    image = SimpleNamespace(
        id=4,
        illust_id=40,
        original_url="https://i.pximg.net/img-original/img/2021/02/03/04/05/06/40_p0.jpg",
        user_id=5,
        last_ok_at="engine",
        last_error_code=None,
        from_engine_item=True,
        width=100,
        height=100,
        x_restrict=0,
        ai_type=0,
        user_name="u",
        title="t",
        created_at_pixiv="2021-01-01T00:00:00Z",
        bookmark_count=1,
        view_count=1,
        comment_count=0,
    )

    class _SessionCtx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            return False

    def Session():  # noqa: N802
        return _SessionCtx()

    async def pick(*, session, exclude_image_ids=None):  # type: ignore[no-untyped-def]
        return image, {"picked_by": "engine"}

    monkeypatch.setattr(
        "app.core.random_delivery.resolve_image_edge_redirect_url",
        lambda **kwargs: "https://img.example.com/u/1/sig/b64",
    )
    monkeypatch.setattr("app.core.random_delivery.needs_opportunistic_hydrate", lambda _img: False)

    bg = BackgroundTasks()

    def no_match() -> ApiError:
        return ApiError(code=ErrorCode.NOT_FOUND, message="none", status_code=404)

    async def _run():
        return await deliver_random_image_stream(
            pick=pick,
            Session=Session,
            engine=object(),
            settings=object(),
            runtime=object(),
            httpx_transport=None,
            httpx_client=None,
            range_header=None,
            attempts=1,
            prefer_edge_redirect=True,
            use_pixiv_cat=False,
            mirror_host="i.pixiv.cat",
            anti_repeat_enabled=False,
            dedup_window_s=60.0,
            dedup_max_images=10,
            dedup_max_authors=10,
            background_tasks=bg,
            no_match_error=no_match,
        )

    resp = asyncio.run(_run())
    assert isinstance(resp, RedirectResponse)
    assert len(bg.tasks) == 0
