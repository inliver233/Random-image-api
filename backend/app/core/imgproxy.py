from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Mapping

from app.core.b64url import b64url_encode
from app.core.config import Settings
from app.core.env_parse import parse_int_env


@dataclass(frozen=True, slots=True)
class ImgproxyConfig:
    base_url: str
    key: bytes
    salt: bytes
    max_dim: int
    default_options: str
    url_chunk_size: int


def _decode_hex(raw: str, *, name: str) -> bytes:
    raw = (raw or "").strip()
    if not raw:
        raise ValueError(f"{name} is required")
    try:
        return bytes.fromhex(raw)
    except Exception as exc:
        raise ValueError(f"{name} must be hex") from exc


def load_imgproxy_config_from_settings(settings: Settings) -> ImgproxyConfig | None:
    base_url = (settings.imgproxy_base_url or "").strip()
    if not base_url:
        return None

    key = _decode_hex(settings.imgproxy_key, name="IMGPROXY_KEY")
    salt = _decode_hex(settings.imgproxy_salt, name="IMGPROXY_SALT")

    default_options = (settings.imgproxy_default_options or "").strip().strip("/")
    if not default_options:
        default_options = f"rs:fit:{int(settings.imgproxy_max_dim)}:{int(settings.imgproxy_max_dim)}"

    return ImgproxyConfig(
        base_url=base_url.rstrip("/"),
        key=key,
        salt=salt,
        max_dim=int(settings.imgproxy_max_dim),
        default_options=default_options,
        url_chunk_size=int(settings.imgproxy_url_chunk_size),
    )


def urlsafe_b64_no_pad(raw: bytes) -> str:
    """Backward-compatible alias for shared b64url_encode."""
    return b64url_encode(raw)


def encode_source_url(source_url: str, *, chunk_size: int) -> str:
    source_url = (source_url or "").strip()
    if not source_url:
        raise ValueError("source_url is required")

    encoded = b64url_encode(source_url.encode("utf-8"))
    if chunk_size <= 0 or len(encoded) <= chunk_size:
        return encoded
    return "/".join(encoded[i : i + chunk_size] for i in range(0, len(encoded), chunk_size))


def sign_path(cfg: ImgproxyConfig, path_after_signature: str) -> str:
    path = (path_after_signature or "").strip()
    if not path.startswith("/"):
        raise ValueError("path_after_signature must start with '/'")

    mac = hmac.new(cfg.key, digestmod=hashlib.sha256)
    mac.update(cfg.salt)
    mac.update(path.encode("utf-8"))
    return b64url_encode(mac.digest())


def build_processing_path(
    *,
    processing_options: str,
    source_url: str,
    extension: str,
    url_chunk_size: int,
) -> str:
    processing_options = (processing_options or "").strip().strip("/")
    if not processing_options:
        raise ValueError("processing_options is required")

    extension = (extension or "").strip().lower().lstrip(".")
    if not extension or len(extension) > 10 or any(c for c in extension if not (c.isalnum() or c == "_")):
        raise ValueError("extension is invalid")

    encoded = encode_source_url(source_url, chunk_size=int(url_chunk_size))
    return f"/{processing_options}/{encoded}.{extension}"


def build_signed_processing_url(
    cfg: ImgproxyConfig,
    *,
    source_url: str,
    extension: str,
    processing_options: str | None = None,
) -> str:
    path = build_processing_path(
        processing_options=processing_options or cfg.default_options,
        source_url=source_url,
        extension=extension,
        url_chunk_size=int(cfg.url_chunk_size),
    )
    sig = sign_path(cfg, path)
    return f"{cfg.base_url}/{sig}{path}"


def load_imgproxy_config(env: Mapping[str, str]) -> ImgproxyConfig | None:
    base_url = (env.get("IMGPROXY_BASE_URL") or "").strip()
    if not base_url:
        return None

    key = _decode_hex(env.get("IMGPROXY_KEY") or "", name="IMGPROXY_KEY")
    salt = _decode_hex(env.get("IMGPROXY_SALT") or "", name="IMGPROXY_SALT")

    max_dim = parse_int_env(
        "IMGPROXY_MAX_DIM",
        default=2048,
        min_v=16,
        max_v=20_000,
        env=env,
    )

    default_options = (env.get("IMGPROXY_DEFAULT_OPTIONS") or "").strip().strip("/")
    if not default_options:
        default_options = f"rs:fit:{max_dim}:{max_dim}"

    url_chunk_size = parse_int_env(
        "IMGPROXY_URL_CHUNK_SIZE",
        default=16,
        min_v=0,
        max_v=128,
        env=env,
    )

    return ImgproxyConfig(
        base_url=base_url.rstrip("/"),
        key=key,
        salt=salt,
        max_dim=max_dim,
        default_options=default_options,
        url_chunk_size=url_chunk_size,
    )

