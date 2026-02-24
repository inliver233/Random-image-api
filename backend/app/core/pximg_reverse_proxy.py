from __future__ import annotations

from collections.abc import Mapping
import ipaddress
import re
from urllib.parse import urlparse, urlunparse


DEFAULT_PXIMG_MIRROR_HOST = "i.pixiv.cat"

ALLOWED_PXIMG_MIRROR_HOSTS: tuple[str, ...] = (
    "i.pixiv.cat",
    "i.pixiv.re",
    "i.pixiv.nl",
)

_PXIMG_MIRROR_ALIASES: dict[str, str] = {
    # pixiv.cat
    "cat": "i.pixiv.cat",
    "pixiv-cat": "i.pixiv.cat",
    "pixiv.cat": "i.pixiv.cat",
    "i-pixiv-cat": "i.pixiv.cat",
    "i.pixiv.cat": "i.pixiv.cat",
    # pixiv.re (often recommended for CN mainland)
    "re": "i.pixiv.re",
    "pixiv-re": "i.pixiv.re",
    "pixiv.re": "i.pixiv.re",
    "i-pixiv-re": "i.pixiv.re",
    "i.pixiv.re": "i.pixiv.re",
    # pixiv.nl (often recommended for CN mainland)
    "nl": "i.pixiv.nl",
    "pixiv-nl": "i.pixiv.nl",
    "pixiv.nl": "i.pixiv.nl",
    "i-pixiv-nl": "i.pixiv.nl",
    "i.pixiv.nl": "i.pixiv.nl",
}

_CUSTOM_HOST_RE = re.compile(
    r"^(?=.{1,253}$)"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)"
    r"(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))+$"
)


_REQUEST_COUNTRY_HEADERS: tuple[str, ...] = (
    # Cloudflare
    "cf-ipcountry",
    # Vercel
    "x-vercel-ip-country",
    # AWS CloudFront
    "cloudfront-viewer-country",
    # Google App Engine / GCP
    "x-appengine-country",
    # Common self-managed reverse proxies
    "x-country-code",
    "x-geo-country",
)


def _get_request_country_code(headers: Mapping[str, str] | None) -> str | None:
    if not headers:
        return None
    lowered: dict[str, str] = {}
    try:
        for k, v in headers.items():
            key = str(k or "").strip().lower()
            if not key:
                continue
            val = str(v or "").strip()
            if not val:
                continue
            lowered[key] = val
    except Exception:
        return None

    for key in _REQUEST_COUNTRY_HEADERS:
        val = lowered.get(key)
        if not val:
            continue
        code = val.strip().upper()
        if not code:
            continue
        # Some CDNs may return "CN,..." or other variants; we only need the first token.
        code = code.split(",", 1)[0].strip()
        if len(code) >= 2:
            return code[:2]
    return None


def pick_pximg_mirror_host_for_request(
    *,
    headers: Mapping[str, str] | None,
    fallback_host: str | None,
) -> str:
    """
    Pick the best pximg mirror host for a request.

    - If request is detected as mainland China, prefer i.pixiv.re (commonly faster inside CN).
    - Otherwise use the configured fallback host (defaults to i.pixiv.cat).

    Detection is best-effort and relies on CDN/reverse-proxy injected country headers
    (e.g. Cloudflare's CF-IPCountry). If missing, falls back to the configured host.
    """
    fallback = normalize_pximg_mirror_host(fallback_host) or DEFAULT_PXIMG_MIRROR_HOST
    if _get_request_country_code(headers) == "CN":
        return "i.pixiv.re"
    return fallback


def is_pximg_image_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False
    # Pixiv original images are usually served from i.pximg.net (sometimes i-cf.pximg.net).
    if host == "i.pximg.net" or host == "i-cf.pximg.net":
        return True
    return host.endswith(".pximg.net") and host.startswith("i.")


def normalize_pximg_mirror_host(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    raw = str(value or "").strip().lower()
    if not raw:
        return None

    # Support full URL forms like "https://i.pixiv.re/...".
    if raw.startswith("http://") or raw.startswith("https://"):
        try:
            parsed = urlparse(raw)
        except Exception:
            return None
        raw = (parsed.hostname or "").strip().lower()
        if not raw:
            return None

    raw = raw.strip().strip(".")
    raw = _PXIMG_MIRROR_ALIASES.get(raw, raw)
    if raw in ALLOWED_PXIMG_MIRROR_HOSTS:
        return raw
    return None


def normalize_pximg_custom_mirror_host(value: object) -> str | None:
    """
    Normalize a *custom* pximg mirror host. This does NOT enforce an allowlist.

    Intended to be used for admin-configured allowlists (trusted) or after an allowlist check
    is performed at the request boundary.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None

    raw = str(value or "").strip().lower()
    if not raw:
        return None

    if raw.startswith("http://") or raw.startswith("https://"):
        try:
            parsed = urlparse(raw)
        except Exception:
            return None
        raw = (parsed.hostname or "").strip().lower()
        if not raw:
            return None

    raw = raw.strip().strip(".")
    if not raw:
        return None

    if raw in {"localhost"}:
        return None

    try:
        ipaddress.ip_address(raw)
    except Exception:
        pass
    else:
        return None

    if not _CUSTOM_HOST_RE.match(raw):
        return None

    return raw


def normalize_pximg_proxy(value: object, *, extra_hosts: list[str] | tuple[str, ...] | None) -> str | None:
    """
    Normalize pximg mirror selection from a public `proxy=` query param.

    - Built-in hosts/aliases are always allowed.
    - Custom hosts are only allowed if present in `extra_hosts` allowlist.
    """
    built_in = normalize_pximg_mirror_host(value)
    if built_in is not None:
        return built_in

    candidate = normalize_pximg_custom_mirror_host(value)
    if candidate is None:
        return None

    if not extra_hosts:
        return None

    allow: set[str] = set()
    for item in extra_hosts:
        norm = normalize_pximg_custom_mirror_host(item)
        if norm:
            allow.add(norm)

    if candidate in allow:
        return candidate
    return None


def rewrite_pximg_to_mirror(url: str, *, mirror_host: str) -> str:
    try:
        parsed = urlparse(url)
    except Exception:
        return url

    if not is_pximg_image_url(url):
        return url

    mirror = normalize_pximg_mirror_host(mirror_host) or normalize_pximg_custom_mirror_host(mirror_host) or ""
    if not mirror:
        return url

    # Preserve scheme/path/query/fragment. Replace netloc (and drop any port/userinfo if present).
    new = parsed._replace(netloc=mirror)
    return urlunparse(new)


def rewrite_pximg_to_pixiv_cat(url: str) -> str:
    return rewrite_pximg_to_mirror(url, mirror_host=DEFAULT_PXIMG_MIRROR_HOST)
