"""Offline unit coverage for scripts/edge/probe-img-edge.py pure helpers.

No network — locks sign_url against frozen Worker vectors and summarize_matrix
mode suggestions used by multi-region POC matrix.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
from types import ModuleType


def _load_probe() -> ModuleType:
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts" / "edge" / "probe-img-edge.py"
    spec = importlib.util.spec_from_file_location("probe_img_edge_script", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def test_probe_sign_url_matches_frozen_worker_vectors() -> None:
    probe = _load_probe()
    root = Path(__file__).resolve().parents[2]
    payload = json.loads((root / "edge" / "img-worker" / "test" / "sign_vectors.json").read_text(encoding="utf-8"))
    secret = str(payload["secret"])
    base = "https://img.example.com"
    for vec in payload["vectors"]:
        path = str(vec["path"])
        exp = int(vec["exp"])
        expect_sig = str(vec["sig"])
        # reverse ttl so now + ttl == exp (ttl must stay >= 60 for probe clamp)
        ttl = 3600
        now = exp - ttl
        url = probe.sign_url(base=base, secret=secret, path=path, ttl=ttl, now=now)
        assert url.startswith(f"{base}/u/{exp}/{expect_sig}/")
        b64path = url.rsplit("/", 1)[-1]
        decoded = base64.urlsafe_b64decode(b64path + "=" * (-len(b64path) % 4)).decode("utf-8")
        assert decoded == path
        raw = hmac.new(secret.encode("utf-8"), f"{exp}\n{path}".encode("utf-8"), hashlib.sha256).digest()
        assert _b64url(raw) == expect_sig


def test_probe_sign_url_clamps_ttl_minimum_60() -> None:
    probe = _load_probe()
    url = probe.sign_url(
        base="https://edge.example/",
        secret="s",
        path="/img-original/img/x.jpg",
        ttl=1,
        now=1_700_000_000,
    )
    # ttl=1 → max(60,1)=60 → exp = now+60
    assert "/u/1700000060/" in url
    assert url.startswith("https://edge.example/u/")


def test_probe_summarize_matrix_suggests_b_when_cache_hits() -> None:
    probe = _load_probe()
    rows = [
        {
            "pass": True,
            "probe_1": {"status": 200, "x_edge_via": "i.pximg.net", "elapsed_ms": 120.0},
            "probe_2": {"status": 200, "x_edge_cache": "HIT", "x_edge_via": "i.pximg.net", "elapsed_ms": 15.0},
        },
        {
            "pass": True,
            "probe_1": {"status": 200, "x_edge_via": "i.pximg.net", "elapsed_ms": 90.0},
            "probe_2": {"status": 200, "x_edge_cache": "HIT", "x_edge_via": "i.pximg.net", "elapsed_ms": 12.0},
        },
    ]
    summary = probe.summarize_matrix(rows)
    assert summary["bases_total"] == 2
    assert summary["bases_ok"] == 2
    assert summary["cache_hit_on_second"] == 2
    assert summary["circuit_open_count"] == 0
    assert "B" in summary["mode_suggestion"]
    assert summary["latency_ms_min"] == 12.0
    assert summary["latency_ms_max"] == 120.0
    assert isinstance(summary["decision_checklist"], list)
    assert len(summary["decision_checklist"]) >= 3


def test_probe_summarize_matrix_suggests_b2_on_circuit_open() -> None:
    probe = _load_probe()
    rows = [
        {
            "pass": True,
            "probe_1": {"status": 200, "x_edge_circuit": "origin-open", "x_edge_via": "i.pixiv.cat", "elapsed_ms": 50.0},
            "probe_2": {},
        },
        {
            "pass": False,
            "probe_1": {"status": 403, "x_edge_circuit": "origin-open", "elapsed_ms": 40.0},
            "probe_2": {},
        },
    ]
    summary = probe.summarize_matrix(rows)
    assert summary["circuit_open_count"] == 2
    assert "B2" in summary["mode_suggestion"] or "r2_only" in summary["mode_suggestion"]


def test_probe_summarize_matrix_suggests_r2_when_storage_present() -> None:
    probe = _load_probe()
    rows = [
        {
            "pass": True,
            "probe_1": {"status": 200, "x_edge_storage": "r2", "x_edge_via": "r2", "elapsed_ms": 30.0},
            "probe_2": {"status": 200, "x_edge_cache": "HIT", "x_edge_storage": "r2", "elapsed_ms": 5.0},
        },
    ]
    summary = probe.summarize_matrix(rows)
    assert summary["storage_counts"].get("r2") == 1
    assert "R2" in summary["mode_suggestion"] or "read_through" in summary["mode_suggestion"]


def test_probe_split_bases() -> None:
    probe = _load_probe()
    assert probe._split_bases("") == []
    assert probe._split_bases(None) == []
    assert probe._split_bases(" https://a.example.com/,https://b.example.com ") == [
        "https://a.example.com",
        "https://b.example.com",
    ]


def test_probe_summarize_matrix_ready_gate_requires_signed_pass() -> None:
    probe = _load_probe()
    rows = [
        {"pass": True, "probe_1": {"status": 200, "elapsed_ms": 10.0}, "probe_2": {}},
        {"pass": False, "probe_1": {"status": 403, "elapsed_ms": 8.0}, "probe_2": {}},
    ]
    summary = probe.summarize_matrix(rows)
    assert summary["all_signed_ok"] is False
    assert summary["ready_for_image_edge_flag"] is False


def test_probe_summarize_matrix_ready_gate_healthz_fail() -> None:
    """When --healthz is used, dead healthz must block IMAGE_EDGE_ENABLED cutover."""
    probe = _load_probe()
    rows = [
        {
            "pass": True,
            "healthz": {"ok": True, "status": 200},
            "probe_1": {"status": 200, "elapsed_ms": 10.0},
            "probe_2": {},
        },
        {
            "pass": True,
            "healthz": {"ok": False, "status": 503},
            "probe_1": {"status": 200, "elapsed_ms": 12.0},
            "probe_2": {},
        },
    ]
    summary = probe.summarize_matrix(rows)
    assert summary["all_signed_ok"] is True
    assert summary["healthz_ok"] == 1
    assert summary["ready_for_image_edge_flag"] is False
