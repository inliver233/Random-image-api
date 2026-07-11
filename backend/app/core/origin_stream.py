from __future__ import annotations

from typing import Any

from app.core.proxy_routing import select_proxy_uri_for_url
from app.core.pximg_reverse_proxy import rewrite_pximg_to_mirror


async def prepare_origin_stream(
    *,
    engine: Any,
    settings: Any,
    runtime: Any,
    origin_url: str,
    use_mirror: bool,
    mirror_host: str | None,
) -> tuple[str, str | None]:
    """
    Resolve upstream source URL and optional residential proxy URI.

    - use_mirror: rewrite pximg host to mirror_host and skip local proxy pool
    - otherwise: keep origin URL and optionally select a pool proxy for fetch
    """
    origin = str(origin_url)
    if use_mirror:
        host = str(mirror_host or "").strip()
        if host:
            return rewrite_pximg_to_mirror(origin, mirror_host=host), None
        return origin, None

    picked = await select_proxy_uri_for_url(engine, settings, runtime, url=origin)
    proxy_uri = picked.uri if picked is not None else None
    return origin, proxy_uri
