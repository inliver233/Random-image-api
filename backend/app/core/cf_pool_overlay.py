from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from app.core.cf_pool_registry import (
    RUNTIME_KEY_API_BASES,
    RUNTIME_KEY_IMAGE_BASES,
    merge_base_url_lists,
    parse_base_urls_payload,
)
from app.core.logging import get_logger

log = get_logger(__name__)

# Multi-process freshness: peers only load overlay at boot unless refreshed.
# Short TTL keeps register/deploy on one BFF visible on others without restart.
_DEFAULT_OVERLAY_TTL_S = 5.0

_lock = threading.RLock()
_api_bases: list[str] = []
_image_bases: list[str] = []
_loaded_at_mono: float = 0.0
_reload_lock: asyncio.Lock | None = None


def _get_reload_lock() -> asyncio.Lock:
    global _reload_lock
    if _reload_lock is None:
        _reload_lock = asyncio.Lock()
    return _reload_lock


def get_api_overlay_bases() -> list[str]:
    with _lock:
        return list(_api_bases)


def get_image_overlay_bases() -> list[str]:
    with _lock:
        return list(_image_bases)


def set_api_overlay_bases(bases: list[str] | None) -> list[str]:
    merged = merge_base_url_lists(bases or [])
    with _lock:
        global _api_bases, _loaded_at_mono
        _api_bases = list(merged)
        _loaded_at_mono = time.monotonic()
        return list(_api_bases)


def set_image_overlay_bases(bases: list[str] | None) -> list[str]:
    merged = merge_base_url_lists(bases or [])
    with _lock:
        global _image_bases, _loaded_at_mono
        _image_bases = list(merged)
        _loaded_at_mono = time.monotonic()
        return list(_image_bases)


def merge_api_bases_with_overlay(env_bases: list[str] | None) -> list[str]:
    return merge_base_url_lists(env_bases or [], get_api_overlay_bases())


def merge_image_bases_with_overlay(env_bases: list[str] | None) -> list[str]:
    return merge_base_url_lists(env_bases or [], get_image_overlay_bases())


def apply_runtime_values_to_overlay(values: dict[str, Any] | None) -> None:
    """Load overlay from runtime_settings values dict (startup / after write / TTL)."""
    values = values or {}
    api = parse_base_urls_payload(values.get(RUNTIME_KEY_API_BASES))
    image = parse_base_urls_payload(values.get(RUNTIME_KEY_IMAGE_BASES))
    set_api_overlay_bases(api)
    set_image_overlay_bases(image)
    # Invalidate image-edge settings cache so merged bases apply immediately.
    try:
        from app.core.image_edge import _EDGE_CFG_FROM_SETTINGS

        _EDGE_CFG_FROM_SETTINGS.clear()
    except Exception:
        pass
    log.info(
        "cf_pool_overlay_loaded api_bases=%s image_bases=%s",
        len(api),
        len(image),
    )


async def reload_overlay_from_engine(engine: Any) -> None:
    """Best-effort load from DB runtime_settings."""
    if engine is None:
        return
    try:
        from app.core.runtime_settings import fetch_runtime_settings

        values = await fetch_runtime_settings(engine)
        apply_runtime_values_to_overlay(values)
    except Exception:
        log.warning("cf_pool_overlay_reload_failed", exc_info=True)


def overlay_age_s(*, now: float | None = None) -> float | None:
    """Seconds since last successful overlay apply; None if never loaded."""
    with _lock:
        loaded = float(_loaded_at_mono or 0.0)
    if loaded <= 0.0:
        return None
    t = time.monotonic() if now is None else float(now)
    return max(0.0, t - loaded)


def should_refresh_overlay(*, ttl_s: float = _DEFAULT_OVERLAY_TTL_S, now: float | None = None) -> bool:
    age = overlay_age_s(now=now)
    if age is None:
        return True
    return age >= max(0.2, float(ttl_s))


async def ensure_overlay_fresh(
    engine: Any,
    *,
    ttl_s: float = _DEFAULT_OVERLAY_TTL_S,
    force: bool = False,
) -> bool:
    """Reload overlay from DB when stale (multi-process membership freshness).

    Returns True if a reload ran. Never raises.
    """
    if engine is None:
        return False
    try:
        if not force and not should_refresh_overlay(ttl_s=ttl_s):
            return False
        async with _get_reload_lock():
            if not force and not should_refresh_overlay(ttl_s=ttl_s):
                return False
            await reload_overlay_from_engine(engine)
            return True
    except Exception:
        log.warning("cf_pool_overlay_ensure_fresh_failed", exc_info=True)
        return False


def reset_overlay_for_tests() -> None:
    """Test helper: clear process overlay."""
    global _loaded_at_mono, _reload_lock
    set_api_overlay_bases([])
    set_image_overlay_bases([])
    with _lock:
        _loaded_at_mono = 0.0
    _reload_lock = None
