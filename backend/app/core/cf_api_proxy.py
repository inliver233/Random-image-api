from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse

from app.core.config import Settings, parse_csv_urls
from app.core.env_parse import parse_bool_env


# Hosts the default Worker allowlist accepts (keep aligned with edge/api-worker).
DEFAULT_CF_API_PROXY_HOSTS = frozenset(
    {
        "oauth.secure.pixiv.net",
        "app-api.pixiv.net",
        "public-api.secure.pixiv.net",
    }
)


@dataclass(frozen=True, slots=True)
class CfApiProxyConfig:
    enabled: bool
    base_urls: list[str]
    secret: str

    @property
    def primary_base_url(self) -> str | None:
        return self.base_urls[0] if self.base_urls else None

    @property
    def ready(self) -> bool:
        # Worker PROXY_SECRET is fail-closed; BFF must have a non-empty shared secret too.
        return bool(self.enabled and self.base_urls and (self.secret or "").strip())


def load_cf_api_proxy_config_from_settings(settings: Settings | None) -> CfApiProxyConfig | None:
    if settings is None:
        return None
    enabled = bool(getattr(settings, "cf_api_proxy_enabled", False))
    base_urls = list(getattr(settings, "cf_api_proxy_base_urls", None) or [])
    secret = str(getattr(settings, "cf_api_proxy_secret", "") or "").strip()
    if not enabled or not base_urls:
        return None
    return CfApiProxyConfig(enabled=True, base_urls=base_urls, secret=secret)


def load_cf_api_proxy_config(env: Mapping[str, str]) -> CfApiProxyConfig | None:
    enabled = parse_bool_env("CF_API_PROXY_ENABLED", default=False, env=env)
    secret = (env.get("CF_API_PROXY_SECRET") or "").strip()
    base_urls = parse_csv_urls(
        env.get("CF_API_PROXY_BASE_URLS") or env.get("CF_API_PROXY_BASE_URL") or ""
    )
    if not enabled or not base_urls:
        return None
    return CfApiProxyConfig(enabled=True, base_urls=base_urls, secret=secret)


def pick_cf_api_proxy_base_url(cfg: CfApiProxyConfig, *, host: str, path: str) -> str:
    """Sticky multi-base selection for egress diversity (same host+path → same base)."""
    bases = list(cfg.base_urls or [])
    if not bases:
        raise ValueError("base_urls is empty")
    if len(bases) == 1:
        return bases[0]
    key = f"{(host or '').lower()}\n{(path or '')}".encode("utf-8")
    digest = hashlib.sha256(key).digest()
    idx = int.from_bytes(digest[:8], "big") % len(bases)
    return bases[idx]


def is_cf_api_proxy_host_allowed(host: str, *, allowed: frozenset[str] | None = None) -> bool:
    h = (host or "").strip().lower().strip(".")
    if not h or "/" in h or ":" in h or "@" in h:
        return False
    pool = allowed if allowed is not None else DEFAULT_CF_API_PROXY_HOSTS
    return h in pool


def rewrite_url_via_cf_api_proxy(
    cfg: CfApiProxyConfig,
    url: str,
    *,
    allowed_hosts: frozenset[str] | None = None,
    base_url: str | None = None,
) -> str | None:
    """Rewrite https://host/path?q → {base}/p/host/path?q. None if not eligible.

    Uses sticky multi-base pick unless ``base_url`` is provided (failover tries remaining bases).
    Does not require ``cfg.ready`` secret so pure rewrite/vector tests stay usable.
    """
    raw = (url or "").strip()
    if not raw or not cfg.enabled or not cfg.base_urls:
        return None
    try:
        parsed = urlparse(raw)
    except Exception:
        return None
    if (parsed.scheme or "").lower() != "https":
        return None
    host = (parsed.hostname or "").lower()
    if not is_cf_api_proxy_host_allowed(host, allowed=allowed_hosts):
        return None
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    # Reject path traversal / absolute-URL smuggling in path segment.
    if ".." in path or path.startswith("//") or "\\" in path:
        return None
    if base_url is not None:
        base = str(base_url or "").strip().rstrip("/")
        if not base:
            return None
    else:
        base = pick_cf_api_proxy_base_url(cfg, host=host, path=path).rstrip("/")
    # Preserve query; drop fragment (not sent to servers anyway).
    q = f"?{parsed.query}" if parsed.query else ""
    return f"{base}/p/{host}{path}{q}"


def cf_api_proxy_headers(cfg: CfApiProxyConfig, *, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Headers to attach when calling through the CF API proxy."""
    out: dict[str, str] = {}
    if extra:
        for k, v in extra.items():
            if v is None:
                continue
            out[str(k)] = str(v)
    secret = (cfg.secret or "").strip()
    if secret:
        out["X-Proxy-Secret"] = secret
    return out


def resolve_pixiv_api_cf_candidates(
    *,
    settings: Settings | None,
    url: str,
) -> list[tuple[str, dict[str, str]]]:
    """Ordered CF rewrites for multi-base failover: sticky base first, then remaining bases.

    Empty when CF API proxy is not ready (enabled + bases + secret) or URL is ineligible.
    """
    cfg = load_cf_api_proxy_config_from_settings(settings)
    if cfg is None or not cfg.ready:
        return []
    raw = (url or "").strip()
    if not raw:
        return []
    try:
        parsed = urlparse(raw)
    except Exception:
        return []
    if (parsed.scheme or "").lower() != "https":
        return []
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    try:
        sticky = pick_cf_api_proxy_base_url(cfg, host=host, path=path)
    except ValueError:
        return []
    ordered: list[str] = []
    seen: set[str] = set()
    for base in [sticky, *list(cfg.base_urls or [])]:
        b = str(base or "").strip().rstrip("/")
        if not b or b in seen:
            continue
        seen.add(b)
        ordered.append(b)
    headers = cf_api_proxy_headers(cfg)
    out: list[tuple[str, dict[str, str]]] = []
    for base in ordered:
        rewritten = rewrite_url_via_cf_api_proxy(cfg, raw, base_url=base)
        if rewritten:
            out.append((rewritten, dict(headers)))
    return out


def resolve_pixiv_api_request(
    *,
    settings: Settings | None,
    url: str,
) -> tuple[str, dict[str, str], bool]:
    """Return (request_url, extra_headers, used_cf_proxy).

    When CF API proxy is ready and URL host is allowlisted, rewrite URL and inject secret.
    Uses sticky primary only; prefer ``resolve_pixiv_api_cf_candidates`` for multi-base failover.
    """
    candidates = resolve_pixiv_api_cf_candidates(settings=settings, url=url)
    if not candidates:
        return url, {}, False
    rewritten, headers = candidates[0]
    return rewritten, headers, True
