#!/usr/bin/env python3
"""Probe Cloudflare API egress Worker (edge/api-worker).

Health only:
  python scripts/edge/probe-api-proxy.py --base-url https://api-edge.example.com --healthz

Gate + allowlist smoke (does not require a live Pixiv token; expects 4xx from upstream
or 401/403 from Worker when secret mismatches):
  python scripts/edge/probe-api-proxy.py \\
    --base-url https://api-edge.example.com \\
    --secret "$CF_API_PROXY_SECRET" \\
    --healthz --proxy-path

Multi-base matrix:
  python scripts/edge/probe-api-proxy.py \\
    --bases https://a.example,https://b.example \\
    --secret "$CF_API_PROXY_SECRET" \\
    --healthz --proxy-path --out api-proxy-matrix.json

Never flips production flags; ops decision only.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any


DEFAULT_PROXY_PATH = "/p/app-api.pixiv.net/v1/illust/detail?illust_id=1&filter=for_android"
SERVICE_NAME = "random-image-api-proxy"


def build_proxy_url(*, base: str, proxy_path: str) -> str:
    """Join worker base with /p/{host}/{path}?query (proxy_path must start with /p/)."""
    b = (base or "").rstrip("/")
    p = (proxy_path or "").strip()
    if not p.startswith("/"):
        p = "/" + p
    if not p.startswith("/p/"):
        raise ValueError("proxy_path must start with /p/{host}/...")
    return f"{b}{p}"


def fetch(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    req = urllib.request.Request(url, method=method, headers=headers or {})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(512)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            return {
                "ok": True,
                "status": resp.status,
                "elapsed_ms": round(elapsed_ms, 1),
                "x_proxy_host": hdrs.get("x-proxy-host"),
                "cache_control": hdrs.get("cache-control"),
                "content_type": hdrs.get("content-type"),
                "body_prefix_len": len(body),
                "body_prefix": body[:120].decode("utf-8", errors="replace"),
            }
    except urllib.error.HTTPError as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        hdrs = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
        try:
            err_body = e.read(300).decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        return {
            "ok": False,
            "status": e.code,
            "elapsed_ms": round(elapsed_ms, 1),
            "x_proxy_host": hdrs.get("x-proxy-host"),
            "cache_control": hdrs.get("cache-control"),
            "error_body": err_body,
        }
    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {"ok": False, "status": 0, "elapsed_ms": round(elapsed_ms, 1), "error": str(e)}


def probe_healthz(base: str, *, timeout: float = 15.0) -> dict[str, Any]:
    url = f"{base.rstrip('/')}/healthz"
    result = fetch(url, method="GET", timeout=timeout)
    result["url"] = url
    if result.get("ok") and result.get("status") == 200:
        try:
            payload = json.loads(result.get("body_prefix") or "{}")
            result["service"] = payload.get("service")
            result["secret_required"] = payload.get("secret_required")
            result["allowed_hosts"] = payload.get("allowed_hosts")
            # Worker may report ok:true with empty PROXY_SECRET; surface for cutover readiness.
            if "secret_configured" in payload:
                result["secret_configured"] = bool(payload.get("secret_configured"))
            else:
                result["secret_configured"] = None
            result["service_ok"] = payload.get("ok") is True and str(
                payload.get("service") or ""
            ) == SERVICE_NAME
        except Exception:
            result["service_ok"] = False
            result["secret_configured"] = None
    else:
        result["service_ok"] = False
        result["secret_configured"] = None
    return result


def probe_proxy(
    *,
    base: str,
    secret: str | None,
    proxy_path: str,
    timeout: float = 20.0,
) -> dict[str, Any]:
    url = build_proxy_url(base=base, proxy_path=proxy_path)
    headers: dict[str, str] = {}
    if secret:
        headers["X-Proxy-Secret"] = secret
    result = fetch(url, method="GET", headers=headers, timeout=timeout)
    result["url"] = url
    # Worker gate: 401/403 = secret wrong/missing. Upstream 4xx/5xx still proves allowlist + egress.
    status = int(result.get("status") or 0)
    result["gate_rejected"] = status in {401, 403}
    result["reached_upstream"] = status not in {0, 401, 403, 404} or (
        status == 404 and bool(result.get("x_proxy_host"))
    )
    # 404 from worker path parse vs upstream is ambiguous; prefer x-proxy-host when present.
    result["pass"] = status != 0 and not (
        status == 401 and secret
    )  # with secret, 401 is hard fail
    if secret and status in {401, 403}:
        result["pass"] = False
    if not secret and status in {401, 403}:
        # Expected when Worker requires secret and we omitted it.
        result["pass"] = True
        result["expected_gate"] = True
    if status in {200, 400, 404, 429, 500, 502, 503} and result.get("x_proxy_host"):
        result["pass"] = True
    return result


def summarize_matrix(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    health_ok = sum(1 for r in rows if (r.get("healthz") or {}).get("service_ok"))
    # secret_configured=false means Worker will 403 /p/* even when healthz service_ok.
    secret_ok = sum(
        1
        for r in rows
        if (r.get("healthz") or {}).get("service_ok")
        and (r.get("healthz") or {}).get("secret_configured") is not False
    )
    proxy_pass = sum(1 for r in rows if (r.get("proxy") or {}).get("pass"))
    latencies = [
        float((r.get("proxy") or {}).get("elapsed_ms") or 0)
        for r in rows
        if (r.get("proxy") or {}).get("elapsed_ms") is not None
    ]
    ready = health_ok == total and total > 0 and secret_ok == total
    return {
        "bases": total,
        "healthz_ok": health_ok,
        "secret_configured_ok": secret_ok,
        "proxy_pass": proxy_pass,
        "all_healthz_ok": health_ok == total and total > 0,
        "proxy_p50_ms": sorted(latencies)[len(latencies) // 2] if latencies else None,
        "ready_for_cf_api_proxy_flag": ready,
        "note": (
            "Set CF_API_PROXY_ENABLED only after healthz_ok + secret_configured on all bases "
            "(and proxy path pass when --secret provided)."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Probe random-image-api-proxy Worker")
    p.add_argument("--base-url", default="", help="Single worker base URL")
    p.add_argument("--bases", default="", help="CSV of bases (matrix mode)")
    p.add_argument("--secret", default="", help="X-Proxy-Secret / CF_API_PROXY_SECRET")
    p.add_argument("--healthz", action="store_true", help="Probe GET /healthz")
    p.add_argument(
        "--proxy-path",
        nargs="?",
        const=DEFAULT_PROXY_PATH,
        default="",
        help=f"Probe allowlisted /p/... path (default: {DEFAULT_PROXY_PATH})",
    )
    p.add_argument("--timeout", type=float, default=20.0)
    p.add_argument("--out", default="", help="Write JSON report path")
    args = p.parse_args(argv)

    bases: list[str] = []
    if args.bases.strip():
        bases = [b.strip() for b in args.bases.split(",") if b.strip()]
    elif args.base_url.strip():
        bases = [args.base_url.strip()]
    if not bases:
        print("error: provide --base-url or --bases", file=sys.stderr)
        return 2
    if not args.healthz and not args.proxy_path:
        # Default to healthz when nothing specified.
        args.healthz = True

    rows: list[dict[str, Any]] = []
    for base in bases:
        row: dict[str, Any] = {"base": base}
        if args.healthz:
            row["healthz"] = probe_healthz(base, timeout=float(args.timeout))
        if args.proxy_path:
            try:
                row["proxy"] = probe_proxy(
                    base=base,
                    secret=(args.secret or None),
                    proxy_path=str(args.proxy_path),
                    timeout=float(args.timeout),
                )
            except ValueError as exc:
                row["proxy"] = {"ok": False, "pass": False, "error": str(exc)}
        rows.append(row)

    report: dict[str, Any] = {
        "service": SERVICE_NAME,
        "rows": rows if len(rows) > 1 else rows[0],
    }
    if len(rows) > 1:
        report["summary"] = summarize_matrix(rows)

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")

    # Exit 0 if all requested probes pass (healthz + secret_configured + proxy).
    ok = True
    for row in rows:
        hz = row.get("healthz")
        if hz is not None:
            if not hz.get("service_ok"):
                ok = False
            # Explicit false = Worker empty PROXY_SECRET (not cutover-ready).
            if hz.get("secret_configured") is False:
                ok = False
        pr = row.get("proxy")
        if pr is not None and not pr.get("pass"):
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
