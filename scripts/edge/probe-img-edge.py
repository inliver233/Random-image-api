#!/usr/bin/env python3
"""Sign a known pximg path and probe image-edge Worker(s).

Single base:
  python scripts/edge/probe-img-edge.py \\
    --base-url https://random-image-edge.example.workers.dev \\
    --secret "$IMAGE_EDGE_SECRET" \\
    --path /img-original/img/2020/01/01/00/00/00/12345_p0.jpg \\
    --twice --healthz

Multi-region matrix (Phase 0 POC):
  python scripts/edge/probe-img-edge.py \\
    --bases https://edge-a.example.com,https://edge-b.example.com \\
    --secret "$IMAGE_EDGE_SECRET" \\
    --path /img-original/img/.../x_p0.jpg \\
    --twice --healthz \\
    --out edge-matrix.json

Records HTTP status, latency, X-Edge-Cache / Via / Storage / Circuit.
Does not require the backend process — pure HMAC + HTTP.
Never flips production flags; ops decision only.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign_url(*, base: str, secret: str, path: str, ttl: int, now: int | None = None) -> str:
    ts = int(now if now is not None else time.time())
    exp = ts + max(60, int(ttl))
    msg = f"{exp}\n{path}".encode("utf-8")
    sig = b64url(hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).digest())
    b64path = b64url(path.encode("utf-8"))
    return f"{base.rstrip('/')}/u/{exp}/{sig}/{b64path}"


def fetch(url: str, *, method: str = "GET", timeout: float = 30.0) -> dict[str, Any]:
    req = urllib.request.Request(url, method=method)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(64) if method == "GET" else b""
            elapsed_ms = (time.perf_counter() - t0) * 1000
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return {
                "ok": True,
                "status": resp.status,
                "elapsed_ms": round(elapsed_ms, 1),
                "x_edge_cache": headers.get("x-edge-cache"),
                "x_edge_via": headers.get("x-edge-via"),
                "x_edge_storage": headers.get("x-edge-storage"),
                "x_edge_circuit": headers.get("x-edge-circuit"),
                "content_type": headers.get("content-type"),
                "content_length": headers.get("content-length"),
                "body_prefix_len": len(body),
            }
    except urllib.error.HTTPError as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        headers = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
        try:
            err_body = e.read(200).decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        return {
            "ok": False,
            "status": e.code,
            "elapsed_ms": round(elapsed_ms, 1),
            "x_edge_cache": headers.get("x-edge-cache"),
            "x_edge_via": headers.get("x-edge-via"),
            "x_edge_storage": headers.get("x-edge-storage"),
            "x_edge_circuit": headers.get("x-edge-circuit"),
            "error_body": err_body,
        }
    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {"ok": False, "status": 0, "elapsed_ms": round(elapsed_ms, 1), "error": str(e)}


def probe_one(
    *,
    base: str,
    secret: str,
    path: str,
    ttl: int,
    method: str,
    twice: bool,
    healthz: bool,
) -> dict[str, Any]:
    base = base.rstrip("/")
    report: dict[str, Any] = {"base_url": base, "path": path}

    if healthz:
        report["healthz"] = fetch(f"{base}/healthz", method="GET")

    url = sign_url(base=base, secret=secret, path=path, ttl=ttl)
    report["signed_url_preview"] = url[:80] + "…" if len(url) > 80 else url
    report["probe_1"] = fetch(url, method=method)
    if twice:
        report["probe_2"] = fetch(url, method=method)

    s1 = report["probe_1"].get("status")
    report["pass"] = s1 == 200
    return report


def _healthz_service_ok(row: dict[str, Any]) -> bool | None:
    """True/False when healthz was probed; None when --healthz was not used."""
    hz = row.get("healthz")
    if hz is None:
        return None
    if not isinstance(hz, dict):
        return False
    # Prefer explicit body.ok when present; else HTTP 200.
    if "ok" in hz and isinstance(hz.get("ok"), bool):
        return bool(hz["ok"]) and int(hz.get("status") or 0) == 200
    # fetch() shape: ok + status
    return bool(hz.get("ok")) and int(hz.get("status") or 0) == 200


def summarize_matrix(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Ops-facing summary for multi-region B / B+R2 / B2 decision."""
    bases_ok = 0
    cache_hit_on_second = 0
    via_counts: dict[str, int] = {}
    storage_counts: dict[str, int] = {}
    circuit_open = 0
    latencies: list[float] = []
    healthz_probed = 0
    healthz_ok = 0

    for row in rows:
        if row.get("pass"):
            bases_ok += 1
        hz_ok = _healthz_service_ok(row)
        if hz_ok is not None:
            healthz_probed += 1
            if hz_ok:
                healthz_ok += 1
        p1 = row.get("probe_1") or {}
        p2 = row.get("probe_2") or {}
        if p2.get("x_edge_cache") == "HIT":
            cache_hit_on_second += 1
        via = p1.get("x_edge_via") or p2.get("x_edge_via")
        if via:
            via_counts[str(via)] = via_counts.get(str(via), 0) + 1
        storage = p1.get("x_edge_storage") or p2.get("x_edge_storage")
        if storage:
            storage_counts[str(storage)] = storage_counts.get(str(storage), 0) + 1
        if (p1.get("x_edge_circuit") or p2.get("x_edge_circuit")) == "origin-open":
            circuit_open += 1
        for probe in (p1, p2):
            ms = probe.get("elapsed_ms")
            if isinstance(ms, (int, float)) and ms > 0:
                latencies.append(float(ms))

    n = max(1, len(rows))
    suggestion = "B"
    if circuit_open >= max(1, n // 2):
        suggestion = "B2 (r2_only + prewarm) or keep B with mirror chain"
    elif storage_counts:
        suggestion = "B+R2 (read_through) viable"
    elif bases_ok == len(rows) and cache_hit_on_second > 0:
        suggestion = "B (Cache API only) ready for sticky multi-base"

    # Cutover-ready: all signed path probes pass; when healthz was requested, all must be ok.
    all_pass = bases_ok == len(rows) and len(rows) > 0
    healthz_ready = healthz_probed == 0 or (healthz_ok == healthz_probed and healthz_probed == len(rows))
    ready = all_pass and healthz_ready

    return {
        "bases_total": len(rows),
        "bases_ok": bases_ok,
        "healthz_probed": healthz_probed,
        "healthz_ok": healthz_ok,
        "all_signed_ok": all_pass,
        "ready_for_image_edge_flag": ready,
        "cache_hit_on_second": cache_hit_on_second,
        "via_counts": via_counts,
        "storage_counts": storage_counts,
        "circuit_open_count": circuit_open,
        "latency_ms_min": round(min(latencies), 1) if latencies else None,
        "latency_ms_max": round(max(latencies), 1) if latencies else None,
        "latency_ms_avg": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "mode_suggestion": suggestion,
        "note": (
            "Set IMAGE_EDGE_ENABLED only after all bases pass signed path "
            "(and healthz when --healthz). This script never flips flags."
        ),
        "decision_checklist": [
            "All bases return 200 on known good path?",
            "healthz ok on every base when --healthz used?",
            "Second request shows X-Edge-Cache: HIT on most bases?",
            "X-Edge-Via mostly origin (not emergency mirrors)?",
            "If origin 403 / circuit-open high → enable R2 read_through or r2_only + prewarm",
            "Only then set IMAGE_EDGE_ENABLED=true on BFF (ops flip; not this script)",
        ],
    }


def _split_bases(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [b.strip().rstrip("/") for b in str(raw).split(",") if b.strip()]


def main() -> int:
    p = argparse.ArgumentParser(description="Probe CF image edge with signed URL")
    p.add_argument("--base-url", default="", help="Single Worker base URL (no trailing path)")
    p.add_argument(
        "--bases",
        default="",
        help="CSV of Worker bases for multi-region matrix (overrides --base-url when set)",
    )
    p.add_argument("--secret", required=True, help="IMAGE_EDGE_SECRET")
    p.add_argument(
        "--path",
        default="/img-original/img/2020/01/01/00/00/00/12345_p0.jpg",
        help="pximg path starting with /",
    )
    p.add_argument("--ttl", type=int, default=3600, help="URL TTL seconds")
    p.add_argument("--method", choices=("GET", "HEAD"), default="GET")
    p.add_argument("--twice", action="store_true", help="Request twice to observe Cache HIT")
    p.add_argument("--healthz", action="store_true", help="Also GET /healthz")
    p.add_argument("--out", default="", help="Write full JSON report to this path")
    args = p.parse_args()

    bases = _split_bases(args.bases) or _split_bases(args.base_url)
    if not bases:
        p.error("provide --base-url or --bases")

    rows = [
        probe_one(
            base=b,
            secret=args.secret,
            path=args.path,
            ttl=args.ttl,
            method=args.method,
            twice=bool(args.twice),
            healthz=bool(args.healthz),
        )
        for b in bases
    ]

    if len(rows) == 1:
        report: dict[str, Any] = rows[0]
    else:
        report = {
            "path": args.path,
            "method": args.method,
            "bases": [r["base_url"] for r in rows],
            "results": rows,
            "summary": summarize_matrix(rows),
        }

    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
            f.write("\n")

    # Exit 0 only when cutover-ready: signed path pass on all bases; healthz ok if probed.
    ok = True
    for row in rows:
        if not row.get("pass"):
            ok = False
        hz_ok = _healthz_service_ok(row)
        if hz_ok is False:
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
