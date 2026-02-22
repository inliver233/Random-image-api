from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine.url import make_url


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

