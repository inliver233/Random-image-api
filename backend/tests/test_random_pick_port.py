from __future__ import annotations

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
