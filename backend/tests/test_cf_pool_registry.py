from __future__ import annotations

from app.core.cf_pool_registry import (
    merge_base_url_lists,
    normalize_cf_base_url,
    parse_base_urls_payload,
    pool_members_from_bases,
    register_base_url,
    unregister_base_url,
)


def test_normalize_cf_base_url() -> None:
    assert normalize_cf_base_url("https://a.example.com/") == "https://a.example.com"
    assert normalize_cf_base_url("https://a.example.com/path") == "https://a.example.com"
    assert normalize_cf_base_url("worker-name.acct.workers.dev") == "https://worker-name.acct.workers.dev"
    assert normalize_cf_base_url("") is None
    assert normalize_cf_base_url("ftp://x") is None


def test_merge_and_register() -> None:
    a = merge_base_url_lists(["https://a.example/"], ["https://b.example", "https://a.example"])
    assert a == ["https://a.example", "https://b.example"]
    b = register_base_url(a, "https://c.example/")
    assert b == ["https://a.example", "https://b.example", "https://c.example"]
    c = unregister_base_url(b, "https://b.example/")
    assert c == ["https://a.example", "https://c.example"]


def test_parse_base_urls_payload() -> None:
    assert parse_base_urls_payload(["https://a.example", "https://b.example/"]) == [
        "https://a.example",
        "https://b.example",
    ]
    assert parse_base_urls_payload("https://a.example,https://b.example") == [
        "https://a.example",
        "https://b.example",
    ]


def test_pool_members_sources() -> None:
    members = pool_members_from_bases(
        kind="api",
        env_bases=["https://env.example"],
        runtime_bases=["https://env.example", "https://rt.example"],
    )
    by_url = {m.base_url: m.source for m in members}
    assert by_url["https://env.example"] == "env+runtime"
    assert by_url["https://rt.example"] == "runtime"
