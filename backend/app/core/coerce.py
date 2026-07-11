from __future__ import annotations

from typing import Any


def as_str(value: Any) -> str | None:
    """Strip optional string; empty/None → None."""
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def as_optional_int(value: Any) -> int | None:
    """
    Lenient optional int for job/import payloads.

    None / bool / unparseable → None (bool rejected so True never becomes 1).
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except Exception:
        return None


def as_int(value: Any, *, default: int = 0) -> int:
    """Lenient int with default; used by job row mapping and hydration run loops."""
    try:
        return int(value)
    except Exception:
        return default


def derive_orientation(width: int | None, height: int | None) -> tuple[float | None, int | None]:
    """Return (aspect_ratio, orientation) where orientation is 1=portrait, 2=landscape, 3=square."""
    if width is None or height is None or width <= 0 or height <= 0:
        return None, None
    if width > height:
        orientation = 2
    elif height > width:
        orientation = 1
    else:
        orientation = 3
    return float(width) / float(height), orientation
