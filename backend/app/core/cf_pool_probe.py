from __future__ import annotations

import time
from typing import Any

import httpx

from app.core.cf_api_proxy import record_cf_base_outcome
from app.core.cf_pool_registry import merge_base_url_lists, normalize_cf_base_url
from app.core.image_edge import record_image_edge_base_outcome


async def probe_cf_worker_base(
    client: httpx.AsyncClient,
    *,
    kind: str,
    base_url: str,
    timeout_s: float = 3.0,
) -> dict[str, Any]:
    """GET ``{base}/healthz``; record process-local cooldown on hard failure.

    Never raises; returns a JSON-safe result dict (no secrets).
    """
    kind_l = (kind or "").strip().lower() or "api"
    base = normalize_cf_base_url(base_url) or ""
    out: dict[str, Any] = {
        "kind": kind_l if kind_l in {"api", "image"} else "api",
        "base_url": base,
        "ok": False,
        "status_code": None,
        "latency_ms": None,
        "error": None,
        "service": None,
        "secret_configured": None,
        "body_ok": None,
    }
    if not base:
        out["error"] = "empty_base_url"
        return out

    url = f"{base}/healthz"
    start = time.monotonic()
    try:
        resp = await client.get(url, timeout=float(timeout_s))
        latency_ms = (time.monotonic() - start) * 1000.0
        out["status_code"] = int(resp.status_code)
        out["latency_ms"] = round(latency_ms, 2)
        if int(resp.status_code) != 200:
            out["error"] = f"status={int(resp.status_code)}"
            _record_outcome(kind_l, base, ok=False)
            return out
        try:
            data = resp.json()
        except Exception:
            out["error"] = "non_json"
            _record_outcome(kind_l, base, ok=False)
            return out
        if not isinstance(data, dict):
            out["error"] = "invalid_json"
            _record_outcome(kind_l, base, ok=False)
            return out
        body_ok = bool(data.get("ok"))
        out["body_ok"] = body_ok
        out["service"] = data.get("service")
        if "secret_configured" in data:
            out["secret_configured"] = bool(data.get("secret_configured"))
        # api-worker healthz can be ok:true with empty PROXY_SECRET (fail-closed only on /p/*).
        # Treat missing secret as not cutover-ready; do not clear cooldown as success.
        if kind_l == "api" and out["secret_configured"] is False:
            out["ok"] = False
            out["error"] = "secret_not_configured"
            # Config gap, not egress failure — skip success/fail cooldown mutation.
            return out
        if body_ok:
            out["ok"] = True
            _record_outcome(kind_l, base, ok=True)
        else:
            out["error"] = "body_not_ok"
            _record_outcome(kind_l, base, ok=False)
        return out
    except Exception as exc:
        latency_ms = (time.monotonic() - start) * 1000.0
        out["latency_ms"] = round(latency_ms, 2)
        out["error"] = type(exc).__name__
        _record_outcome(kind_l, base, ok=False)
        return out


def _record_outcome(kind: str, base: str, *, ok: bool) -> None:
    try:
        if kind == "image":
            record_image_edge_base_outcome(base, ok=ok)
        else:
            record_cf_base_outcome(base, ok=ok)
    except Exception:
        return


async def probe_cf_pool_bases(
    client: httpx.AsyncClient,
    *,
    kind: str,
    bases: list[str],
    timeout_s: float = 3.0,
) -> list[dict[str, Any]]:
    """Probe each base; preserves input order after normalize/dedupe."""
    ordered = merge_base_url_lists(bases)
    results: list[dict[str, Any]] = []
    for base in ordered:
        results.append(
            await probe_cf_worker_base(
                client,
                kind=kind,
                base_url=base,
                timeout_s=timeout_s,
            )
        )
    return results
