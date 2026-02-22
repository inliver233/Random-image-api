from __future__ import annotations

import pytest

from app.core.proxy_uri import parse_proxy_uri


def test_parse_proxy_uri_password_contains_at() -> None:
    u = "http://inliver:inliverBAIPIAO@123@152.53.91.30:2323"
    parts = parse_proxy_uri(u)
    assert parts.scheme == "http"
    assert parts.host == "152.53.91.30"
    assert parts.port == 2323
    assert parts.username == "inliver"
    assert parts.password == "inliverBAIPIAO@123"


def test_parse_proxy_uri_password_urlencoded() -> None:
    u = "http://inliver:inliverBAIPIAO%40123@152.53.91.30:2323"
    parts = parse_proxy_uri(u)
    assert parts.scheme == "http"
    assert parts.host == "152.53.91.30"
    assert parts.port == 2323
    assert parts.username == "inliver"
    assert parts.password == "inliverBAIPIAO@123"


def test_parse_proxy_uri_socks5_no_auth() -> None:
    u = "socks5://127.0.0.1:1080"
    parts = parse_proxy_uri(u)
    assert parts.scheme == "socks5"
    assert parts.host == "127.0.0.1"
    assert parts.port == 1080
    assert parts.username is None
    assert parts.password is None


def test_parse_proxy_uri_rejects_missing_port() -> None:
    with pytest.raises(ValueError, match="missing port"):
        parse_proxy_uri("http://127.0.0.1")


def test_parse_proxy_uri_rejects_unknown_scheme() -> None:
    with pytest.raises(ValueError, match="unsupported scheme"):
        parse_proxy_uri("ftp://127.0.0.1:21")
