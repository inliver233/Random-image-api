from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class PixivOriginalUrl:
    illust_id: int
    page_index: int
    ext: str


_PIXIV_P_RE = re.compile(r"(?P<illust_id>\d+)_p(?P<page_index>\d+)(?:_master1200)?\.(?P<ext>[A-Za-z0-9]+)$")
_PIXIV_UGOIRA_RE = re.compile(r"(?P<illust_id>\d+)_ugoira(?P<page_index>\d+)\.(?P<ext>[A-Za-z0-9]+)$")

ALLOWED_IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp"}


def parse_pixiv_original_url(url: str) -> PixivOriginalUrl:
    url = url.strip()
    if not url:
        raise ValueError("url is required")

    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("unsupported scheme")

    host = (parsed.hostname or "").lower()
    if not host.endswith("pximg.net"):
        raise ValueError("unsupported host")

    m = _PIXIV_P_RE.search(parsed.path) or _PIXIV_UGOIRA_RE.search(parsed.path)
    if not m:
        raise ValueError("unsupported pixiv original url")

    illust_id = int(m.group("illust_id"))
    page_index = int(m.group("page_index"))
    ext = m.group("ext").lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        raise ValueError("unsupported ext")

    return PixivOriginalUrl(illust_id=illust_id, page_index=page_index, ext=ext)
