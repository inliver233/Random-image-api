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
from app.core.random_request import prefer_edge_browser_redirect_from_query


def test_build_edge_redirect_response_headers() -> None:
    resp = build_edge_redirect_response(edge_url="https://img.example.com/u/1/sig/path")
    assert resp.status_code == 302
    assert resp.headers.get("location") == "https://img.example.com/u/1/sig/path"
    assert resp.headers.get("x-image-edge") == "1"
    assert resp.headers.get("cache-control") == "no-store"


def test_prefer_edge_browser_redirect_from_query() -> None:
    assert prefer_edge_browser_redirect_from_query({}) is False
    assert prefer_edge_browser_redirect_from_query({"redirect": "0"}) is False
    assert prefer_edge_browser_redirect_from_query({"redirect": "1"}) is True
    assert prefer_edge_browser_redirect_from_query({"edge_redirect": "true"}) is True
    assert prefer_edge_browser_redirect_from_query({"local": "1"}) is False


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


def test_deliver_random_image_stream_edge_stream_h0(monkeypatch: pytest.MonkeyPatch) -> None:
    """H0: prefer edge → same-origin stream via signed img-worker (not browser 302)."""
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

    async def pick(*, session, exclude_image_ids=None, skip_engine=False):  # type: ignore[no-untyped-def]
        return image, {"picked_by": "test"}

    monkeypatch.setattr(
        "app.core.random_delivery.resolve_image_edge_redirect_url",
        lambda **kwargs: "https://img.example.com/u/1/sig/b64",
    )
    monkeypatch.setattr("app.core.random_delivery.needs_opportunistic_hydrate", lambda _img: False)

    streamed: list[str] = []

    class _FakeResp:
        status_code = 200
        headers: dict[str, str] = {}

    async def _stream_url(url, **kwargs):  # type: ignore[no-untyped-def]
        streamed.append(str(url))
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
            prefer_edge_stream=True,
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

    before = IMAGE_DELIVERY_TOTAL.labels(path="edge_stream")._value.get()
    before_redirect = IMAGE_DELIVERY_TOTAL.labels(path="edge_redirect")._value.get()
    resp = asyncio.run(_run())
    assert isinstance(resp, _FakeResp)
    assert streamed == ["https://img.example.com/u/1/sig/b64"]
    assert resp.headers.get("X-Image-Edge") == "stream"
    # Stream proved bytes → mark_ok scheduled (last_ok_at was None / error).
    assert len(bg.tasks) == 1
    after = IMAGE_DELIVERY_TOTAL.labels(path="edge_stream")._value.get()
    after_redirect = IMAGE_DELIVERY_TOTAL.labels(path="edge_redirect")._value.get()
    assert after == before + 1.0
    assert after_redirect == before_redirect


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

    async def pick(*, session, exclude_image_ids=None, skip_engine=False):  # type: ignore[no-untyped-def]
        return image, {"picked_by": "test"}

    monkeypatch.setattr(
        "app.core.random_delivery.resolve_image_edge_redirect_url",
        lambda **kwargs: None,
    )
    monkeypatch.setattr("app.core.random_delivery.needs_opportunistic_hydrate", lambda _img: False)

    async def _prepare(**kwargs):  # type: ignore[no-untyped-def]
        return ("https://origin.example/img.jpg", "http://proxy.example:8080")

    monkeypatch.setattr("app.core.random_delivery.prepare_origin_stream", _prepare)

    class _FakeResp:
        status_code = 200

    stream_kwargs: list[dict] = []

    async def _stream_url(*args, **kwargs):  # type: ignore[no-untyped-def]
        stream_kwargs.append(dict(kwargs))
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
            httpx_transport=object(),
            httpx_client=object(),
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
    assert stream_kwargs[0]["proxy"] == "http://proxy.example:8080"
    assert stream_kwargs[0]["transport"] is None
    assert stream_kwargs[0]["client"] is None
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

    async def pick(*, session, exclude_image_ids=None, skip_engine=False):  # type: ignore[no-untyped-def]
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


def test_deliver_random_image_stream_engine_dto_marks_ok_on_edge_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H0 edge stream proves bytes → mark_ok for engine DTO (unlike old browser 302)."""
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

    async def pick(*, session, exclude_image_ids=None, skip_engine=False):  # type: ignore[no-untyped-def]
        return image, {"picked_by": "engine"}

    monkeypatch.setattr(
        "app.core.random_delivery.resolve_image_edge_redirect_url",
        lambda **kwargs: "https://img.example.com/u/1/sig/b64",
    )
    monkeypatch.setattr("app.core.random_delivery.needs_opportunistic_hydrate", lambda _img: False)

    class _FakeResp:
        status_code = 200
        headers: dict[str, str] = {}

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
            prefer_edge_stream=True,
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
    assert len(bg.tasks) == 1


def test_deliver_random_image_stream_sticky_skip_engine_on_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After first pick, retries must pass skip_engine=True (no N× dual-run)."""
    img1 = SimpleNamespace(
        id=11,
        illust_id=110,
        original_url="https://i.pximg.net/img-original/img/2021/02/03/04/05/06/11_p0.jpg",
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
    img2 = SimpleNamespace(
        id=12,
        illust_id=120,
        original_url="https://i.pximg.net/img-original/img/2021/02/03/04/05/06/12_p0.jpg",
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

    pick_flags: list[bool] = []
    n = {"i": 0}

    async def pick(*, session, exclude_image_ids=None, skip_engine=False):  # type: ignore[no-untyped-def]
        pick_flags.append(bool(skip_engine))
        n["i"] += 1
        return (img1 if n["i"] == 1 else img2), {"picked_by": "test", "skip_engine": skip_engine}

    monkeypatch.setattr("app.core.random_delivery.needs_opportunistic_hydrate", lambda _img: False)
    monkeypatch.setattr(
        "app.core.random_delivery.resolve_image_edge_redirect_url",
        lambda **kwargs: None,
    )

    async def _prepare(**kwargs):  # type: ignore[no-untyped-def]
        return ("https://origin.example/img.jpg", None)

    monkeypatch.setattr("app.core.random_delivery.prepare_origin_stream", _prepare)

    class _FakeResp:
        status_code = 200

    stream_n = {"i": 0}

    async def _stream_url(*args, **kwargs):  # type: ignore[no-untyped-def]
        stream_n["i"] += 1
        if stream_n["i"] == 1:
            raise ApiError(
                code=ErrorCode.UPSTREAM_403,
                message="blocked",
                status_code=403,
            )
        return _FakeResp()

    monkeypatch.setattr("app.core.random_delivery.stream_url", _stream_url)

    async def _mark_fail(*_a, **_k):  # type: ignore[no-untyped-def]
        return None

    async def _mark_ok(*_a, **_k):  # type: ignore[no-untyped-def]
        return None

    class _Cat:
        mark_image_failure = staticmethod(_mark_fail)
        mark_image_ok = staticmethod(_mark_ok)

    monkeypatch.setattr("app.core.random_delivery.resolve_catalog_store", lambda _c=None: _Cat())

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
            attempts=3,
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
    assert pick_flags == [False, True], f"expected sticky skip_engine, got {pick_flags}"
