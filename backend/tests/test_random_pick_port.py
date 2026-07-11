from __future__ import annotations

from typing import Any

from app.db.random_pick_port import (
    RandomPickPort,
    SqliteRandomPick,
    build_random_pick,
    resolve_random_pick,
)


def test_build_random_pick_defaults_to_sqlite() -> None:
    pick = build_random_pick()
    assert isinstance(pick, SqliteRandomPick)
    assert pick.backend == "sqlite"
    assert isinstance(pick, RandomPickPort)


def test_resolve_random_pick_prefers_injected() -> None:
    custom = SqliteRandomPick()
    assert resolve_random_pick(custom) is custom
    assert isinstance(resolve_random_pick(None), SqliteRandomPick)


def test_sqlite_random_pick_count_candidates_delegates(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    async def _fake_count(session: Any, **kwargs: Any) -> int:
        calls.append(dict(kwargs))
        return 7

    monkeypatch.setattr("app.db.random_pick_port.count_pick_candidates", _fake_count)

    import asyncio

    pick = SqliteRandomPick()

    async def _run() -> None:
        n = await pick.count_candidates(object(), r18=0, r18_strict=True)  # type: ignore[arg-type]
        assert n == 7
        assert calls and calls[0]["r18"] == 0

    asyncio.run(_run())
