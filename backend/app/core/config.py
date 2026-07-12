from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from cryptography.fernet import Fernet

from app.core.crypto import FieldEncryptor
from app.core.env_parse import parse_bool_env, parse_int_env
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
    image_edge_enabled: bool
    image_edge_base_urls: list[str]
    image_edge_secret: str
    # Optional previous HMAC secret for zero-downtime rotation (verify-only on edge).
    image_edge_secret_previous: str
    image_edge_sign_ttl_seconds: int
    public_api_key_required: bool
    public_api_key_rpm: int
    public_api_key_burst: int
    # Rate-limit backend for public API keys: memory (default) | redis (needs REDIS_URL).
    public_api_key_rate_limit_backend: str
    redis_url: str
    # Short-window anti-repeat store: memory (default) | redis (requires REDIS_URL; fail-open).
    recent_dedup_backend: str
    # Job claim/enqueue port: sqlite (default) | memory. redis/nats reserved and rejected at load.
    job_queue_backend: str
    random_totals_persist_interval_seconds: int
    # Optional Go random-engine BFF dual-run / cutover (empty = Python-only pick).
    random_engine_url: str
    random_engine_enabled: bool
    random_engine_timeout_ms: int
    # Progressive cutover when engine enabled: 0=never call engine, 100=all eligible picks.
    random_engine_traffic_percent: int
    # Shared secret for engine X-Engine-Secret (empty = engine open; match RANDOM_ENGINE_SECRET).
    random_engine_secret: str
    # Optional R2 prewarm webhook after catalog upserts (Phase 2; default off).
    r2_prewarm_enabled: bool
    r2_prewarm_url: str
    # Worker X-Prewarm-Secret; empty falls back to IMAGE_EDGE_SECRET at call time.
    r2_prewarm_secret: str
    # Optional CF Worker API egress pool for hydrate/OAuth (residential proxy fallback).
    cf_api_proxy_enabled: bool
    cf_api_proxy_base_urls: list[str]
    cf_api_proxy_secret: str
    # When True (default), residential is emergency-only if CF/edge is ready (mandate).
    residential_egress_emergency_only: bool

    @property
    def is_prod(self) -> bool:
        return self.app_env in {"prod", "production"}


def _get(env: Mapping[str, str], key: str, default: str) -> str:
    value = env.get(key, default)
    return value.strip()


def parse_csv_urls(raw: str) -> list[str]:
    """Parse comma/semicolon-separated http(s) base URLs; de-dupe, strip trailing slash."""
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
    imgproxy_max_dim = parse_int_env(
        "IMGPROXY_MAX_DIM",
        default=2048,
        min_v=16,
        max_v=20_000,
        env=env,
    )

    imgproxy_default_options = _get(env, "IMGPROXY_DEFAULT_OPTIONS", "")

    imgproxy_url_chunk_size = parse_int_env(
        "IMGPROXY_URL_CHUNK_SIZE",
        default=16,
        min_v=0,
        max_v=128,
        env=env,
    )

    image_edge_enabled = parse_bool_env("IMAGE_EDGE_ENABLED", default=False, env=env)
    image_edge_secret = _get(env, "IMAGE_EDGE_SECRET", "")
    image_edge_secret_previous = _get(env, "IMAGE_EDGE_SECRET_PREVIOUS", "")
    # Never keep a previous secret that equals the active one (no dual-check noise).
    if image_edge_secret_previous and image_edge_secret_previous == image_edge_secret:
        image_edge_secret_previous = ""
    image_edge_base_urls = parse_csv_urls(
        _get(env, "IMAGE_EDGE_BASE_URLS", "") or _get(env, "IMAGE_EDGE_BASE_URL", "")
    )
    image_edge_sign_ttl_seconds = parse_int_env(
        "IMAGE_EDGE_SIGN_TTL_SECONDS",
        default=604800,
        min_v=60,
        max_v=31_536_000,
        env=env,
    )

    public_api_key_required = parse_bool_env("PUBLIC_API_KEY_REQUIRED", default=False, env=env)
    public_api_key_rpm = parse_int_env(
        "PUBLIC_API_KEY_RPM",
        default=0,
        min_v=0,
        max_v=10_000_000,
        env=env,
    )
    public_api_key_burst = parse_int_env(
        "PUBLIC_API_KEY_BURST",
        default=0,
        min_v=0,
        max_v=10_000_000,
        env=env,
    )
    # memory (default) | redis. Redis only activates when REDIS_URL is also set.
    public_api_key_rate_limit_backend = _get(env, "PUBLIC_API_KEY_RATE_LIMIT_BACKEND", "memory").lower()
    if public_api_key_rate_limit_backend not in {"memory", "redis"}:
        public_api_key_rate_limit_backend = "memory"
    redis_url = _get(env, "REDIS_URL", "") or _get(env, "PUBLIC_API_KEY_REDIS_URL", "")
    # memory (default). redis activates only with REDIS_URL (see build_recent_dedup).
    recent_dedup_backend = _get(env, "RECENT_DEDUP_BACKEND", "memory").lower()
    if recent_dedup_backend not in {"memory", "redis"}:
        recent_dedup_backend = "memory"
    # sqlite (default) | memory. redis/nats/unknown are rejected (fail loud; no silent fallback).
    job_queue_backend = _get(env, "JOB_QUEUE_BACKEND", "sqlite").lower()
    if job_queue_backend in {"redis", "nats"}:
        raise ValueError(
            f"JOB_QUEUE_BACKEND={job_queue_backend} is reserved and not implemented yet; "
            "use sqlite (default) or memory."
        )
    if job_queue_backend not in {"sqlite", "memory"}:
        raise ValueError(
            f"JOB_QUEUE_BACKEND={job_queue_backend!r} is not supported; "
            "use sqlite (default) or memory."
        )

    random_totals_persist_interval_seconds = parse_int_env(
        "RANDOM_TOTALS_PERSIST_INTERVAL_SECONDS",
        default=15,
        min_v=2,
        max_v=300,
        env=env,
    )

    random_engine_url = _get(env, "RANDOM_ENGINE_URL", "").rstrip("/")
    random_engine_enabled = parse_bool_env("RANDOM_ENGINE_ENABLED", default=False, env=env) and bool(
        random_engine_url
    )
    random_engine_timeout_ms = parse_int_env(
        "RANDOM_ENGINE_TIMEOUT_MS",
        default=800,
        min_v=50,
        max_v=10_000,
        env=env,
    )
    # Default 100 when engine is on so enabling the flag alone is full dual-run.
    random_engine_traffic_percent = parse_int_env(
        "RANDOM_ENGINE_TRAFFIC_PERCENT",
        default=100,
        min_v=0,
        max_v=100,
        env=env,
    )
    random_engine_secret = _get(env, "RANDOM_ENGINE_SECRET", "")
    r2_prewarm_url = _get(env, "R2_PREWARM_URL", "").rstrip("/")
    r2_prewarm_enabled = parse_bool_env("R2_PREWARM_ENABLED", default=False, env=env) and bool(r2_prewarm_url)
    # Prefer dedicated prewarm secret; IMAGE_EDGE_SECRET is Worker fallback (authorizePrewarm).
    r2_prewarm_secret = (
        _get(env, "R2_PREWARM_SECRET", "")
        or _get(env, "PREWARM_SECRET", "")
        or _get(env, "IMAGE_EDGE_SECRET", "")
    )

    cf_api_proxy_enabled = parse_bool_env("CF_API_PROXY_ENABLED", default=False, env=env)
    cf_api_proxy_secret = _get(env, "CF_API_PROXY_SECRET", "")
    cf_api_proxy_base_urls = parse_csv_urls(
        _get(env, "CF_API_PROXY_BASE_URLS", "") or _get(env, "CF_API_PROXY_BASE_URL", "")
    )
    # Flag stays true with bases so admin can report missing secret; ready requires secret separately.
    if not cf_api_proxy_base_urls:
        cf_api_proxy_enabled = False
    # Mandate default: residential emergency-only when CF/edge ready. Opt out for transition.
    residential_egress_emergency_only = parse_bool_env(
        "RESIDENTIAL_EGRESS_EMERGENCY_ONLY", default=True, env=env
    )

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
        image_edge_enabled=image_edge_enabled,
        image_edge_base_urls=image_edge_base_urls,
        image_edge_secret=image_edge_secret,
        image_edge_secret_previous=image_edge_secret_previous,
        image_edge_sign_ttl_seconds=image_edge_sign_ttl_seconds,
        public_api_key_required=public_api_key_required,
        public_api_key_rpm=public_api_key_rpm,
        public_api_key_burst=public_api_key_burst,
        public_api_key_rate_limit_backend=public_api_key_rate_limit_backend,
        redis_url=redis_url,
        recent_dedup_backend=recent_dedup_backend,
        job_queue_backend=job_queue_backend,
        random_totals_persist_interval_seconds=random_totals_persist_interval_seconds,
        random_engine_url=random_engine_url,
        random_engine_enabled=random_engine_enabled,
        random_engine_timeout_ms=random_engine_timeout_ms,
        random_engine_traffic_percent=random_engine_traffic_percent,
        random_engine_secret=random_engine_secret,
        r2_prewarm_enabled=r2_prewarm_enabled,
        r2_prewarm_url=r2_prewarm_url,
        r2_prewarm_secret=r2_prewarm_secret,
        cf_api_proxy_enabled=cf_api_proxy_enabled,
        cf_api_proxy_base_urls=cf_api_proxy_base_urls,
        cf_api_proxy_secret=cf_api_proxy_secret,
        residential_egress_emergency_only=residential_egress_emergency_only,
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
        if settings.image_edge_enabled and (not settings.image_edge_secret or not settings.image_edge_base_urls):
            missing.append("IMAGE_EDGE_SECRET/IMAGE_EDGE_BASE_URLS")
        if bool(getattr(settings, "cf_api_proxy_enabled", False)) and not list(
            getattr(settings, "cf_api_proxy_base_urls", None) or []
        ):
            missing.append("CF_API_PROXY_BASE_URLS")
        # Worker PROXY_SECRET is fail-closed; prod must not enable CF API proxy without secret.
        if bool(getattr(settings, "cf_api_proxy_enabled", False)) and not str(
            getattr(settings, "cf_api_proxy_secret", "") or ""
        ).strip():
            missing.append("CF_API_PROXY_SECRET")
        # Dual-run engine is open without secret; prod must not ship that surface.
        if bool(getattr(settings, "random_engine_enabled", False)) and not str(
            getattr(settings, "random_engine_secret", "") or ""
        ).strip():
            missing.append("RANDOM_ENGINE_SECRET")
        if missing:
            raise ValueError(f"Missing required env vars for prod: {', '.join(missing)}")

    return settings
