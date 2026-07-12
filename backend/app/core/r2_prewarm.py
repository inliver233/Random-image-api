from __future__ import annotations

import logging
from typing import Any, Mapping

from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings, load_settings
from app.core.env_parse import parse_bool_env
from app.core.image_edge import is_edge_allowed_path, pximg_path_from_original_url
from app.db.catalog import CatalogStore, SqliteCatalogStore
from app.db.session import create_sessionmaker

logger = logging.getLogger(__name__)

# Worker /v1/prewarm accepts max 50 allowlisted paths per request.
_R2_PREWARM_CHUNK = 50
_R2_PREWARM_TIMEOUT_S = 15.0


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


def r2_prewarm_secret(settings: Settings | Any | None = None) -> str:
    """Secret for Worker X-Prewarm-Secret (PREWARM_SECRET | R2_PREWARM_SECRET | IMAGE_EDGE_SECRET)."""
    if settings is None:
        settings = load_settings()
    for key in ("r2_prewarm_secret", "image_edge_secret"):
        val = str(getattr(settings, key, "") or "").strip()
        if val:
            return val
    return ""


def normalize_prewarm_paths(paths: list[str] | None) -> list[str]:
    """Dedupe + allowlist pximg paths for Worker /v1/prewarm."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in paths or []:
        path = str(raw or "").strip()
        if not path or path in seen:
            continue
        if not is_edge_allowed_path(path):
            continue
        seen.add(path)
        out.append(path)
    return out


def paths_from_original_urls(urls: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in urls or []:
        path = pximg_path_from_original_url(str(raw or ""))
        if not path or path in seen:
            continue
        if not is_edge_allowed_path(path):
            continue
        seen.add(path)
        out.append(path)
    return out


async def resolve_prewarm_paths_from_image_ids(
    *,
    image_ids: list[int],
    engine: AsyncEngine,
    catalog: CatalogStore | None = None,
) -> list[str]:
    """Map catalog image ids → allowlisted pximg paths (any status)."""
    ids: list[int] = []
    seen: set[int] = set()
    for raw in image_ids:
        try:
            i = int(raw)
        except Exception:
            continue
        if i <= 0 or i in seen:
            continue
        seen.add(i)
        ids.append(i)
    if not ids:
        return []

    store: CatalogStore = catalog if catalog is not None else SqliteCatalogStore()
    Session = create_sessionmaker(engine)
    urls: list[str] = []
    async with Session() as session:
        # any_status: import/heal may touch disabled rows that still need R2 fill.
        rows = await store.get_images_by_ids_any_status(session, image_ids=ids)
        for im in rows or []:
            url = str(getattr(im, "original_url", "") or "").strip()
            if url:
                urls.append(url)
    return paths_from_original_urls(urls)


async def maybe_enqueue_r2_prewarm(
    *,
    image_ids: list[int] | None = None,
    paths: list[str] | None = None,
    settings: Settings | None = None,
    client: Any | None = None,
    engine: AsyncEngine | None = None,
    catalog: CatalogStore | None = None,
) -> dict[str, Any] | None:
    """Best-effort POST of allowlisted paths to Worker /v1/prewarm.

    Worker contract (edge/img-worker):
      POST {base}/v1/prewarm
      Header: X-Prewarm-Secret
      Body: { "paths": ["/img-original/...", ...] }  // max 50

    When only image_ids are provided, resolve original_url via CatalogStore
    (requires engine). No-op when disabled / no paths / no secret. Never raises.
    """
    try:
        s = settings if settings is not None else load_settings()
        if not r2_prewarm_enabled(s):
            return None
        base = r2_prewarm_url(s)
        if not base:
            return None
        secret = r2_prewarm_secret(s)
        if not secret:
            logger.warning("r2-prewarm ready but no secret (R2_PREWARM_SECRET / IMAGE_EDGE_SECRET)")
            return None

        resolved: list[str] = normalize_prewarm_paths(paths)
        if image_ids and engine is not None:
            from_ids = await resolve_prewarm_paths_from_image_ids(
                image_ids=list(image_ids),
                engine=engine,
                catalog=catalog,
            )
            # Merge, preserve order, dedupe.
            seen = set(resolved)
            for p in from_ids:
                if p not in seen:
                    seen.add(p)
                    resolved.append(p)
        elif image_ids and not resolved:
            # Callers that only pass ids must inject engine for id→path mapping.
            logger.debug("r2-prewarm skipped: image_ids without engine and no paths")
            return None

        if not resolved:
            return None

        use_client = client
        if use_client is None:
            # Worker/job path: reuse process control-plane client (no cold client per call).
            from app.core.http_client import get_control_plane_http_client

            use_client = get_control_plane_http_client()

        applied = 0
        failed_chunks = 0
        last: dict[str, Any] | None = None
        headers = {"X-Prewarm-Secret": secret, "Content-Type": "application/json"}
        for offset in range(0, len(resolved), _R2_PREWARM_CHUNK):
            chunk = resolved[offset : offset + _R2_PREWARM_CHUNK]
            try:
                resp = await use_client.post(
                    f"{base}/v1/prewarm",
                    json={"paths": chunk},
                    headers=headers,
                    timeout=_R2_PREWARM_TIMEOUT_S,
                )
                if resp.status_code >= 300:
                    logger.debug(
                        "r2-prewarm status=%s body=%s",
                        resp.status_code,
                        (resp.text or "")[:200],
                    )
                    failed_chunks += 1
                    continue
                data = resp.json() if resp.content else {"ok": True}
                last = data if isinstance(data, dict) else {"ok": True}
                applied += len(chunk)
            except Exception as exc:
                logger.warning("r2-prewarm enqueue failed: %s", exc)
                failed_chunks += 1
                continue
        if applied <= 0:
            return None
        if last is None:
            last = {"ok": True}
        last = dict(last)
        last.setdefault("enqueued_paths", applied)
        if failed_chunks:
            last["failed_chunks"] = failed_chunks
        return last
    except Exception as exc:
        logger.warning("r2-prewarm error: %s", exc)
        return None
