from __future__ import annotations

import hashlib
import hmac
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlparse

from app.core.b64url import b64url_encode
from app.core.config import Settings, parse_csv_urls
from app.core.env_parse import parse_bool_env, parse_int_env

# Keep aligned with edge/img-worker path allowlist (contract: contracts/image-edge.md).
_ALLOWED_EDGE_PREFIXES = ("/img-original/", "/img-master/", "/img-/", "/c/")
_ALLOWED_EDGE_EXTS = frozenset({"jpg", "jpeg", "png", "gif", "webp"})

# Settings are immutable per process boot; cache by config key for hot delivery path.
_EDGE_CFG_FROM_SETTINGS: dict[tuple[Any, ...], ImageEdgeConfig | None] = {}
_EDGE_CFG_CACHE_MAX = 32

# Process-local soft cooldown for multi-base image edge.
# Public 302 prefers sticky when healthy; cooling sticky is demoted via ordered helpers.
_IMAGE_EDGE_BASE_COOLDOWN_S = 30.0
_image_edge_base_lock = threading.Lock()
_image_edge_base_cool_until: dict[str, float] = {}


@dataclass(frozen=True, slots=True)
class ImageEdgeConfig:
    enabled: bool
    base_urls: list[str]
    secret: str
    sign_ttl_seconds: int
    # Optional previous secret: backend never signs with it; Worker may still verify.
    secret_previous: str = ""

    @property
    def primary_base_url(self) -> str | None:
        return self.base_urls[0] if self.base_urls else None

    @property
    def verify_secrets(self) -> list[str]:
        """Ordered secrets for verify (primary first, then previous if distinct)."""
        out: list[str] = []
        primary = (self.secret or "").strip()
        if primary:
            out.append(primary)
        prev = (self.secret_previous or "").strip()
        if prev and prev != primary:
            out.append(prev)
        return out


def load_image_edge_config_from_settings(settings: Settings) -> ImageEdgeConfig | None:
    enabled = bool(getattr(settings, "image_edge_enabled", False))
    secret = str(getattr(settings, "image_edge_secret", "") or "").strip()
    secret_previous = str(getattr(settings, "image_edge_secret_previous", "") or "").strip()
    if secret_previous and secret_previous == secret:
        secret_previous = ""
    env_bases = list(getattr(settings, "image_edge_base_urls", None) or [])
    try:
        from app.core.cf_pool_overlay import merge_image_bases_with_overlay

        base_urls = merge_image_bases_with_overlay(env_bases)
    except Exception:
        base_urls = list(env_bases)
    ttl = int(getattr(settings, "image_edge_sign_ttl_seconds", 604800) or 604800)
    ttl = max(60, min(ttl, 31_536_000))
    cache_key = (enabled, secret, secret_previous, tuple(base_urls), ttl)
    if cache_key in _EDGE_CFG_FROM_SETTINGS:
        return _EDGE_CFG_FROM_SETTINGS[cache_key]
    if not enabled or not secret or not base_urls:
        cfg: ImageEdgeConfig | None = None
    else:
        cfg = ImageEdgeConfig(
            enabled=True,
            base_urls=base_urls,
            secret=secret,
            sign_ttl_seconds=ttl,
            secret_previous=secret_previous,
        )
    if len(_EDGE_CFG_FROM_SETTINGS) >= _EDGE_CFG_CACHE_MAX:
        _EDGE_CFG_FROM_SETTINGS.clear()
    _EDGE_CFG_FROM_SETTINGS[cache_key] = cfg
    return cfg


def image_edge_is_ready(settings: Settings | None) -> bool:
    """True when IMAGE_EDGE is enabled and can mint signed URLs (secret + base_urls).

    Public delivery must gate prefer-edge on this so default-off does not spam
    ``edge_unavailable`` metrics on every local cascade request.
    """
    if settings is None:
        return False
    return load_image_edge_config_from_settings(settings) is not None


def load_image_edge_config(env: Mapping[str, str]) -> ImageEdgeConfig | None:
    enabled = parse_bool_env("IMAGE_EDGE_ENABLED", default=False, env=env)
    secret = (env.get("IMAGE_EDGE_SECRET") or "").strip()
    secret_previous = (env.get("IMAGE_EDGE_SECRET_PREVIOUS") or "").strip()
    if secret_previous and secret_previous == secret:
        secret_previous = ""
    base_urls = parse_csv_urls(env.get("IMAGE_EDGE_BASE_URLS") or env.get("IMAGE_EDGE_BASE_URL") or "")
    ttl = parse_int_env(
        "IMAGE_EDGE_SIGN_TTL_SECONDS",
        default=604800,
        min_v=60,
        max_v=31_536_000,
        env=env,
    )
    if not enabled or not secret or not base_urls:
        return None
    return ImageEdgeConfig(
        enabled=True,
        base_urls=base_urls,
        secret=secret,
        sign_ttl_seconds=ttl,
        secret_previous=secret_previous,
    )


def verify_image_edge_signature(
    cfg: ImageEdgeConfig,
    *,
    path: str,
    exp: int,
    sig: str,
) -> bool:
    """Verify sig against primary then previous secret (rotation window)."""
    path = (path or "").strip()
    sig = (sig or "").strip()
    if not path or not sig:
        return False
    msg = f"{int(exp)}\n{path}".encode("utf-8")
    for secret in cfg.verify_secrets:
        dig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).digest()
        expect = b64url_encode(dig)
        if hmac.compare_digest(expect, sig):
            return True
    return False


def pximg_path_from_original_url(original_url: str) -> str | None:
    raw = (original_url or "").strip()
    if not raw:
        return None
    try:
        parsed = urlparse(raw)
    except Exception:
        return None
    host = (parsed.hostname or "").lower()
    if not host.endswith("pximg.net"):
        return None
    path = parsed.path or ""
    if not path.startswith("/"):
        return None
    if ".." in path or "\\" in path or path.startswith("//"):
        return None
    # Normalize accidental double-slashes without resolving ".." escapes.
    while "//" in path:
        path = path.replace("//", "/")
    # Keep only path; query/fragment must not be signed into public URLs.
    return path


def is_edge_allowed_path(path: str) -> bool:
    """Match Worker allowlist so we never mint signed URLs the edge will reject."""
    p = (path or "").strip()
    if not p.startswith("/") or ".." in p or "\\" in p or "//" in p:
        return False
    if "://" in p or "@" in p or "?" in p:
        return False
    if not any(p.startswith(prefix) for prefix in _ALLOWED_EDGE_PREFIXES):
        return False
    ext = p.rsplit(".", 1)[-1].lower() if "." in p else ""
    return ext in _ALLOWED_EDGE_EXTS


def pick_image_edge_base_url(cfg: ImageEdgeConfig, path: str) -> str:
    """Sticky multi-base selection for egress diversity (ds2api-style multi-deploy).

    Same path always maps to the same base (cache locality + stable client URLs within TTL).
    """
    bases = list(cfg.base_urls or [])
    if not bases:
        raise ValueError("base_urls is empty")
    if len(bases) == 1:
        return bases[0]
    digest = hashlib.sha256((path or "").encode("utf-8")).digest()
    idx = int.from_bytes(digest[:8], "big") % len(bases)
    return bases[idx]


def reset_image_edge_base_cooldown_for_tests() -> None:
    """Clear process image-edge base cooldown map (unit tests only)."""
    with _image_edge_base_lock:
        _image_edge_base_cool_until.clear()


def normalize_image_edge_base_url(base: str) -> str:
    return str(base or "").strip().rstrip("/")


def is_image_edge_base_cooling(base: str, *, now: float | None = None) -> bool:
    b = normalize_image_edge_base_url(base)
    if not b:
        return False
    t = time.monotonic() if now is None else float(now)
    with _image_edge_base_lock:
        until = float(_image_edge_base_cool_until.get(b) or 0.0)
        if until <= t:
            if b in _image_edge_base_cool_until:
                _image_edge_base_cool_until.pop(b, None)
            return False
        return True


def record_image_edge_base_outcome(
    base: str,
    *,
    ok: bool,
    cooldown_s: float = _IMAGE_EDGE_BASE_COOLDOWN_S,
    now: float | None = None,
) -> None:
    """Record image-edge worker base success/failure for process-local demotion.

    Success clears cooldown. Failure opens/extends cooldown so ordered lists and public
    302 signing demote that base (prefer next hot base). Best-effort; never raises.
    """
    try:
        b = normalize_image_edge_base_url(base)
        if not b:
            return
        t = time.monotonic() if now is None else float(now)
        with _image_edge_base_lock:
            if ok:
                _image_edge_base_cool_until.pop(b, None)
                return
            cool = max(1.0, float(cooldown_s))
            prev = float(_image_edge_base_cool_until.get(b) or 0.0)
            _image_edge_base_cool_until[b] = max(prev, t + cool)
    except Exception:
        return


def order_image_edge_bases_for_failover(bases: list[str], *, now: float | None = None) -> list[str]:
    """Keep sticky-first order among healthy bases; append cooling bases last."""
    hot: list[str] = []
    cold: list[str] = []
    seen: set[str] = set()
    for raw in bases:
        b = normalize_image_edge_base_url(raw)
        if not b or b in seen:
            continue
        seen.add(b)
        if is_image_edge_base_cooling(b, now=now):
            cold.append(b)
        else:
            hot.append(b)
    return hot + cold


def ordered_image_edge_base_urls(cfg: ImageEdgeConfig, path: str) -> list[str]:
    """Sticky base first when healthy, then remaining bases (deduped).

    Mirrors ``resolve_pixiv_api_cf_candidates`` ordering for CF API proxy multi-deploy.
    Cooling bases (process-local after probe/egress hard fails) are demoted to the end.
    Public 302 / ``urls.proxy`` use the first entry so a cooling sticky does not pin traffic.
    """
    try:
        sticky = pick_image_edge_base_url(cfg, path)
    except ValueError:
        return []
    ordered: list[str] = []
    seen: set[str] = set()
    for base in [sticky, *list(cfg.base_urls or [])]:
        b = normalize_image_edge_base_url(base)
        if not b or b in seen:
            continue
        seen.add(b)
        ordered.append(b)
    return order_image_edge_bases_for_failover(ordered)


def sign_image_edge_path(cfg: ImageEdgeConfig, path: str, *, now: int | None = None, base_url: str | None = None) -> str:
    path = (path or "").strip()
    if not path.startswith("/"):
        raise ValueError("path must start with '/'")
    if not is_edge_allowed_path(path):
        raise ValueError("path not allowed by image edge contract")
    if base_url is not None and str(base_url).strip():
        base = str(base_url).strip().rstrip("/")
    else:
        ordered = ordered_image_edge_base_urls(cfg, path)
        base = (ordered[0] if ordered else pick_image_edge_base_url(cfg, path) or "").rstrip("/")
    if not base:
        raise ValueError("base_url is required")
    exp = int(now if now is not None else time.time()) + int(cfg.sign_ttl_seconds)
    msg = f"{exp}\n{path}".encode("utf-8")
    dig = hmac.new(cfg.secret.encode("utf-8"), msg, hashlib.sha256).digest()
    sig = b64url_encode(dig)
    b64path = b64url_encode(path.encode("utf-8"))
    return f"{base}/u/{exp}/{sig}/{b64path}"


def build_image_edge_url(
    cfg: ImageEdgeConfig,
    *,
    original_url: str,
    now: int | None = None,
    base_url: str | None = None,
) -> str | None:
    path = pximg_path_from_original_url(original_url)
    if path is None:
        return None
    try:
        return sign_image_edge_path(cfg, path, now=now, base_url=base_url)
    except Exception:
        return None


async def ensure_image_edge_overlay_fresh(engine: Any | None = None) -> None:
    """Best-effort multi-process refresh of runtime image pool members before signing.

    Image delivery is sync-config after this await; mirrors API egress
    ``ensure_overlay_fresh`` so register/deploy on another BFF is visible without restart.
    """
    if engine is None:
        return
    try:
        from app.core.cf_pool_overlay import ensure_overlay_fresh

        await ensure_overlay_fresh(engine)
    except Exception:
        pass


def resolve_image_edge_signed_candidates(
    *,
    settings: Settings | None,
    original_url: str,
    now: int | None = None,
) -> list[str]:
    """Ordered signed edge URLs: sticky base first when healthy, cooling bases last.

    Empty when edge is not ready or ``original_url`` is not a pximg path.
    Public 302 / ``urls.proxy`` use the first ordered (hot) base via
    ``sign_image_edge_path`` / ``build_image_edge_url``; this helper returns the full
    multi-base candidate list for ops/probe callers.
    """
    if settings is None:
        return []
    cfg = load_image_edge_config_from_settings(settings)
    if cfg is None:
        return []
    path = pximg_path_from_original_url(original_url)
    if path is None:
        return []
    out: list[str] = []
    for base in ordered_image_edge_base_urls(cfg, path):
        try:
            signed = sign_image_edge_path(cfg, path, now=now, base_url=base)
        except Exception:
            continue
        if signed:
            out.append(signed)
    return out


def resolve_public_proxy_url(
    *,
    settings: Settings,
    original_url: str,
    local_proxy_path: str,
) -> str:
    """Prefer CF image edge signed URL; fall back to local /i/{id}.{ext} path."""
    cfg = load_image_edge_config_from_settings(settings)
    if cfg is not None:
        edge = build_image_edge_url(cfg, original_url=original_url)
        if edge:
            return edge
    return local_proxy_path


def resolve_image_edge_redirect_url(
    *,
    settings: Settings,
    original_url: str,
) -> str | None:
    """Return absolute signed edge URL for 302, or None to keep local stream/proxy.

    Uses ordered multi-base selection (sticky first when healthy; cooling demoted) via
    ``build_image_edge_url`` / ``sign_image_edge_path``. For full candidate lists see
    ``resolve_image_edge_signed_candidates``.
    Callers on multi-process BFFs should ``await ensure_image_edge_overlay_fresh(engine)`` first.
    """
    cfg = load_image_edge_config_from_settings(settings)
    if cfg is None:
        return None
    return build_image_edge_url(cfg, original_url=original_url)
