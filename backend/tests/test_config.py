import pytest

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


def test_load_settings_prod_requires_secrets() -> None:
    with pytest.raises(ValueError):
        load_settings({"APP_ENV": "prod"})


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
