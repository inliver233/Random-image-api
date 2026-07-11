from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


def random_engine_base_url(settings: Settings) -> str | None:
    url = str(getattr(settings, "random_engine_url", "") or "").strip().rstrip("/")
    return url or None


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
