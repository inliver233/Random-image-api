from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.main import create_app


def test_admin_random_stats_tracks_only_random_requests(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_random_stats.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with app.state.engine.begin() as conn:
            await conn.exec_driver_sql(
                """
INSERT INTO images (illust_id,page_index,ext,original_url,proxy_path,random_key,status,x_restrict,width,height)
VALUES (123,0,'jpg','https://example.com/123.jpg','/i/123.jpg',0.5,1,0,100,100);
""".strip()
            )

    asyncio.run(_seed())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        # 1) success
        resp_ok = client.get("/random?format=json", headers={"X-Request-Id": "req_r1"})
        assert resp_ok.status_code == 200
        assert resp_ok.json()["ok"] is True

        # 2) no match (filtered out)
        resp_nm = client.get("/random?format=json&min_width=999999", headers={"X-Request-Id": "req_r2"})
        assert resp_nm.status_code == 404

        # 3) stats should reflect 2 /random requests (admin calls are not counted)
        resp = client.get(
            "/admin/api/stats/random",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_stats"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["request_id"] == "req_stats"

        stats = body["stats"]
        assert int(stats["total_requests"]) >= 2
        assert int(stats["total_ok"]) >= 1
        assert int(stats["total_error"]) >= 1
        assert int(stats["in_flight"]) == 0
        assert int(stats["last_window_requests"]) >= 2
