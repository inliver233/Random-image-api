from __future__ import annotations

import logging
from typing import Any, Mapping

import httpx

from app.core.config import Settings, load_settings
from app.core.env_parse import parse_bool_env

logger = logging.getLogger(__name__)

# Optional Phase-2 hook: after catalog upsert, notify an external prewarm worker
# (e.g. CF Worker/Queue that fills R2). Default off — never affects public pick path.
_R2_PREWARM_CHUNK = 100
_R2_PREWARM_TIMEOUT_S = 5.0


def r2_prewarm_enabled(settings: Settings | Any | None = None, *, env: Mapping[str, str] | None = None) -> bool:
    if settings is not None:
        return bool(getattr(settings, "r2_prewarm_enabled", False)) and bool(
            str(getattr(settings, "r2_prewarm_url", "") or "").strip()
        )
    # Fallback for callers without Settings (tests / scripts).
    e = env
    if e is None:
        import os

        e = os.environ
    url = str(e.get("R2_PREWARM_URL") or "").strip()
    return parse_bool_env("R2_PREWARM_ENABLED", default=False, env=e) and bool(url)


def r2_prewarm_url(settings: Settings | Any | None = None) -> str | None:
    if settings is None:
        settings = load_settings()
    url = str(getattr(settings, "r2_prewarm_url", "") or "").strip().rstrip("/")
    return url or None


async def maybe_enqueue_r2_prewarm(
    *,
    image_ids: list[int] | None = None,
    settings: Settings | None = None,
    client: Any | None = None,
) -> dict[str, Any] | None:
    """Best-effort POST of image ids to R2_PREWARM_URL. No-op when disabled. Never raises."""
    owned: httpx.AsyncClient | None = None
    try:
        s = settings if settings is not None else load_settings()
        if not r2_prewarm_enabled(s):
            return None
        base = r2_prewarm_url(s)
        if not base:
            return None

        ids: list[int] = []
        seen: set[int] = set()
        for raw in image_ids or []:
            try:
                i = int(raw)
            except Exception:
                continue
            if i <= 0 or i in seen:
                continue
            seen.add(i)
            ids.append(i)
        if not ids:
            return None

        use_client = client
        if use_client is None:
            owned = httpx.AsyncClient()
            use_client = owned

        applied = 0
        last: dict[str, Any] | None = None
        for offset in range(0, len(ids), _R2_PREWARM_CHUNK):
            chunk = ids[offset : offset + _R2_PREWARM_CHUNK]
            try:
                resp = await use_client.post(
                    f"{base}/v1/prewarm",
                    json={"image_ids": chunk},
                    timeout=_R2_PREWARM_TIMEOUT_S,
                )
                if resp.status_code >= 300:
                    logger.debug("r2-prewarm status=%s body=%s", resp.status_code, (resp.text or "")[:200])
                    return None
                data = resp.json() if resp.content else {"ok": True}
                last = data if isinstance(data, dict) else {"ok": True}
                applied += len(chunk)
            except Exception as exc:
                logger.warning("r2-prewarm enqueue failed: %s", exc)
                return None
        return last if last is not None else {"ok": True, "enqueued": applied}
    except Exception as exc:
        logger.warning("r2-prewarm error: %s", exc)
        return None
    finally:
        if owned is not None:
            try:
                await owned.aclose()
            except Exception:
                pass
