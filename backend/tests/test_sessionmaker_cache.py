from __future__ import annotations

from pathlib import Path

from types import SimpleNamespace

from app.db.engine import create_engine
from app.db.session import create_sessionmaker, resolve_sessionmaker
from app.main import create_app


def test_create_sessionmaker_reuses_instance_per_engine(tmp_path: Path) -> None:
    db_url = "sqlite+aiosqlite:///" + (tmp_path / "sm.db").as_posix()
    engine = create_engine(db_url)
    a = create_sessionmaker(engine)
    b = create_sessionmaker(engine)
    assert a is b


def test_create_app_wires_sessionmaker(tmp_path: Path, monkeypatch) -> None:
    db_url = "sqlite+aiosqlite:///" + (tmp_path / "app_sm.db").as_posix()
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    app = create_app()
    assert getattr(app.state, "sessionmaker", None) is not None
    # Same object as process cache for the app engine.
    assert app.state.sessionmaker is create_sessionmaker(app.state.engine)


def test_resolve_sessionmaker_prefers_app_state(tmp_path: Path) -> None:
    db_url = "sqlite+aiosqlite:///" + (tmp_path / "resolve_sm.db").as_posix()
    engine = create_engine(db_url)
    sm = create_sessionmaker(engine)
    sentinel = object()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(sessionmaker=sentinel, engine=engine)))
    assert resolve_sessionmaker(request) is sentinel
    # Without state sessionmaker, fall back to process cache for engine.
    request2 = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(engine=engine)))
    assert resolve_sessionmaker(request2, engine) is sm
