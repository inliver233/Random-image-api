from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.core.config import load_settings
from app.core.r2_prewarm import (
    maybe_enqueue_r2_prewarm,
    normalize_prewarm_paths,
    paths_from_original_urls,
    r2_prewarm_enabled,
    r2_prewarm_secret,
)
from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.session import create_sessionmaker


class _FakeResp:
    def __init__(self, status_code: int = 200, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {"ok": True, "prewarmed": 1}
        self.text = str(self._payload)
        self.content = b"{}"

    def json(self) -> dict[str, Any]:
        return dict(self._payload)


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, *, json: Any = None, headers: Any = None, timeout: Any = None) -> _FakeResp:
        self.calls.append({"url": url, "json": json, "headers": dict(headers or {}), "timeout": timeout})
        return _FakeResp()


def test_normalize_prewarm_paths_allowlist() -> None:
    paths = normalize_prewarm_paths(
        [
            "/img-original/img/2020/01/01/00/00/00/1_p0.jpg",
            "/img-original/img/2020/01/01/00/00/00/1_p0.jpg",  # dedupe
            "/not-allowed/x.jpg",
            "https://evil.example/x",
            "/img-master/img/2020/01/01/00/00/00/2_p0.png",
        ]
    )
    assert paths == [
        "/img-original/img/2020/01/01/00/00/00/1_p0.jpg",
        "/img-master/img/2020/01/01/00/00/00/2_p0.png",
    ]


def test_paths_from_original_urls() -> None:
    out = paths_from_original_urls(
        [
            "https://i.pximg.net/img-original/img/2020/01/01/00/00/00/9_p0.jpg",
            "https://example.com/nope.jpg",
        ]
    )
    assert out == ["/img-original/img/2020/01/01/00/00/00/9_p0.jpg"]


def test_r2_prewarm_settings_secret_fallback() -> None:
    s = load_settings(
        {
            "R2_PREWARM_ENABLED": "1",
            "R2_PREWARM_URL": "https://img.example.com",
            "IMAGE_EDGE_SECRET": "edge-secret",
        }
    )
    assert r2_prewarm_enabled(s) is True
    assert r2_prewarm_secret(s) == "edge-secret"

    s2 = load_settings(
        {
            "R2_PREWARM_ENABLED": "1",
            "R2_PREWARM_URL": "https://img.example.com",
            "R2_PREWARM_SECRET": "prewarm-only",
            "IMAGE_EDGE_SECRET": "edge-secret",
        }
    )
    assert r2_prewarm_secret(s2) == "prewarm-only"


def test_maybe_enqueue_posts_paths_and_secret() -> None:
    from app.core.metrics import R2_PREWARM_TOTAL

    s = load_settings(
        {
            "R2_PREWARM_ENABLED": "1",
            "R2_PREWARM_URL": "https://img.example.com",
            "R2_PREWARM_SECRET": "pw-secret",
        }
    )
    client = _FakeClient()
    before_ok = R2_PREWARM_TOTAL.labels(result="ok")._value.get()  # type: ignore[attr-defined]

    async def _run() -> dict[str, Any] | None:
        return await maybe_enqueue_r2_prewarm(
            paths=["/img-original/img/2020/01/01/00/00/00/1_p0.jpg"],
            settings=s,
            client=client,
        )

    out = asyncio.run(_run())
    assert out is not None
    assert client.calls, "expected POST"
    call = client.calls[0]
    assert call["url"] == "https://img.example.com/v1/prewarm"
    assert call["json"] == {"paths": ["/img-original/img/2020/01/01/00/00/00/1_p0.jpg"]}
    assert call["headers"].get("X-Prewarm-Secret") == "pw-secret"
    # Must not send legacy image_ids shape to Worker.
    assert "image_ids" not in (call["json"] or {})
    after_ok = R2_PREWARM_TOTAL.labels(result="ok")._value.get()  # type: ignore[attr-defined]
    assert after_ok >= before_ok + 1


def test_maybe_enqueue_skipped_disabled_observes_metric() -> None:
    from app.core.metrics import R2_PREWARM_TOTAL

    s = load_settings({"R2_PREWARM_ENABLED": "0"})
    before = R2_PREWARM_TOTAL.labels(result="skipped_disabled")._value.get()  # type: ignore[attr-defined]

    async def _run() -> dict[str, Any] | None:
        return await maybe_enqueue_r2_prewarm(
            paths=["/img-original/img/2020/01/01/00/00/00/1_p0.jpg"],
            settings=s,
            client=_FakeClient(),
        )

    assert asyncio.run(_run()) is None
    after = R2_PREWARM_TOTAL.labels(result="skipped_disabled")._value.get()  # type: ignore[attr-defined]
    assert after >= before + 1


def test_maybe_enqueue_image_ids_resolves_via_catalog(tmp_path: Path) -> None:
    db_path = tmp_path / "r2_prewarm_ids.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()
    engine = create_engine(db_url)

    async def _seed() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.exec_driver_sql(
                """
INSERT INTO images (
  id, illust_id, page_index, ext, status, original_url, proxy_path, random_key
) VALUES (
  42, 100, 0, 'jpg', 1,
  'https://i.pximg.net/img-original/img/2020/01/01/00/00/00/100_p0.jpg',
  '/i/42.jpg', 0.5
)
""".strip()
            )

    asyncio.run(_seed())

    s = load_settings(
        {
            "R2_PREWARM_ENABLED": "1",
            "R2_PREWARM_URL": "https://img.example.com",
            "IMAGE_EDGE_SECRET": "edge-secret",
        }
    )
    client = _FakeClient()

    async def _run() -> dict[str, Any] | None:
        return await maybe_enqueue_r2_prewarm(
            image_ids=[42],
            settings=s,
            client=client,
            engine=engine,
        )

    out = asyncio.run(_run())
    assert out is not None
    assert client.calls
    assert client.calls[0]["json"] == {
        "paths": ["/img-original/img/2020/01/01/00/00/00/100_p0.jpg"]
    }
    assert client.calls[0]["headers"].get("X-Prewarm-Secret") == "edge-secret"
    asyncio.run(engine.dispose())


def test_maybe_enqueue_noop_without_engine_for_ids_only() -> None:
    s = load_settings(
        {
            "R2_PREWARM_ENABLED": "1",
            "R2_PREWARM_URL": "https://img.example.com",
            "IMAGE_EDGE_SECRET": "edge-secret",
        }
    )
    client = _FakeClient()

    async def _run() -> dict[str, Any] | None:
        return await maybe_enqueue_r2_prewarm(image_ids=[1, 2], settings=s, client=client)

    assert asyncio.run(_run()) is None
    assert client.calls == []


def test_maybe_enqueue_noop_when_disabled() -> None:
    s = load_settings({})
    client = _FakeClient()

    async def _run() -> dict[str, Any] | None:
        return await maybe_enqueue_r2_prewarm(
            paths=["/img-original/img/2020/01/01/00/00/00/1_p0.jpg"],
            settings=s,
            client=client,
        )

    assert asyncio.run(_run()) is None
    assert client.calls == []
