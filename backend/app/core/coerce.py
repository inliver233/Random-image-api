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


def clamp_int(value: int, *, min_v: int, max_v: int) -> int:
    """Inclusive clamp for already-parsed ints (not env/request parsers)."""
    return max(int(min_v), min(int(value), int(max_v)))


def clamp_float(value: float, *, min_v: float, max_v: float) -> float:
    """Inclusive clamp for already-parsed floats (not env/request parsers)."""
    return max(float(min_v), min(float(value), float(max_v)))


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


def truncate_text(text: str, *, max_len: int = 500) -> str:
    """Truncate with trailing ellipsis when longer than max_len (min useful max_len ≈ 4)."""
    if len(text) <= max_len:
        return text
    if max_len <= 3:
        return text[:max_len]
    return text[: max_len - 3] + "..."
