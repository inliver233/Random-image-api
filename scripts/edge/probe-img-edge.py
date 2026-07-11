#!/usr/bin/env python3
"""Sign a known pximg path and probe image-edge Worker (status + cache headers).

Usage:
  python scripts/edge/probe-img-edge.py \\
    --base-url https://random-image-edge.example.workers.dev \\
    --secret "$IMAGE_EDGE_SECRET" \\
    --path /img-original/img/2020/01/01/00/00/00/12345_p0.jpg

Records HTTP status, X-Edge-Cache, X-Edge-Via, X-Edge-Storage for multi-region POC.
Does not require the backend process — pure HMAC + HTTP.
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
            "error_body": err_body,
        }
    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {"ok": False, "status": 0, "elapsed_ms": round(elapsed_ms, 1), "error": str(e)}


def main() -> int:
    p = argparse.ArgumentParser(description="Probe CF image edge with signed URL")
    p.add_argument("--base-url", required=True, help="Worker base URL (no trailing path)")
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
    args = p.parse_args()

    base = args.base_url.rstrip("/")
    report: dict[str, Any] = {"base_url": base, "path": args.path}

    if args.healthz:
        report["healthz"] = fetch(f"{base}/healthz", method="GET")

    url = sign_url(base=base, secret=args.secret, path=args.path, ttl=args.ttl)
    report["signed_url_preview"] = url[:80] + "…" if len(url) > 80 else url
    report["probe_1"] = fetch(url, method=args.method)
    if args.twice:
        report["probe_2"] = fetch(url, method=args.method)

    print(json.dumps(report, indent=2, ensure_ascii=False))
    s1 = report["probe_1"].get("status")
    return 0 if s1 == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
