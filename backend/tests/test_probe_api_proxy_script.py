"""Offline unit coverage for scripts/edge/probe-api-proxy.py pure helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_probe() -> ModuleType:
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts" / "edge" / "probe-api-proxy.py"
    spec = importlib.util.spec_from_file_location("probe_api_proxy_script", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_build_proxy_url_joins_base_and_path() -> None:
    probe = _load_probe()
    assert (
        probe.build_proxy_url(
            base="https://api-edge.example.com/",
            proxy_path="/p/app-api.pixiv.net/v1/illust/detail?illust_id=1",
        )
        == "https://api-edge.example.com/p/app-api.pixiv.net/v1/illust/detail?illust_id=1"
    )


def test_build_proxy_url_rejects_non_p_prefix() -> None:
    probe = _load_probe()
    try:
        probe.build_proxy_url(base="https://x", proxy_path="/healthz")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_summarize_matrix_ready_when_all_healthz() -> None:
    probe = _load_probe()
    rows = [
        {"healthz": {"service_ok": True}, "proxy": {"pass": True, "elapsed_ms": 40.0}},
        {"healthz": {"service_ok": True}, "proxy": {"pass": True, "elapsed_ms": 80.0}},
    ]
    summary = probe.summarize_matrix(rows)
    assert summary["bases"] == 2
    assert summary["all_healthz_ok"] is True
    assert summary["ready_for_cf_api_proxy_flag"] is True
    assert summary["proxy_p50_ms"] == 80.0


def test_summarize_matrix_not_ready_on_health_fail() -> None:
    probe = _load_probe()
    rows = [
        {"healthz": {"service_ok": True}},
        {"healthz": {"service_ok": False}},
    ]
    summary = probe.summarize_matrix(rows)
    assert summary["all_healthz_ok"] is False
    assert summary["ready_for_cf_api_proxy_flag"] is False


def test_summarize_matrix_not_ready_when_secret_not_configured() -> None:
    """healthz ok:true with empty PROXY_SECRET must block CF_API_PROXY_ENABLED cutover."""
    probe = _load_probe()
    rows = [
        {
            "healthz": {"service_ok": True, "secret_configured": False},
            "proxy": {"pass": False},
        },
        {
            "healthz": {"service_ok": True, "secret_configured": True},
            "proxy": {"pass": True, "elapsed_ms": 12.0},
        },
    ]
    summary = probe.summarize_matrix(rows)
    assert summary["all_healthz_ok"] is True
    assert summary["secret_configured_ok"] == 1
    assert summary["ready_for_cf_api_proxy_flag"] is False
