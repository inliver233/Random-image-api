"""Soft SQLite write budget defaults (M9)."""

from __future__ import annotations

from app.worker import apply_write_budget

SQLITE_URL = "sqlite+aiosqlite:///./data/app.db"
PG_URL = "postgresql+asyncpg://ria:ria@db:5432/random_image"


def test_sqlite_defaults_to_conservative_cap(monkeypatch) -> None:
    monkeypatch.delenv("WORKER_SQLITE_WRITE_BUDGET", raising=False)
    assert apply_write_budget(50, database_url=SQLITE_URL) == 8


def test_sqlite_explicit_zero_disables_cap(monkeypatch) -> None:
    monkeypatch.setenv("WORKER_SQLITE_WRITE_BUDGET", "0")
    assert apply_write_budget(50, database_url=SQLITE_URL) == 50


def test_sqlite_explicit_budget_wins(monkeypatch) -> None:
    monkeypatch.setenv("WORKER_SQLITE_WRITE_BUDGET", "4")
    assert apply_write_budget(50, database_url=SQLITE_URL) == 4


def test_budget_never_raises_concurrency(monkeypatch) -> None:
    monkeypatch.setenv("WORKER_SQLITE_WRITE_BUDGET", "100")
    assert apply_write_budget(10, database_url=SQLITE_URL) == 10


def test_postgres_defaults_to_no_cap(monkeypatch) -> None:
    monkeypatch.delenv("WORKER_SQLITE_WRITE_BUDGET", raising=False)
    assert apply_write_budget(50, database_url=PG_URL) == 50


def test_postgres_explicit_budget_still_applies(monkeypatch) -> None:
    monkeypatch.setenv("WORKER_SQLITE_WRITE_BUDGET", "12")
    assert apply_write_budget(50, database_url=PG_URL) == 12
