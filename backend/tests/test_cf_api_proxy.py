from __future__ import annotations

import json
from pathlib import Path

from app.core.cf_api_proxy import (
    DEFAULT_CF_API_PROXY_HOSTS,
    CfApiProxyConfig,
    cf_api_proxy_headers,
    cf_proxy_base_from_request_url,
    is_cf_api_proxy_host_allowed,
    is_cf_base_cooling,
    load_cf_api_proxy_config,
    load_cf_api_proxy_config_from_settings,
    order_cf_bases_for_failover,
    pick_cf_api_proxy_base_url,
    record_cf_base_outcome,
    reset_cf_base_cooldown_for_tests,
    resolve_pixiv_api_cf_candidates,
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


def test_load_cf_api_proxy_config_not_ready_without_secret() -> None:
    cfg = load_cf_api_proxy_config(
        {
            "CF_API_PROXY_ENABLED": "true",
            "CF_API_PROXY_BASE_URLS": "https://api-a.example.com",
            "CF_API_PROXY_SECRET": "",
        }
    )
    assert cfg is not None
    assert cfg.ready is False


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


def test_resolve_pixiv_api_request_requires_secret() -> None:
    settings = load_settings(
        {
            "APP_ENV": "dev",
            "SECRET_KEY": "x",
            "CF_API_PROXY_ENABLED": "1",
            "CF_API_PROXY_BASE_URLS": "https://edge.example.com",
            "CF_API_PROXY_SECRET": "",
        }
    )
    raw = "https://app-api.pixiv.net/v1/illust/detail"
    url, headers, used = resolve_pixiv_api_request(settings=settings, url=raw)
    assert used is False
    assert url == raw
    assert headers == {}
    assert resolve_pixiv_api_cf_candidates(settings=settings, url=raw) == []


def test_resolve_pixiv_api_cf_candidates_orders_sticky_first() -> None:
    reset_cf_base_cooldown_for_tests()
    settings = load_settings(
        {
            "APP_ENV": "dev",
            "SECRET_KEY": "x",
            "CF_API_PROXY_ENABLED": "1",
            "CF_API_PROXY_BASE_URLS": "https://a.example.com,https://b.example.com,https://c.example.com",
            "CF_API_PROXY_SECRET": "sec",
        }
    )
    raw = "https://app-api.pixiv.net/v1/illust/detail"
    candidates = resolve_pixiv_api_cf_candidates(settings=settings, url=raw)
    assert len(candidates) == 3
    sticky, headers, used = resolve_pixiv_api_request(settings=settings, url=raw)
    assert used is True
    assert candidates[0][0] == sticky
    assert all(h.get("X-Proxy-Secret") == "sec" for _, h in candidates)
    bases = [u.split("/p/")[0] for u, _ in candidates]
    assert len(set(bases)) == 3


def test_cf_base_cooldown_demotes_failed_base() -> None:
    reset_cf_base_cooldown_for_tests()
    settings = load_settings(
        {
            "APP_ENV": "dev",
            "SECRET_KEY": "x",
            "CF_API_PROXY_ENABLED": "1",
            "CF_API_PROXY_BASE_URLS": "https://a.example.com,https://b.example.com",
            "CF_API_PROXY_SECRET": "sec",
        }
    )
    raw = "https://app-api.pixiv.net/v1/illust/detail"
    first = resolve_pixiv_api_cf_candidates(settings=settings, url=raw)
    sticky_url = first[0][0]
    sticky_base = cf_proxy_base_from_request_url(sticky_url)
    assert sticky_base is not None
    record_cf_base_outcome(sticky_url, ok=False)
    assert is_cf_base_cooling(sticky_base) is True
    second = resolve_pixiv_api_cf_candidates(settings=settings, url=raw)
    assert len(second) == 2
    # Cooling sticky is still present but not first when another base is hot.
    assert second[0][0] != sticky_url
    assert second[-1][0] == sticky_url
    record_cf_base_outcome(sticky_url, ok=True)
    assert is_cf_base_cooling(sticky_base) is False
    third = resolve_pixiv_api_cf_candidates(settings=settings, url=raw)
    assert third[0][0] == sticky_url


def test_order_cf_bases_for_failover_and_extract() -> None:
    reset_cf_base_cooldown_for_tests()
    assert (
        cf_proxy_base_from_request_url("https://edge.example.com/p/app-api.pixiv.net/v1/x")
        == "https://edge.example.com"
    )
    ordered = order_cf_bases_for_failover(
        ["https://hot.example.com", "https://cold.example.com", "https://hot.example.com"]
    )
    assert ordered == ["https://hot.example.com", "https://cold.example.com"]
    record_cf_base_outcome("https://hot.example.com", ok=False, cooldown_s=60.0)
    demoted = order_cf_bases_for_failover(["https://hot.example.com", "https://cold.example.com"])
    assert demoted[0] == "https://cold.example.com"
    assert demoted[-1] == "https://hot.example.com"


def test_settings_keeps_flag_without_env_bases_for_runtime_overlay() -> None:
    """Env CSV may be empty; runtime pool overlay can supply bases after boot."""
    settings = load_settings(
        {
            "APP_ENV": "dev",
            "SECRET_KEY": "x",
            "CF_API_PROXY_ENABLED": "1",
            "CF_API_PROXY_BASE_URLS": "",
            "CF_API_PROXY_SECRET": "sec",
        }
    )
    assert isinstance(settings, Settings)
    assert settings.cf_api_proxy_enabled is True
    # No env bases and empty overlay → not ready yet.
    assert load_cf_api_proxy_config_from_settings(settings) is None


def test_cf_api_proxy_matches_frozen_proxy_vectors() -> None:
    """Parity with edge/api-worker/test/proxy_vectors.json (Worker parse + allowlist)."""
    vectors_path = (
        Path(__file__).resolve().parents[2] / "edge" / "api-worker" / "test" / "proxy_vectors.json"
    )
    vectors = json.loads(vectors_path.read_text(encoding="utf-8"))
    assert set(vectors["default_hosts"]) == set(DEFAULT_CF_API_PROXY_HOSTS)

    cfg = CfApiProxyConfig(enabled=True, base_urls=["https://edge.example.com"], secret="")
    base = "https://edge.example.com"

    for row in vectors["parse_ok"]:
        host = row["host"]
        path_with_query = row["pathWithQuery"]
        if "?" in path_with_query:
            path, q = path_with_query.split("?", 1)
            raw = f"https://{host}{path}?{q}"
        else:
            path = path_with_query
            raw = f"https://{host}{path}"
        out = rewrite_url_via_cf_api_proxy(cfg, raw)
        assert out == f"{base}/p/{host}{path_with_query}", row

    for host in vectors["host_reject_in_allowlist_parse"]:
        assert is_cf_api_proxy_host_allowed(host) is False, host
        assert rewrite_url_via_cf_api_proxy(cfg, f"https://{host}/v1/x") is None
