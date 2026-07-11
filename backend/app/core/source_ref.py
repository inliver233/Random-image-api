from __future__ import annotations

from urllib.parse import urlparse, urlunparse


def sanitize_source_ref(value: str | None, *, max_len: int = 200) -> str | None:
    """
    Normalize a free-form source reference for storage/display.

    - empty → None
    - invalid/non-URL text → truncated raw
    - URL → scheme + host[:port] + path only (drop userinfo/query/fragment)
    """
    raw = str(value or "").strip()
    if not raw:
        return None

    try:
        parsed = urlparse(raw)
    except Exception:
        return raw[:max_len]

    if not parsed.scheme or not parsed.netloc:
        return raw[:max_len]

    host = parsed.hostname
    if not host:
        return raw[:max_len]

    port = parsed.port
    netloc = f"{host}:{int(port)}" if port else host
    path = parsed.path or ""
    return urlunparse((parsed.scheme, netloc, path, "", "", ""))
