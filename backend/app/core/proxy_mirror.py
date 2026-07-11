from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.errors import ApiError, ErrorCode
from app.core.pximg_reverse_proxy import (
    normalize_pximg_mirror_host,
    normalize_pximg_proxy,
    pick_pximg_mirror_host_for_request,
)


@dataclass(frozen=True, slots=True)
class ProxyMirrorResolution:
    """Shared proxy / mirror resolution for /random, /i, and legacy routes."""

    proxy_override: str | None
    pximg_mirror_host_override: str | None
    use_pixiv_cat: bool
    mirror_host: str


def resolve_proxy_mirror(
    *,
    runtime: Any,
    headers: Any | None = None,
    pixiv_cat: int = 0,
    pximg_mirror_host: str | None = None,
    proxy: str | None = None,
    raise_on_invalid: bool = True,
) -> ProxyMirrorResolution:
    """Normalize query overrides + runtime defaults into one delivery tuple.

    - proxy= / pximg_mirror_host= force mirror host path (use_pixiv_cat=True)
    - runtime.image_proxy_use_pixiv_cat enables mirrors by default
    - when mirrors are on without override, sticky host is picked from request headers
    """
    if int(pixiv_cat) not in {0, 1}:
        if raise_on_invalid:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported pixiv_cat", status_code=400)
        pixiv_cat = 0

    pximg_mirror_host_override: str | None = None
    if pximg_mirror_host is not None:
        raw = str(pximg_mirror_host or "").strip()
        if raw:
            pximg_mirror_host_override = normalize_pximg_mirror_host(raw)
            if pximg_mirror_host_override is None and raise_on_invalid:
                raise ApiError(
                    code=ErrorCode.BAD_REQUEST,
                    message="Unsupported pximg_mirror_host",
                    status_code=400,
                )

    proxy_override: str | None = None
    if proxy is not None:
        raw = str(proxy or "").strip()
        if raw:
            extra = list(getattr(runtime, "image_proxy_extra_pximg_mirror_hosts", None) or [])
            proxy_override = normalize_pximg_proxy(raw, extra_hosts=extra)
            if proxy_override is None and raise_on_invalid:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported proxy", status_code=400)

    use_pixiv_cat = (
        bool(getattr(runtime, "image_proxy_use_pixiv_cat", False))
        or int(pixiv_cat) == 1
        or proxy_override is not None
    )
    mirror_host_override = proxy_override or pximg_mirror_host_override
    runtime_mirror_host = (
        str(getattr(runtime, "image_proxy_pximg_mirror_host", "") or "").strip() or "i.pixiv.cat"
    )
    if mirror_host_override:
        mirror_host = str(mirror_host_override)
    elif use_pixiv_cat and headers is not None:
        mirror_host = pick_pximg_mirror_host_for_request(
            headers=headers,
            fallback_host=runtime_mirror_host,
        )
    else:
        mirror_host = runtime_mirror_host

    return ProxyMirrorResolution(
        proxy_override=proxy_override,
        pximg_mirror_host_override=pximg_mirror_host_override,
        use_pixiv_cat=bool(use_pixiv_cat),
        mirror_host=str(mirror_host),
    )
