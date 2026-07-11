from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.engine.url import make_url


def ensure_sqlite_parent_dir(database_url: str | Any) -> None:
    """
    Ensure the parent directory of a SQLite file DB exists.

    Accepts a URL string or a SQLAlchemy URL-like object with get_backend_name/database.
    No-op for non-SQLite and in-memory databases.
    """
    try:
        if hasattr(database_url, "get_backend_name") and hasattr(database_url, "database"):
            url = database_url
        else:
            url = make_url(str(database_url or ""))
    except Exception:
        return

    if url.get_backend_name() != "sqlite":
        return
    db_path = url.database
    if not db_path or db_path == ":memory:":
        return
    Path(db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def get_sqlite_db_dir(database_url: str) -> Path:
    """
    Returns the directory containing the SQLite database file.

    For non-SQLite or in-memory DBs, falls back to ./data relative to CWD.
    """

    fallback = Path("./data").resolve()

    try:
        url = make_url(database_url)
    except Exception:
        return fallback

    backend = (url.get_backend_name() or "").lower()
    if backend != "sqlite":
        return fallback

    db = str(url.database or "").strip()
    if not db or db == ":memory:":
        return fallback

    p = Path(db)
    if not p.is_absolute():
        p = Path.cwd() / p
    return p.resolve().parent


def make_file_ref(path: Path, *, base_dir: Path) -> str:
    base = base_dir.resolve()
    target = path.resolve()
    return target.relative_to(base).as_posix()


def resolve_file_ref(file_ref: str, *, base_dir: Path) -> Path:
    raw = (file_ref or "").strip()
    if not raw:
        raise ValueError("file_ref is required")

    rel = Path(raw)
    if rel.is_absolute():
        raise ValueError("file_ref must be relative")
    if any(part == ".." for part in rel.parts):
        raise ValueError("file_ref must not contain '..'")

    base = base_dir.resolve()
    target = (base / rel).resolve()
    target.relative_to(base)  # raises if escaped
    return target

