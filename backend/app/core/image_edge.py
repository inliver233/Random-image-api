from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse

from app.core.config import Settings

# Keep aligned with edge/img-worker path allowlist (contract: contracts/image-edge.md).
_ALLOWED_EDGE_PREFIXES = ("/img-original/", "/img-master/", "/img-/", "/c/")
_ALLOWED_EDGE_EXTS = frozenset({"jpg", "jpeg", "png", "gif", "webp"})


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


def _urlsafe_b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _parse_base_urls(raw: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for part in (raw or "").replace(";", ",").split(","):
        base = part.strip().rstrip("/")
        if not base or base in seen:
            continue
        if not (base.startswith("https://") or base.startswith("http://")):
            continue
        seen.add(base)
        out.append(base)
    return out


def load_image_edge_config_from_settings(settings: Settings) -> ImageEdgeConfig | None:
    enabled = bool(getattr(settings, "image_edge_enabled", False))
    secret = str(getattr(settings, "image_edge_secret", "") or "").strip()
    secret_previous = str(getattr(settings, "image_edge_secret_previous", "") or "").strip()
    if secret_previous and secret_previous == secret:
        secret_previous = ""
    base_urls = list(getattr(settings, "image_edge_base_urls", None) or [])
    ttl = int(getattr(settings, "image_edge_sign_ttl_seconds", 604800) or 604800)
    ttl = max(60, min(ttl, 31_536_000))
    if not enabled or not secret or not base_urls:
        return None
    return ImageEdgeConfig(
        enabled=True,
        base_urls=base_urls,
        secret=secret,
        sign_ttl_seconds=ttl,
        secret_previous=secret_previous,
    )


def load_image_edge_config(env: Mapping[str, str]) -> ImageEdgeConfig | None:
    raw_enabled = (env.get("IMAGE_EDGE_ENABLED") or "").strip().lower()
    enabled = raw_enabled in {"1", "true", "yes", "y", "on"}
    secret = (env.get("IMAGE_EDGE_SECRET") or "").strip()
    secret_previous = (env.get("IMAGE_EDGE_SECRET_PREVIOUS") or "").strip()
    if secret_previous and secret_previous == secret:
        secret_previous = ""
    base_urls = _parse_base_urls(env.get("IMAGE_EDGE_BASE_URLS") or env.get("IMAGE_EDGE_BASE_URL") or "")
    try:
        ttl = int((env.get("IMAGE_EDGE_SIGN_TTL_SECONDS") or "604800").strip() or "604800")
    except Exception:
        ttl = 604800
    ttl = max(60, min(ttl, 31_536_000))
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
        expect = _urlsafe_b64(dig)
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
    if not p.startswith("/") or ".." in p or "\\" in p or p.startswith("//"):
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


def sign_image_edge_path(cfg: ImageEdgeConfig, path: str, *, now: int | None = None, base_url: str | None = None) -> str:
    path = (path or "").strip()
    if not path.startswith("/"):
        raise ValueError("path must start with '/'")
    if not is_edge_allowed_path(path):
        raise ValueError("path not allowed by image edge contract")
    base = (base_url or pick_image_edge_base_url(cfg, path) or "").rstrip("/")
    if not base:
        raise ValueError("base_url is required")
    exp = int(now if now is not None else time.time()) + int(cfg.sign_ttl_seconds)
    msg = f"{exp}\n{path}".encode("utf-8")
    dig = hmac.new(cfg.secret.encode("utf-8"), msg, hashlib.sha256).digest()
    sig = _urlsafe_b64(dig)
    b64path = _urlsafe_b64(path.encode("utf-8"))
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
    """Return absolute signed edge URL for 302, or None to keep local stream/proxy."""
    cfg = load_image_edge_config_from_settings(settings)
    if cfg is None:
        return None
    return build_image_edge_url(cfg, original_url=original_url)
