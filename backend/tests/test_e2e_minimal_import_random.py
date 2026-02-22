from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.main import create_app
from app.worker import build_default_dispatcher, poll_and_execute_jobs


def test_e2e_import_10_then_random_json_ok_and_no_match(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "e2e_minimal.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)

    lines: list[str] = []
    for i in range(10):
        illust_id = 12345670 + i
        lines.append(
            f"https://i.pximg.net/img-original/img/2023/01/01/00/00/00/{illust_id}_p0.jpg"
        )

    with TestClient(app) as client:
        import_resp = client.post(
            "/admin/api/imports",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"text": "\n".join(lines), "dry_run": False, "hydrate_on_import": False, "source": "manual"},
        )
        assert import_resp.status_code == 200
        import_body = import_resp.json()
        assert import_body["ok"] is True
        assert import_body["accepted"] == 10
        assert import_body["deduped"] == 0
        assert import_body["request_id"] == "req_test"

        if not bool(import_body.get("executed_inline")):
            async def _run_worker() -> None:
                dispatcher = build_default_dispatcher(app.state.engine)
                ran = await poll_and_execute_jobs(app.state.engine, dispatcher, worker_id="test-worker", max_jobs=10)
                assert ran >= 1

            asyncio.run(_run_worker())

        ok_resp = client.get("/random", params={"format": "json", "attempts": 1, "r18_strict": 0})
        assert ok_resp.status_code == 200
        ok_body = ok_resp.json()
        assert ok_body["ok"] is True
        assert ok_body["code"] == "OK"
        assert ok_body["data"]["image"]["id"].isdigit()

        nomatch_resp = client.get(
            "/random",
            params={"format": "json", "attempts": 1, "r18_strict": 0, "min_width": 999999},
        )
        assert nomatch_resp.status_code == 404
        nomatch_body = nomatch_resp.json()
        assert nomatch_body["ok"] is False
        assert nomatch_body["code"] == "NO_MATCH"
