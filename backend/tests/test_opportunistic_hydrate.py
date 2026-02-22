from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.db.models.base import Base
from app.main import create_app


def test_random_opportunistically_enqueues_hydrate_metadata_once(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "opportunistic_hydrate.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    illust_id = 123

    async def _migrate_and_seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.exec_driver_sql(
                """
                INSERT INTO images (illust_id, page_index, ext, original_url, proxy_path, random_key, x_restrict)
                VALUES (?, 0, 'jpg', 'https://example.com/x.jpg', '/i/1.jpg', 0.5, 0);
                """.strip(),
                (illust_id,),
            )

    asyncio.run(_migrate_and_seed())

    def _count_jobs() -> int:
        async def _op() -> int:
            async with app.state.engine.connect() as conn:
                result = await conn.exec_driver_sql(
                    """
                    SELECT COUNT(*) FROM jobs
                    WHERE type='hydrate_metadata'
                      AND ref_type='opportunistic_hydrate'
                      AND ref_id=?
                      AND status IN ('pending','running');
                    """.strip(),
                    (str(illust_id),),
                )
                return int(result.scalar_one())

        return asyncio.run(_op())

    def _fetch_job_fields() -> tuple[int, int, str]:
        async def _op() -> tuple[int, int, str]:
            async with app.state.engine.connect() as conn:
                result = await conn.exec_driver_sql(
                    """
                    SELECT priority, status, payload_json FROM jobs
                    WHERE type='hydrate_metadata' AND ref_type='opportunistic_hydrate' AND ref_id=?
                    ORDER BY id DESC LIMIT 1;
                    """.strip(),
                    (str(illust_id),),
                )
                row = result.fetchone()
                assert row is not None
                return (int(row[0]), 1 if str(row[1]) == "pending" else 0, str(row[2]))

        return asyncio.run(_op())

    with TestClient(app) as client:
        resp1 = client.get("/random?format=simple_json", headers={"X-Request-Id": "req_test"})
        assert resp1.status_code == 200
        body = resp1.json()
        assert body["ok"] is True
        assert body["data"]["image"]["illust_id"] == str(illust_id)

    assert _count_jobs() == 1
    priority, is_pending, payload_json = _fetch_job_fields()
    assert priority < 0
    assert is_pending == 1
    payload = json.loads(payload_json)
    assert payload["illust_id"] == illust_id
    assert payload["reason"] == "random"

    with TestClient(app) as client:
        resp2 = client.get("/random?format=simple_json", headers={"X-Request-Id": "req_test2"})
        assert resp2.status_code == 200

    assert _count_jobs() == 1

    async def _mark_running() -> None:
        async with app.state.engine.begin() as conn:
            await conn.exec_driver_sql(
                """
                UPDATE jobs SET status='running'
                WHERE type='hydrate_metadata' AND ref_type='opportunistic_hydrate' AND ref_id=?;
                """.strip(),
                (str(illust_id),),
            )

    asyncio.run(_mark_running())

    with TestClient(app) as client:
        resp3 = client.get("/random?format=simple_json", headers={"X-Request-Id": "req_test3"})
        assert resp3.status_code == 200

    assert _count_jobs() == 1


def test_image_proxy_opportunistically_enqueues_hydrate_metadata_once(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "opportunistic_hydrate_image_proxy.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)

    app = create_app()

    illust_id = 123

    async def _migrate_and_seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.exec_driver_sql(
                """
                INSERT INTO images (illust_id, page_index, ext, original_url, proxy_path, random_key, x_restrict)
                VALUES (?, 0, 'jpg', 'https://example.com/x.jpg', '/i/1.jpg', 0.5, 0);
                """.strip(),
                (illust_id,),
            )

    asyncio.run(_migrate_and_seed())

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers.get("Referer") == "https://www.pixiv.net/"
        return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"img-bytes")

    app.state.httpx_transport = httpx.MockTransport(handler)

    def _count_jobs() -> int:
        async def _op() -> int:
            async with app.state.engine.connect() as conn:
                result = await conn.exec_driver_sql(
                    """
                    SELECT COUNT(*) FROM jobs
                    WHERE type='hydrate_metadata'
                      AND ref_type='opportunistic_hydrate'
                      AND ref_id=?
                      AND status IN ('pending','running');
                    """.strip(),
                    (str(illust_id),),
                )
                return int(result.scalar_one())

        return asyncio.run(_op())

    def _fetch_payload_reason() -> str:
        async def _op() -> str:
            async with app.state.engine.connect() as conn:
                result = await conn.exec_driver_sql(
                    """
                    SELECT payload_json FROM jobs
                    WHERE type='hydrate_metadata' AND ref_type='opportunistic_hydrate' AND ref_id=?
                    ORDER BY id DESC LIMIT 1;
                    """.strip(),
                    (str(illust_id),),
                )
                row = result.fetchone()
                assert row is not None
                payload = json.loads(str(row[0] or "{}"))
                return str(payload.get("reason") or "")

        return asyncio.run(_op())

    with TestClient(app) as client:
        resp1 = client.get("/i/1.jpg", headers={"X-Request-Id": "req_test"})
        assert resp1.status_code == 200
        assert resp1.content == b"img-bytes"

    assert _count_jobs() == 1
    assert _fetch_payload_reason() == "image_proxy"

    with TestClient(app) as client:
        resp2 = client.get("/i/1.jpg", headers={"X-Request-Id": "req_test2"})
        assert resp2.status_code == 200

    assert _count_jobs() == 1
