from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from cryptography.fernet import Fernet

from app.core.crypto import FieldEncryptor
from app.core.logging import get_logger

log = get_logger(__name__)

_DEFAULT_PIXIV_OAUTH_CLIENT_ID = "MOBrBDS8blbauoSck0ZfDbtuzpyT"
_DEFAULT_PIXIV_OAUTH_CLIENT_SECRET = "lsACyCD94FhDUtGTXi3QzcFE2uU1hqtDaKeqrdwj"
_DEFAULT_PIXIV_OAUTH_HASH_SECRET = "28c1fdd170a5204386cb1313c7077b34f83e4aaf4aa829ce78c231e05b0bae2c"


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: str
    database_url: str
    secret_key: str
    field_encryption_key: str
    admin_username: str
    admin_password: str
    pixiv_oauth_client_id: str
    pixiv_oauth_client_secret: str
    pixiv_oauth_hash_secret: str
    imgproxy_base_url: str
    imgproxy_key: str
    imgproxy_salt: str
    imgproxy_max_dim: int
    imgproxy_default_options: str
    imgproxy_url_chunk_size: int
    public_api_key_required: bool
    public_api_key_rpm: int
    public_api_key_burst: int

    @property
    def is_prod(self) -> bool:
        return self.app_env in {"prod", "production"}


def _get(env: Mapping[str, str], key: str, default: str) -> str:
    value = env.get(key, default)
    return value.strip()


def _get_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = _get(env, key, "1" if default else "0").lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _read_key_file(path: Path) -> str | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except Exception as exc:
        log.warning("field_encryption_key_read_failed path=%s err=%s", str(path), type(exc).__name__)
        return None

    value = raw.strip()
    return value or None


def _atomic_write(path: Path, *, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    os.replace(tmp, path)


def _ensure_field_encryption_key(env: Mapping[str, str], *, app_env: str) -> str:
    key = _get(env, "FIELD_ENCRYPTION_KEY", "")
    if key:
        FieldEncryptor.from_key(key)
        return key

    file_raw = _get(env, "FIELD_ENCRYPTION_KEY_FILE", "")
    key_file = Path(file_raw) if file_raw else Path("./data/field_encryption_key")

    from_file = _read_key_file(key_file)
    if from_file is not None:
        FieldEncryptor.from_key(from_file)
        return from_file

    if app_env in {"prod", "production"}:
        return ""

    generated = Fernet.generate_key().decode("utf-8")
    try:
        _atomic_write(key_file, content=generated + "\n")
        log.info("field_encryption_key_generated path=%s", str(key_file))
    except Exception as exc:
        log.warning(
            "field_encryption_key_generated_not_persisted path=%s err=%s",
            str(key_file),
            type(exc).__name__,
        )
    return generated


def _ensure_pixiv_oauth_config(env: Mapping[str, str], *, app_env: str) -> tuple[str, str, str]:
    client_id = _get(env, "PIXIV_OAUTH_CLIENT_ID", "")
    client_secret = _get(env, "PIXIV_OAUTH_CLIENT_SECRET", "")
    hash_secret = _get(env, "PIXIV_OAUTH_HASH_SECRET", "")

    if app_env not in {"prod", "production"}:
        client_id = client_id or _DEFAULT_PIXIV_OAUTH_CLIENT_ID
        client_secret = client_secret or _DEFAULT_PIXIV_OAUTH_CLIENT_SECRET
        hash_secret = hash_secret or _DEFAULT_PIXIV_OAUTH_HASH_SECRET

    return client_id, client_secret, hash_secret


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = env or os.environ

    app_env = _get(env, "APP_ENV", "dev").lower()
    database_url = _get(env, "DATABASE_URL", "sqlite+aiosqlite:///./data/app.db")
    secret_key = _get(env, "SECRET_KEY", "dev-secret-key" if app_env != "prod" else "")
    field_encryption_key = _ensure_field_encryption_key(env, app_env=app_env)

    admin_username = _get(env, "ADMIN_USERNAME", "admin")
    admin_password = _get(env, "ADMIN_PASSWORD", "admin" if app_env != "prod" else "")

    pixiv_oauth_client_id, pixiv_oauth_client_secret, pixiv_oauth_hash_secret = _ensure_pixiv_oauth_config(
        env, app_env=app_env
    )

    imgproxy_base_url = _get(env, "IMGPROXY_BASE_URL", "")
    imgproxy_key = _get(env, "IMGPROXY_KEY", "")
    imgproxy_salt = _get(env, "IMGPROXY_SALT", "")
    try:
        imgproxy_max_dim = int(_get(env, "IMGPROXY_MAX_DIM", "2048") or "2048")
    except Exception:
        imgproxy_max_dim = 2048
    imgproxy_max_dim = max(16, min(int(imgproxy_max_dim), 20_000))

    imgproxy_default_options = _get(env, "IMGPROXY_DEFAULT_OPTIONS", "")

    try:
        imgproxy_url_chunk_size = int(_get(env, "IMGPROXY_URL_CHUNK_SIZE", "16") or "16")
    except Exception:
        imgproxy_url_chunk_size = 16
    imgproxy_url_chunk_size = max(0, min(int(imgproxy_url_chunk_size), 128))

    public_api_key_required = _get_bool(env, "PUBLIC_API_KEY_REQUIRED", False)
    try:
        public_api_key_rpm = int(_get(env, "PUBLIC_API_KEY_RPM", "0") or "0")
    except Exception:
        public_api_key_rpm = 0
    public_api_key_rpm = max(0, min(int(public_api_key_rpm), 10_000_000))
    try:
        public_api_key_burst = int(_get(env, "PUBLIC_API_KEY_BURST", "0") or "0")
    except Exception:
        public_api_key_burst = 0
    public_api_key_burst = max(0, min(int(public_api_key_burst), 10_000_000))

    settings = Settings(
        app_env=app_env,
        database_url=database_url,
        secret_key=secret_key,
        field_encryption_key=field_encryption_key,
        admin_username=admin_username,
        admin_password=admin_password,
        pixiv_oauth_client_id=pixiv_oauth_client_id,
        pixiv_oauth_client_secret=pixiv_oauth_client_secret,
        pixiv_oauth_hash_secret=pixiv_oauth_hash_secret,
        imgproxy_base_url=imgproxy_base_url,
        imgproxy_key=imgproxy_key,
        imgproxy_salt=imgproxy_salt,
        imgproxy_max_dim=imgproxy_max_dim,
        imgproxy_default_options=imgproxy_default_options,
        imgproxy_url_chunk_size=imgproxy_url_chunk_size,
        public_api_key_required=public_api_key_required,
        public_api_key_rpm=public_api_key_rpm,
        public_api_key_burst=public_api_key_burst,
    )

    if settings.is_prod:
        missing: list[str] = []
        if not settings.secret_key:
            missing.append("SECRET_KEY")
        if not settings.field_encryption_key:
            missing.append("FIELD_ENCRYPTION_KEY")
        if not settings.admin_password:
            missing.append("ADMIN_PASSWORD")
        if settings.imgproxy_base_url and (not settings.imgproxy_key or not settings.imgproxy_salt):
            missing.append("IMGPROXY_KEY/IMGPROXY_SALT")
        if missing:
            raise ValueError(f"Missing required env vars for prod: {', '.join(missing)}")

    return settings
