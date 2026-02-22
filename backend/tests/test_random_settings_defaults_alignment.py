from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.db.models.base import Base
from app.db.models.images import Image
from app.db.models.runtime_settings import RuntimeSetting
from app.db.session import create_sessionmaker
from app.main import create_app


def test_random_uses_runtime_defaults_for_attempts_r18_strict_and_fail_cooldown(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "random_runtime_defaults_alignment.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            session.add(
                RuntimeSetting(
                    key="random.defaults",
                    value_json=json.dumps(
                        {
                            "default_attempts": 7,
                            "default_r18_strict": False,
                            "fail_cooldown_ms": 12345,
                            "strategy": "random",
                        },
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ),
                    description=None,
                    updated_by=None,
                )
            )
            session.add(
                Image(
                    illust_id=123,
                    page_index=0,
                    ext="jpg",
                    original_url="https://example.test/1.jpg",
                    proxy_path="/i/1.jpg",
                    random_key=0.10,
                    x_restrict=None,
                )
            )
            await session.commit()

        await app.state.engine.dispose()

    asyncio.run(_seed())

    with TestClient(app) as client:
        resp = client.get("/random", params={"format": "json", "seed": "seed_alignment_001"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        debug = body["data"]["debug"]
        assert debug["attempts"] == 7
        assert debug["attempts_source"] == "runtime"
        assert debug["r18_strict"] == 0
        assert debug["r18_strict_source"] == "runtime"
        assert debug["fail_cooldown_ms"] == 12345
        assert debug["fail_cooldown_source"] == "runtime"
        assert debug["strategy"] == "random"
        assert debug["strategy_source"] == "runtime"

