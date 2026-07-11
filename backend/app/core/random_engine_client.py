from __future__ import annotations

import logging
import random
from typing import Any

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


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
