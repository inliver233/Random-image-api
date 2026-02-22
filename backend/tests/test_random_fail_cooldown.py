from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.db.models.base import Base
from app.db.models.images import Image
from app.db.session import create_sessionmaker
from app.main import create_app


def test_fail_cooldown_skips_recent_failures(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "random_fail_cooldown.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("RANDOM_FAIL_COOLDOWN_SECONDS", "3600")
    monkeypatch.setattr("app.api.public.random.random.random", lambda: 0.0)

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            session.add_all(
                [
                    Image(
                        illust_id=1,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/bad.jpg",
                        proxy_path="/i/1.jpg",
                        random_key=0.1,
                        x_restrict=0,
                    ),
                    Image(
                        illust_id=2,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/good.jpg",
                        proxy_path="/i/2.jpg",
                        random_key=0.2,
                        x_restrict=0,
                    ),
                ]
            )
            await session.commit()

        await app.state.engine.dispose()

    asyncio.run(_seed())

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/bad.jpg"):
            return httpx.Response(404, request=req)
        return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"ok", request=req)

    app.state.httpx_transport = httpx.MockTransport(handler)

    with TestClient(app) as client:
        first = client.get("/random?attempts=1", headers={"X-Request-Id": "req_test"})
        assert first.status_code == 502

        second = client.get("/random?attempts=1", headers={"X-Request-Id": "req_test"})
        assert second.status_code == 200
        assert second.content == b"ok"

    async def _assert_db() -> None:
        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            bad = (await session.get(Image, 1))
            assert bad is not None
            assert bad.last_fail_at is not None
            assert int(bad.fail_count) >= 1

    asyncio.run(_assert_db())

