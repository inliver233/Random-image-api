from __future__ import annotations

from types import SimpleNamespace

from cryptography.fernet import Fernet

from app.core.crypto import FieldEncryptor
from app.core.proxy_routing import (
    _proxy_uri_from_endpoint_row,
    reset_proxy_uri_cache_for_tests,
)


def _settings(key: str) -> SimpleNamespace:
    return SimpleNamespace(field_encryption_key=key)


def test_proxy_uri_decrypt_cache_hits_same_endpoint() -> None:
    reset_proxy_uri_cache_for_tests()
    key = Fernet.generate_key().decode("utf-8")
    enc = FieldEncryptor.from_key(key)
    password_enc = enc.encrypt_text("s3cret")
    settings = _settings(key)

    a = _proxy_uri_from_endpoint_row(
        settings,  # type: ignore[arg-type]
        endpoint_id=7,
        pool_id=1,
        scheme="http",
        host="10.0.0.1",
        port=8080,
        username="u",
        password_enc=password_enc,
    )
    b = _proxy_uri_from_endpoint_row(
        settings,  # type: ignore[arg-type]
        endpoint_id=7,
        pool_id=1,
        scheme="http",
        host="10.0.0.1",
        port=8080,
        username="u",
        password_enc=password_enc,
    )
    assert a.uri == b.uri
    assert a.uri  # cached decrypt path returns same URI string
    assert "s3cret" in a.uri
    assert a.endpoint_id == 7
    assert a.pool_id == 1


def test_proxy_uri_cache_miss_on_password_rotation() -> None:
    reset_proxy_uri_cache_for_tests()
    key = Fernet.generate_key().decode("utf-8")
    enc = FieldEncryptor.from_key(key)
    enc1 = enc.encrypt_text("old-pass")
    enc2 = enc.encrypt_text("new-pass")
    settings = _settings(key)

    old = _proxy_uri_from_endpoint_row(
        settings,  # type: ignore[arg-type]
        endpoint_id=3,
        pool_id=2,
        scheme="http",
        host="10.0.0.2",
        port=3128,
        username="u",
        password_enc=enc1,
    )
    new = _proxy_uri_from_endpoint_row(
        settings,  # type: ignore[arg-type]
        endpoint_id=3,
        pool_id=2,
        scheme="http",
        host="10.0.0.2",
        port=3128,
        username="u",
        password_enc=enc2,
    )
    assert old is not new
    assert "old-pass" in old.uri
    assert "new-pass" in new.uri


def test_proxy_uri_cache_reuses_uri_across_pools() -> None:
    reset_proxy_uri_cache_for_tests()
    key = Fernet.generate_key().decode("utf-8")
    enc = FieldEncryptor.from_key(key)
    password_enc = enc.encrypt_text("p")
    settings = _settings(key)

    p1 = _proxy_uri_from_endpoint_row(
        settings,  # type: ignore[arg-type]
        endpoint_id=9,
        pool_id=1,
        scheme="socks5",
        host="127.0.0.1",
        port=1080,
        username="",
        password_enc=password_enc,
    )
    p2 = _proxy_uri_from_endpoint_row(
        settings,  # type: ignore[arg-type]
        endpoint_id=9,
        pool_id=2,
        scheme="socks5",
        host="127.0.0.1",
        port=1080,
        username="",
        password_enc=password_enc,
    )
    assert p1.pool_id == 1
    assert p2.pool_id == 2
    assert p1.uri == p2.uri  # decrypt cached; pool reattached per call
