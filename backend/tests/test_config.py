import pytest
from cryptography.fernet import Fernet

from app.core.config import load_settings
from app.core.crypto import FieldEncryptor


def test_load_settings_dev_defaults() -> None:
    s = load_settings({})
    assert s.app_env == "dev"
    assert s.database_url
    assert s.secret_key
    assert s.admin_username == "admin"
    assert s.admin_password == "admin"
    assert s.public_api_key_rate_limit_backend == "memory"
    assert s.redis_url == ""
    assert s.recent_dedup_backend == "memory"
    assert s.job_queue_backend == "sqlite"


def test_load_settings_api_key_rate_limit_backend() -> None:
    s = load_settings(
        {
            "PUBLIC_API_KEY_RATE_LIMIT_BACKEND": "redis",
            "REDIS_URL": "redis://127.0.0.1:6379/0",
        }
    )
    assert s.public_api_key_rate_limit_backend == "redis"
    assert s.redis_url == "redis://127.0.0.1:6379/0"

    s2 = load_settings({"PUBLIC_API_KEY_RATE_LIMIT_BACKEND": "weird"})
    assert s2.public_api_key_rate_limit_backend == "memory"

    s3 = load_settings({"PUBLIC_API_KEY_REDIS_URL": "redis://alias:6379/1"})
    assert s3.redis_url == "redis://alias:6379/1"


def test_load_settings_recent_dedup_backend() -> None:
    s = load_settings({"RECENT_DEDUP_BACKEND": "redis"})
    # redis is accepted as requested label; factory still returns memory until implemented.
    assert s.recent_dedup_backend == "redis"
    s2 = load_settings({"RECENT_DEDUP_BACKEND": "weird"})
    assert s2.recent_dedup_backend == "memory"


def test_load_settings_job_queue_backend() -> None:
    s = load_settings({})
    assert s.job_queue_backend == "sqlite"

    # redis/nats reserved and not implemented — fail loud (no silent sqlite fallback).
    with pytest.raises(ValueError, match="not implemented|reserved"):
        load_settings({"JOB_QUEUE_BACKEND": "redis"})
    with pytest.raises(ValueError, match="not implemented|reserved"):
        load_settings({"JOB_QUEUE_BACKEND": "nats"})

    s_mem = load_settings({"JOB_QUEUE_BACKEND": "memory"})
    assert s_mem.job_queue_backend == "memory"

    with pytest.raises(ValueError, match="not supported|not implemented|reserved"):
        load_settings({"JOB_QUEUE_BACKEND": "weird"})


def test_load_settings_prod_requires_secrets() -> None:
    with pytest.raises(ValueError):
        load_settings({"APP_ENV": "prod"})


@pytest.mark.parametrize("app_env", ["prod", "production", "PRODUCTION"])
def test_load_settings_all_production_aliases_require_explicit_credentials(app_env: str) -> None:
    with pytest.raises(ValueError, match="SECRET_KEY.*ADMIN_PASSWORD"):
        load_settings({"APP_ENV": app_env})


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("SECRET_KEY", "dev-secret-key"),
        ("SECRET_KEY", "dev-secret-key-change-me"),
        ("ADMIN_PASSWORD", "admin"),
        ("ADMIN_PASSWORD", "admin-change-me"),
    ],
)
def test_load_settings_prod_rejects_known_insecure_credentials(key: str, value: str) -> None:
    env = {
        "APP_ENV": "production",
        "SECRET_KEY": "prod-secret-key-long-enough",
        "FIELD_ENCRYPTION_KEY": Fernet.generate_key().decode("utf-8"),
        "ADMIN_PASSWORD": "prod-admin-pass",
    }
    env[key] = value

    with pytest.raises(ValueError, match=key):
        load_settings(env)


def test_load_settings_production_accepts_explicit_non_placeholder_credentials() -> None:
    settings = load_settings(
        {
            "APP_ENV": "production",
            "SECRET_KEY": "prod-secret-key-long-enough",
            "FIELD_ENCRYPTION_KEY": Fernet.generate_key().decode("utf-8"),
            "ADMIN_PASSWORD": "prod-admin-pass",
            "DATABASE_URL": "postgresql+asyncpg://ria:ria@db:5432/random_image",
        }
    )

    assert settings.is_prod is True
    assert settings.secret_key == "prod-secret-key-long-enough"
    assert settings.admin_password == "prod-admin-pass"


def test_load_settings_prod_rejects_default_sqlite_without_override() -> None:
    base = {
        "APP_ENV": "production",
        "SECRET_KEY": "prod-secret-key-long-enough",
        "FIELD_ENCRYPTION_KEY": Fernet.generate_key().decode("utf-8"),
        "ADMIN_PASSWORD": "prod-admin-pass",
    }
    # Forgetting DATABASE_URL falls into SQLite — refuse by default.
    with pytest.raises(ValueError, match="ALLOW_PROD_SQLITE"):
        load_settings(base)
    # Explicit SQLite URL is equally refused without the override.
    with pytest.raises(ValueError, match="ALLOW_PROD_SQLITE"):
        load_settings({**base, "DATABASE_URL": "sqlite+aiosqlite:///./data/app.db"})
    # The deliberate single-instance SQLite deployment stays available.
    s = load_settings({**base, "ALLOW_PROD_SQLITE": "true"})
    assert s.database_url.startswith("sqlite")


def test_load_settings_prod_requires_engine_secret_when_enabled() -> None:
    base = {
        "APP_ENV": "prod",
        "SECRET_KEY": "prod-secret-key-long-enough",
        "FIELD_ENCRYPTION_KEY": Fernet.generate_key().decode("utf-8"),
        "ADMIN_PASSWORD": "prod-admin-pass",
        "DATABASE_URL": "postgresql+asyncpg://ria:ria@db:5432/random_image",
        "PIXIV_OAUTH_CLIENT_ID": "cid",
        "PIXIV_OAUTH_CLIENT_SECRET": "csec",
        "PIXIV_OAUTH_HASH_SECRET": "hsec",
        "RANDOM_ENGINE_URL": "http://127.0.0.1:8091",
        "RANDOM_ENGINE_ENABLED": "true",
    }
    with pytest.raises(ValueError, match="RANDOM_ENGINE_SECRET"):
        load_settings({**base, "RANDOM_ENGINE_SECRET": ""})
    s = load_settings({**base, "RANDOM_ENGINE_SECRET": "engine-secret"})
    assert s.random_engine_enabled is True
    assert s.random_engine_secret == "engine-secret"


def test_load_settings_prod_cf_flags_require_secrets_not_env_bases_alone() -> None:
    """Prod may enable CF with secret only; bases can come from runtime pool overlay."""
    base = {
        "APP_ENV": "prod",
        "SECRET_KEY": "prod-secret-key-long-enough",
        "FIELD_ENCRYPTION_KEY": Fernet.generate_key().decode("utf-8"),
        "ADMIN_PASSWORD": "prod-admin-pass",
        "DATABASE_URL": "postgresql+asyncpg://ria:ria@db:5432/random_image",
        "PIXIV_OAUTH_CLIENT_ID": "cid",
        "PIXIV_OAUTH_CLIENT_SECRET": "csec",
        "PIXIV_OAUTH_HASH_SECRET": "hsec",
    }
    with pytest.raises(ValueError, match="CF_API_PROXY_SECRET"):
        load_settings(
            {
                **base,
                "CF_API_PROXY_ENABLED": "true",
                "CF_API_PROXY_BASE_URLS": "",
                "CF_API_PROXY_SECRET": "",
            }
        )
    s = load_settings(
        {
            **base,
            "CF_API_PROXY_ENABLED": "true",
            "CF_API_PROXY_BASE_URLS": "",
            "CF_API_PROXY_SECRET": "proxy-secret",
        }
    )
    assert s.cf_api_proxy_enabled is True
    assert s.cf_api_proxy_base_urls == []

    with pytest.raises(ValueError, match="IMAGE_EDGE_SECRET"):
        load_settings(
            {
                **base,
                "IMAGE_EDGE_ENABLED": "true",
                "IMAGE_EDGE_BASE_URLS": "",
                "IMAGE_EDGE_SECRET": "",
            }
        )
    s2 = load_settings(
        {
            **base,
            "IMAGE_EDGE_ENABLED": "true",
            "IMAGE_EDGE_BASE_URLS": "",
            "IMAGE_EDGE_SECRET": "edge-secret",
        }
    )
    assert s2.image_edge_enabled is True
    assert s2.image_edge_base_urls == []


def test_load_settings_dev_auto_generates_field_encryption_key(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("FIELD_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("FIELD_ENCRYPTION_KEY_FILE", raising=False)

    s1 = load_settings()
    assert s1.field_encryption_key
    FieldEncryptor.from_key(s1.field_encryption_key)

    key_path = tmp_path / "data" / "field_encryption_key"
    assert key_path.exists()

    s2 = load_settings()
    assert s2.field_encryption_key == s1.field_encryption_key


def test_load_settings_dev_pixiv_oauth_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("PIXIV_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("PIXIV_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("PIXIV_OAUTH_HASH_SECRET", raising=False)

    s = load_settings()
    assert s.pixiv_oauth_client_id
    assert s.pixiv_oauth_client_secret
    assert s.pixiv_oauth_hash_secret
