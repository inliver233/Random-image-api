from __future__ import annotations

import asyncio
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.db.models.base import Base
from app.main import create_app


_HAS_CHINESE = re.compile(r"[\u4e00-\u9fff]")


def test_random_no_match_hints_are_chinese(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "random_no_match_hints.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await app.state.engine.dispose()

    asyncio.run(_migrate())

    with TestClient(app) as client:
        resp = client.get("/random?format=json", headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 404
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "NO_MATCH"
        assert body["request_id"] == "req_test"

        msg = str(body.get("message") or "")
        assert _HAS_CHINESE.search(msg), f"expected Chinese message, got: {msg!r}"

        hints = body.get("details", {}).get("hints", {})
        suggestions = hints.get("suggestions")
        assert isinstance(suggestions, list)
        assert suggestions
        for s in suggestions:
            assert isinstance(s, str)
            assert _HAS_CHINESE.search(s), f"expected Chinese suggestion, got: {s!r}"

