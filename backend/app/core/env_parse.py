from __future__ import annotations

import os


def parse_int_env(name: str, *, default: int, min_v: int, max_v: int) -> int:
    """Soft env int with inclusive clamp; missing/invalid → default then clamp."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except Exception:
        return int(default)
    return max(int(min_v), min(int(value), int(max_v)))


def parse_bool_env(name: str, *, default: bool) -> bool:
    """Soft env bool (1/true/yes/y/on and 0/false/no/n/off); empty/unknown → default."""
    raw = (os.environ.get(name) or "").strip()
    if raw == "":
        return bool(default)
    v = raw.lower()
    if v in {"1", "true", "yes", "y", "on"}:
        return True
    if v in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)
