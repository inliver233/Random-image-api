from __future__ import annotations

import hashlib
import threading
import time
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

# Process-local soft cooldown after CF base transport/5xx/gate failures (not DB; not residential).
# Sticky pick still prefers a healthy sticky base; cooling bases are demoted to the end.
# P0-5 health: consecutive-fail exponential backoff (proxy_health-inspired), success clears streak.
_CF_BASE_COOLDOWN_S = 30.0
_CF_BASE_COOLDOWN_MAX_S = 300.0
_cf_base_lock = threading.Lock()
_cf_base_cool_until: dict[str, float] = {}
_cf_base_fail_streak: dict[str, int] = {}


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
    env_enabled = bool(getattr(settings, "cf_api_proxy_enabled", False))
    env_bases = list(getattr(settings, "cf_api_proxy_base_urls", None) or [])
    secret = str(getattr(settings, "cf_api_proxy_secret", "") or "").strip()
    # Merge ops-registered runtime bases + deploy auto-enable/secret overlay.
    try:
        from app.core.cf_pool_overlay import (
            get_api_overlay_enabled,
            get_api_overlay_secret,
            merge_api_bases_with_overlay,
        )

        base_urls = merge_api_bases_with_overlay(env_bases)
        enabled = bool(env_enabled or get_api_overlay_enabled())
        if not secret:
            secret = str(get_api_overlay_secret() or "").strip()
    except Exception:
        base_urls = list(env_bases)
        enabled = env_enabled
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


def reset_cf_base_cooldown_for_tests() -> None:
    """Clear process CF base cooldown map (unit tests only)."""
    with _cf_base_lock:
        _cf_base_cool_until.clear()
        _cf_base_fail_streak.clear()


def normalize_cf_proxy_base_url(base: str) -> str:
    return str(base or "").strip().rstrip("/")


def cf_proxy_base_from_request_url(request_url: str) -> str | None:
    """Extract worker base from a rewritten CF proxy URL ``{base}/p/{host}/…``."""
    raw = (request_url or "").strip()
    if not raw:
        return None
    try:
        parsed = urlparse(raw)
    except Exception:
        return None
    path = parsed.path or ""
    marker = "/p/"
    idx = path.find(marker)
    if idx < 0:
        return None
    origin = f"{(parsed.scheme or 'https').lower()}://{(parsed.netloc or '').lower()}"
    if not parsed.netloc:
        return None
    return normalize_cf_proxy_base_url(origin)


def is_cf_base_cooling(base: str, *, now: float | None = None) -> bool:
    b = normalize_cf_proxy_base_url(base)
    if not b:
        return False
    t = time.monotonic() if now is None else float(now)
    with _cf_base_lock:
        until = float(_cf_base_cool_until.get(b) or 0.0)
        if until <= t:
            if b in _cf_base_cool_until:
                _cf_base_cool_until.pop(b, None)
            return False
        return True


def snapshot_cf_base_cooldown(
    bases: list[str] | None = None,
    *,
    now: float | None = None,
) -> list[dict[str, object]]:
    """Process-local CF base demotion snapshot for admin observability (P0-5).

    Returns one row per known cooling base (or per requested base when provided).
    Never raises; times are relative remaining seconds (not wall clock).
    """
    t = time.monotonic() if now is None else float(now)
    out: list[dict[str, object]] = []
    try:
        with _cf_base_lock:
            if bases is None:
                keys = sorted(set(_cf_base_cool_until.keys()) | set(_cf_base_fail_streak.keys()))
            else:
                keys = []
                seen: set[str] = set()
                for raw in bases:
                    b = normalize_cf_proxy_base_url(raw)
                    if not b or b in seen:
                        continue
                    seen.add(b)
                    keys.append(b)
            for b in keys:
                until = float(_cf_base_cool_until.get(b) or 0.0)
                streak = int(_cf_base_fail_streak.get(b) or 0)
                remaining = max(0.0, until - t) if until > t else 0.0
                cooling = remaining > 0.0
                if bases is None and not cooling and streak <= 0:
                    continue
                out.append(
                    {
                        "base_url": b,
                        "cooling": cooling,
                        "fail_streak": streak,
                        "cool_remaining_s": round(remaining, 3) if cooling else 0.0,
                        "cooldown_base_s": _CF_BASE_COOLDOWN_S,
                        "cooldown_max_s": _CF_BASE_COOLDOWN_MAX_S,
                    }
                )
    except Exception:
        return []
    return out


def is_cf_worker_gate_status(status_code: int | None) -> bool:
    """True for Worker-local gate statuses (auth/rate) that should failover to next CF base.

    api-worker returns 403 Forbidden on bad/missing secret and 429 on isolate rate limit
    *before* upstream Pixiv; those are base/config faults, not Pixiv business errors.
    """
    if status_code is None:
        return False
    try:
        code = int(status_code)
    except (TypeError, ValueError):
        return False
    return code in {401, 403, 429}


def should_failover_cf_attempt(status_code: int | None) -> bool:
    """Whether a via_cf attempt should continue to the next CF (or residential) candidate.

    Transport-like (None), upstream 5xx, and Worker gate 401/403/429 → yes.
    Typical Pixiv 4xx (e.g. OAuth 400 invalid_grant) → no (raise / stop).
    """
    if status_code is None:
        return True
    try:
        code = int(status_code)
    except (TypeError, ValueError):
        return True
    if code >= 500:
        return True
    return is_cf_worker_gate_status(code)


def record_cf_base_outcome(
    base_or_request_url: str,
    *,
    ok: bool,
    cooldown_s: float = _CF_BASE_COOLDOWN_S,
    max_cooldown_s: float = _CF_BASE_COOLDOWN_MAX_S,
    now: float | None = None,
) -> None:
    """Record CF worker base success/failure for process-local demotion.

    Success clears cooldown + fail streak (decay). Failure increments streak and
    applies exponential cooldown: base * 2^(streak-1), capped at max_cooldown_s.
    Best-effort; never raises.
    """
    try:
        b = normalize_cf_proxy_base_url(base_or_request_url)
        if "/p/" in (base_or_request_url or ""):
            extracted = cf_proxy_base_from_request_url(base_or_request_url)
            if extracted:
                b = extracted
        if not b:
            return
        t = time.monotonic() if now is None else float(now)
        with _cf_base_lock:
            if ok:
                _cf_base_cool_until.pop(b, None)
                _cf_base_fail_streak.pop(b, None)
                return
            streak = int(_cf_base_fail_streak.get(b) or 0) + 1
            _cf_base_fail_streak[b] = streak
            base_cool = max(1.0, float(cooldown_s))
            cap = max(base_cool, float(max_cooldown_s))
            # streak 1 → base, 2 → 2x, 3 → 4x … until cap
            exp = min(16, max(0, streak - 1))
            cool = min(cap, base_cool * float(2**exp))
            prev = float(_cf_base_cool_until.get(b) or 0.0)
            _cf_base_cool_until[b] = max(prev, t + cool)
    except Exception:
        return


def order_cf_bases_for_failover(bases: list[str], *, now: float | None = None) -> list[str]:
    """Keep sticky-first order among healthy bases; append cooling bases last."""
    hot: list[str] = []
    cold: list[str] = []
    seen: set[str] = set()
    for raw in bases:
        b = normalize_cf_proxy_base_url(raw)
        if not b or b in seen:
            continue
        seen.add(b)
        if is_cf_base_cooling(b, now=now):
            cold.append(b)
        else:
            hot.append(b)
    return hot + cold


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
        b = normalize_cf_proxy_base_url(base)
        if not b or b in seen:
            continue
        seen.add(b)
        ordered.append(b)
    # Demote process-local cooling bases so sticky dead members do not lead forever.
    ordered = order_cf_bases_for_failover(ordered)
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
