from __future__ import annotations

import json
from typing import Any


def soft_json_object(value: str | None) -> dict[str, Any]:
    """Best-effort JSON object for stored admin/detail fields; invalid/empty → {}."""
    raw = str(value or "").strip()
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def soft_json_value(value: str | None) -> Any | None:
    """Best-effort JSON value for display; empty/invalid → None (any JSON type kept)."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None
