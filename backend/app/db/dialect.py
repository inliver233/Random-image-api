from __future__ import annotations

from sqlalchemy.engine.url import make_url


def backend_from_database_url(database_url: str) -> str:
    """Map DATABASE_URL dialect → ops backend label (sqlite | postgres | other).

    Shared by CatalogStore / TagStore / RandomPickPort factories. Label only —
    no automatic schema migration or dual-write.
    """
    raw = (database_url or "").strip()
    if not raw:
        return "sqlite"
    try:
        url = make_url(raw)
        name = (url.get_backend_name() or "").lower()
    except Exception:
        low = raw.lower()
        if low.startswith("postgres") or "postgresql" in low:
            return "postgres"
        if low.startswith("sqlite"):
            return "sqlite"
        return "other"
    if name.startswith("sqlite"):
        return "sqlite"
    if name.startswith("postgres"):
        return "postgres"
    return name or "other"
