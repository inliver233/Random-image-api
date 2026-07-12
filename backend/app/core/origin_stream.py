from __future__ import annotations

from typing import Any

from app.core.egress_policy import allow_residential_image_origin
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
    allow_residential_proxy: bool | None = None,
    force_residential_emergency: bool = False,
) -> tuple[str, str | None]:
    """
    Resolve upstream source URL and optional residential proxy URI.

    - use_mirror: rewrite pximg host to mirror_host and skip local proxy pool
    - allow_residential_proxy=False: never select pool proxy (direct origin)
    - allow_residential_proxy=True: always attempt pool select (explicit override)
    - allow_residential_proxy=None (default): use egress_policy —
      emergency-only when CF image edge ready (mandate); residential near-zero
    - force_residential_emergency: admin/ops emergency path even when edge ready
    """
    origin = str(origin_url)
    if use_mirror:
        host = str(mirror_host or "").strip()
        if host:
            return rewrite_pximg_to_mirror(origin, mirror_host=host), None
        return origin, None

    allow = allow_residential_image_origin(
        settings,
        force_emergency=force_residential_emergency,
        allow_override=allow_residential_proxy,
    )
    if not allow:
        return origin, None

    picked = await select_proxy_uri_for_url(engine, settings, runtime, url=origin)
    proxy_uri = picked.uri if picked is not None else None
    return origin, proxy_uri
