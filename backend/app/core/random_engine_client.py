from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)

# Process-local dual-run circuit: stop paying engine RTT after consecutive hard failures.
# Hard failures = transport/5xx (unavailable) or empty/not-ready index. Filter no_match does not trip.
_CIRCUIT_FAILURE_THRESHOLD = 5
_CIRCUIT_OPEN_S = 30.0
_CIRCUIT_HARD_STATUSES = frozenset({"unavailable", "empty_index"})

_circuit_lock = threading.Lock()
_circuit_consecutive_failures = 0
_circuit_open_until = 0.0
_circuit_half_open = False


def reset_engine_circuit_for_tests() -> None:
    """Reset process circuit state (unit tests only)."""
    global _circuit_consecutive_failures, _circuit_open_until, _circuit_half_open
    with _circuit_lock:
        _circuit_consecutive_failures = 0
        _circuit_open_until = 0.0
        _circuit_half_open = False


def engine_circuit_snapshot(*, now: float | None = None) -> dict[str, Any]:
    """Read-only circuit state for tests/ops (no side effects; does not arm half-open)."""
    t = time.monotonic() if now is None else float(now)
    with _circuit_lock:
        open_until = float(_circuit_open_until)
        failures = int(_circuit_consecutive_failures)
        half = bool(_circuit_half_open)
    if open_until > t:
        state = "open"
    elif half:
        state = "half_open"
    else:
        state = "closed"
    return {
        "state": state,
        "consecutive_failures": failures,
        "open_remaining_s": max(0.0, open_until - t),
        "failure_threshold": _CIRCUIT_FAILURE_THRESHOLD,
        "open_s": _CIRCUIT_OPEN_S,
    }


def engine_circuit_allow(*, now: float | None = None) -> bool:
    """True if dual-run may attempt the engine (closed, or one half-open probe)."""
    global _circuit_half_open, _circuit_open_until
    t = time.monotonic() if now is None else float(now)
    with _circuit_lock:
        if _circuit_open_until > t:
            return False
        if _circuit_open_until > 0.0 and _circuit_open_until <= t:
            # Cool-down expired → allow a single half-open probe.
            _circuit_half_open = True
            _circuit_open_until = 0.0
            return True
        if _circuit_half_open:
            # Another concurrent request while probe in flight — stay on Python.
            return False
        return True


def engine_circuit_record(status: str, *, now: float | None = None) -> None:
    """Record a dual-run engine outcome. Hard failures open the circuit after threshold."""
    global _circuit_consecutive_failures, _circuit_open_until, _circuit_half_open
    label = (status or "").strip().lower() or "fallback"
    t = time.monotonic() if now is None else float(now)
    hard = label in _CIRCUIT_HARD_STATUSES
    success = label == "ok"
    with _circuit_lock:
        if success:
            _circuit_consecutive_failures = 0
            _circuit_open_until = 0.0
            _circuit_half_open = False
            return
        if not hard:
            # Soft miss (no_match / not_ok / db_miss / …): clear half-open without tripping.
            if _circuit_half_open:
                _circuit_half_open = False
            return
        _circuit_consecutive_failures = int(_circuit_consecutive_failures) + 1
        _circuit_half_open = False
        if _circuit_consecutive_failures >= _CIRCUIT_FAILURE_THRESHOLD:
            _circuit_open_until = t + float(_CIRCUIT_OPEN_S)
            logger.warning(
                "random-engine circuit open for %.0fs after %s consecutive %s",
                _CIRCUIT_OPEN_S,
                _circuit_consecutive_failures,
                label,
            )
            _circuit_consecutive_failures = 0


def random_engine_base_url(settings: Settings | Any) -> str | None:
    url = str(getattr(settings, "random_engine_url", "") or "").strip().rstrip("/")
    return url or None


def random_engine_traffic_percent(settings: Settings | Any) -> int:
    """0–100 progressive cutover share when dual-run is enabled (default 100)."""
    try:
        pct = int(getattr(settings, "random_engine_traffic_percent", 100) or 0)
    except Exception:
        pct = 100
    if pct < 0:
        return 0
    if pct > 100:
        return 100
    return pct


def should_route_pick_to_engine(settings: Settings | Any, *, rng: Any | None = None) -> bool:
    """Whether this pick should attempt the Go engine (flag + URL + traffic %).

    Catalog event publish is independent (URL-only) so the index can warm first.
    Process circuit is checked separately via ``engine_circuit_allow`` so open
    circuits are not mis-labeled as traffic skips.
    """
    if not bool(getattr(settings, "random_engine_enabled", False)):
        return False
    if not random_engine_base_url(settings):
        return False
    pct = random_engine_traffic_percent(settings)
    if pct <= 0:
        return False
    if pct >= 100:
        return True
    roller = rng if rng is not None else random
    try:
        roll = float(roller.random())  # type: ignore[attr-defined]
    except Exception:
        roll = float(random.random())
    return (roll * 100.0) < float(pct)


async def engine_health(client: httpx.AsyncClient, base_url: str, *, timeout_s: float = 0.5) -> dict[str, Any] | None:
    try:
        resp = await client.get(f"{base_url}/healthz", timeout=timeout_s)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not isinstance(data, dict) or not data.get("ok"):
            return None
        return data
    except Exception as exc:
        logger.debug("random-engine health failed: %s", exc)
        return None


async def engine_pick(
    client: httpx.AsyncClient,
    base_url: str,
    *,
    payload: dict[str, Any],
    timeout_s: float = 0.8,
) -> dict[str, Any] | None:
    """POST /v1/pick. Returns parsed JSON or None on transport/5xx (BFF should fall back)."""
    try:
        resp = await client.post(f"{base_url}/v1/pick", json=payload, timeout=timeout_s)
        if resp.status_code >= 500:
            return None
        if resp.status_code != 200:
            logger.debug("random-engine pick status=%s body=%s", resp.status_code, resp.text[:200])
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.debug("random-engine pick failed: %s", exc)
        return None


async def engine_filter_count(
    client: httpx.AsyncClient,
    base_url: str,
    *,
    filters: dict[str, Any],
    timeout_s: float = 2.0,
) -> dict[str, Any] | None:
    """POST /v1/admin/filter-count — dual-run cardinality check (no sampling)."""
    try:
        resp = await client.post(
            f"{base_url}/v1/admin/filter-count",
            json={"filters": filters or {}},
            timeout=timeout_s,
        )
        if resp.status_code != 200:
            logger.debug(
                "random-engine filter-count status=%s body=%s",
                resp.status_code,
                (resp.text or "")[:200],
            )
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.debug("random-engine filter-count failed: %s", exc)
        return None


async def engine_apply_snapshot(
    client: httpx.AsyncClient,
    base_url: str,
    *,
    revision: str,
    images: list[dict[str, Any]],
    tag_postings: dict[str, list[int]] | None = None,
    timeout_s: float = 30.0,
) -> dict[str, Any] | None:
    body: dict[str, Any] = {"revision": revision, "images": images}
    if tag_postings:
        body["tag_postings"] = tag_postings
    try:
        resp = await client.post(f"{base_url}/v1/admin/snapshot", json=body, timeout=timeout_s)
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("random-engine snapshot failed: %s", exc)
        return None


async def engine_apply_events(
    client: httpx.AsyncClient,
    base_url: str,
    *,
    events: list[dict[str, Any]],
    timeout_s: float = 5.0,
) -> dict[str, Any] | None:
    """POST /v1/admin/events. Best-effort catalog delta; None on transport/non-200."""
    if not events:
        return {"ok": True, "applied": 0}
    body: dict[str, Any] = {"events": list(events)}
    try:
        resp = await client.post(f"{base_url}/v1/admin/events", json=body, timeout=timeout_s)
        if resp.status_code != 200:
            logger.debug(
                "random-engine events status=%s body=%s",
                resp.status_code,
                (resp.text or "")[:200],
            )
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("random-engine events failed: %s", exc)
        return None
