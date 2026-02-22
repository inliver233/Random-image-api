from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from typing import Mapping

from app.core.config import Settings


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
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def encode_source_url(source_url: str, *, chunk_size: int) -> str:
    source_url = (source_url or "").strip()
    if not source_url:
        raise ValueError("source_url is required")

    encoded = urlsafe_b64_no_pad(source_url.encode("utf-8"))
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
    return urlsafe_b64_no_pad(mac.digest())


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

    try:
        max_dim = int((env.get("IMGPROXY_MAX_DIM") or "2048").strip() or "2048")
    except Exception:
        max_dim = 2048
    max_dim = max(16, min(int(max_dim), 20_000))

    default_options = (env.get("IMGPROXY_DEFAULT_OPTIONS") or "").strip().strip("/")
    if not default_options:
        default_options = f"rs:fit:{max_dim}:{max_dim}"

    try:
        url_chunk_size = int((env.get("IMGPROXY_URL_CHUNK_SIZE") or "16").strip() or "16")
    except Exception:
        url_chunk_size = 16
    url_chunk_size = max(0, min(int(url_chunk_size), 128))

    return ImgproxyConfig(
        base_url=base_url.rstrip("/"),
        key=key,
        salt=salt,
        max_dim=max_dim,
        default_options=default_options,
        url_chunk_size=url_chunk_size,
    )

