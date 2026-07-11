from __future__ import annotations

import os
from collections.abc import Mapping


def _env_raw(name: str, env: Mapping[str, str] | None) -> str:
    source: Mapping[str, str] = env if env is not None else os.environ
    return str(source.get(name) or "").strip()


def parse_int_env(
    name: str,
    *,
    default: int,
    min_v: int,
    max_v: int,
    env: Mapping[str, str] | None = None,
) -> int:
    """Soft env int with inclusive clamp; missing/invalid → default then clamp."""
    raw = _env_raw(name, env)
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except Exception:
        return int(default)
    return max(int(min_v), min(int(value), int(max_v)))


def parse_float_env(
    name: str,
    *,
    default: float,
    min_v: float,
    max_v: float,
    env: Mapping[str, str] | None = None,
) -> float:
    """Soft env float with inclusive clamp; missing/invalid → default then clamp."""
    raw = _env_raw(name, env)
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except Exception:
        return float(default)
    return max(float(min_v), min(float(value), float(max_v)))


def parse_bool_env(
    name: str,
    *,
    default: bool,
    env: Mapping[str, str] | None = None,
) -> bool:
    """Soft env bool (1/true/yes/y/on and 0/false/no/n/off); empty/unknown → default."""
    raw = _env_raw(name, env)
    if raw == "":
        return bool(default)
    v = raw.lower()
    if v in {"1", "true", "yes", "y", "on"}:
        return True
    if v in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def parse_str_env(
    name: str,
    *,
    default: str = "",
    env: Mapping[str, str] | None = None,
) -> str:
    """Soft env string (strip); empty/missing → default (also stripped)."""
    raw = _env_raw(name, env)
    if raw == "":
        return str(default or "").strip()
    return raw


def parse_optional_str_env(
    name: str,
    *,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Soft env string; empty/missing → None."""
    raw = _env_raw(name, env)
    return raw or None
