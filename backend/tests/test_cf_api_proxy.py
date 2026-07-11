from __future__ import annotations

from app.core.cf_api_proxy import (
    CfApiProxyConfig,
    cf_api_proxy_headers,
    is_cf_api_proxy_host_allowed,
    load_cf_api_proxy_config,
    pick_cf_api_proxy_base_url,
    resolve_pixiv_api_request,
    rewrite_url_via_cf_api_proxy,
)
from app.core.config import Settings, load_settings


def test_load_cf_api_proxy_config_disabled_by_default() -> None:
    cfg = load_cf_api_proxy_config({})
    assert cfg is None


def test_load_cf_api_proxy_config_requires_bases() -> None:
    cfg = load_cf_api_proxy_config(
        {
            "CF_API_PROXY_ENABLED": "1",
            "CF_API_PROXY_BASE_URLS": "",
        }
    )
    assert cfg is None


def test_load_cf_api_proxy_config_ready() -> None:
    cfg = load_cf_api_proxy_config(
        {
            "CF_API_PROXY_ENABLED": "true",
            "CF_API_PROXY_BASE_URLS": "https://api-a.example.com,https://api-b.example.com",
            "CF_API_PROXY_SECRET": "s3cret",
        }
    )
    assert cfg is not None
    assert cfg.ready is True
    assert cfg.base_urls == ["https://api-a.example.com", "https://api-b.example.com"]
    assert cfg.secret == "s3cret"


def test_host_allowlist() -> None:
    assert is_cf_api_proxy_host_allowed("app-api.pixiv.net") is True
    assert is_cf_api_proxy_host_allowed("oauth.secure.pixiv.net") is True
    assert is_cf_api_proxy_host_allowed("evil.example.com") is False
    assert is_cf_api_proxy_host_allowed("app-api.pixiv.net:443") is False
    assert is_cf_api_proxy_host_allowed("") is False


def test_rewrite_url_via_cf_api_proxy() -> None:
    cfg = CfApiProxyConfig(
        enabled=True,
        base_urls=["https://edge.example.com"],
        secret="tok",
    )
    out = rewrite_url_via_cf_api_proxy(
        cfg,
        "https://app-api.pixiv.net/v1/illust/detail?illust_id=1&filter=for_android",
    )
    assert out == (
        "https://edge.example.com/p/app-api.pixiv.net/v1/illust/detail"
        "?illust_id=1&filter=for_android"
    )


def test_rewrite_rejects_non_allowlisted() -> None:
    cfg = CfApiProxyConfig(enabled=True, base_urls=["https://edge.example.com"], secret="")
    assert rewrite_url_via_cf_api_proxy(cfg, "https://httpbin.org/get") is None
    assert rewrite_url_via_cf_api_proxy(cfg, "http://app-api.pixiv.net/v1/x") is None


def test_sticky_multi_base_is_stable() -> None:
    cfg = CfApiProxyConfig(
        enabled=True,
        base_urls=["https://a.example.com", "https://b.example.com", "https://c.example.com"],
        secret="",
    )
    a1 = pick_cf_api_proxy_base_url(cfg, host="app-api.pixiv.net", path="/v1/illust/detail")
    a2 = pick_cf_api_proxy_base_url(cfg, host="app-api.pixiv.net", path="/v1/illust/detail")
    assert a1 == a2
    b = pick_cf_api_proxy_base_url(cfg, host="oauth.secure.pixiv.net", path="/auth/token")
    assert b in cfg.base_urls


def test_cf_api_proxy_headers_inject_secret() -> None:
    cfg = CfApiProxyConfig(enabled=True, base_urls=["https://edge.example.com"], secret="abc")
    headers = cf_api_proxy_headers(cfg, extra={"Authorization": "Bearer x"})
    assert headers["X-Proxy-Secret"] == "abc"
    assert headers["Authorization"] == "Bearer x"


def test_resolve_pixiv_api_request_from_settings() -> None:
    settings = load_settings(
        {
            "APP_ENV": "dev",
            "SECRET_KEY": "x",
            "CF_API_PROXY_ENABLED": "1",
            "CF_API_PROXY_BASE_URLS": "https://edge.example.com",
            "CF_API_PROXY_SECRET": "sec",
        }
    )
    url, headers, used = resolve_pixiv_api_request(
        settings=settings,
        url="https://oauth.secure.pixiv.net/auth/token",
    )
    assert used is True
    assert url == "https://edge.example.com/p/oauth.secure.pixiv.net/auth/token"
    assert headers["X-Proxy-Secret"] == "sec"


def test_resolve_pixiv_api_request_disabled() -> None:
    settings = load_settings({"APP_ENV": "dev", "SECRET_KEY": "x"})
    raw = "https://app-api.pixiv.net/v1/illust/detail"
    url, headers, used = resolve_pixiv_api_request(settings=settings, url=raw)
    assert used is False
    assert url == raw
    assert headers == {}


def test_settings_disables_flag_without_bases() -> None:
    settings = load_settings(
        {
            "APP_ENV": "dev",
            "SECRET_KEY": "x",
            "CF_API_PROXY_ENABLED": "1",
            "CF_API_PROXY_BASE_URLS": "",
        }
    )
    assert isinstance(settings, Settings)
    assert settings.cf_api_proxy_enabled is False
