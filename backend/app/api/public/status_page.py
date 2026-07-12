from __future__ import annotations

import json
import math
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.core.cf_api_proxy import load_cf_api_proxy_config_from_settings
from app.core.coerce import clamp_float
from app.core.image_edge import load_image_edge_config_from_settings
from app.core.r2_prewarm import r2_prewarm_enabled, r2_prewarm_secret
from app.core.random_engine_client import engine_circuit_snapshot, random_engine_base_url
from app.core.request_id import get_or_create_request_id, set_request_id_header, set_request_id_on_state
from app.core.time import iso_utc_ms
from app.db.session import with_sqlite_busy_retry

router = APIRouter()


def _as_nonneg_stat(value: Any) -> int:
    """Status-page stats: invalid/None → 0 (display-only, not a range clamp)."""
    try:
        return int(value or 0)
    except Exception:
        return 0


def _random_engine_public_snapshot(settings: Any) -> dict[str, Any]:
    """Local dual-run readiness for public /status (no outbound engine probe)."""
    engine_url = random_engine_base_url(settings) if settings is not None else None
    enabled = bool(getattr(settings, "random_engine_enabled", False)) if settings is not None else False
    try:
        traffic = int(getattr(settings, "random_engine_traffic_percent", 100) or 0) if settings is not None else 0
    except Exception:
        traffic = 0
    return {
        "url_configured": bool(engine_url),
        "enabled": enabled,
        "traffic_percent": traffic,
        # Process dual-run circuit (same shape as /healthz modules.random_engine.circuit).
        "circuit": engine_circuit_snapshot(),
    }


def _image_edge_public_snapshot(settings: Any) -> dict[str, Any]:
    """Local Image Edge readiness for public /status (config only; no secrets / no edge probe)."""
    enabled_flag = bool(getattr(settings, "image_edge_enabled", False)) if settings is not None else False
    cfg = load_image_edge_config_from_settings(settings) if settings is not None else None
    if cfg is not None:
        base_url_count = len(cfg.base_urls)
    else:
        raw_bases = list(getattr(settings, "image_edge_base_urls", None) or []) if settings is not None else []
        base_url_count = len(raw_bases)
    return {
        "enabled_flag": enabled_flag,
        "ready": cfg is not None,
        "base_url_count": int(base_url_count),
    }


def _cf_api_proxy_public_snapshot(settings: Any) -> dict[str, Any]:
    """Local CF API proxy readiness for public /status (config only; no secrets / no worker probe)."""
    enabled_flag = bool(getattr(settings, "cf_api_proxy_enabled", False)) if settings is not None else False
    cfg = load_cf_api_proxy_config_from_settings(settings) if settings is not None else None
    ready = bool(cfg is not None and cfg.ready)
    if cfg is not None:
        base_url_count = len(cfg.base_urls)
    else:
        raw_bases = list(getattr(settings, "cf_api_proxy_base_urls", None) or []) if settings is not None else []
        base_url_count = len(raw_bases)
    return {
        "enabled_flag": enabled_flag,
        "ready": ready,
        "base_url_count": int(base_url_count),
    }


def _r2_prewarm_public_snapshot(settings: Any) -> dict[str, Any]:
    """Local R2 prewarm readiness for public /status (config only; no secrets / no webhook probe)."""
    enabled_flag = bool(getattr(settings, "r2_prewarm_enabled", False)) if settings is not None else False
    url_configured = (
        bool(str(getattr(settings, "r2_prewarm_url", "") or "").strip()) if settings is not None else False
    )
    # Match /healthz modules.r2_prewarm.ready: flag+url via r2_prewarm_enabled AND secret present.
    ready = bool(r2_prewarm_enabled(settings) and r2_prewarm_secret(settings)) if settings is not None else False
    return {
        "enabled_flag": enabled_flag,
        "ready": ready,
        "url_configured": url_configured,
    }


def _api_key_rate_limit_public_snapshot(request: Request, settings: Any) -> dict[str, Any]:
    """Local API-key rate-limit backend honesty for public /status (no Redis probe / no URL)."""
    requested = (
        str(getattr(settings, "public_api_key_rate_limit_backend", "memory") or "memory").strip().lower()
        if settings is not None
        else "memory"
    )
    if requested not in {"memory", "redis"}:
        requested = "memory"
    redis_url_configured = (
        bool(str(getattr(settings, "redis_url", "") or "").strip()) if settings is not None else False
    )
    limiter = getattr(request.app.state, "api_key_limiter", None)
    backend = str(
        getattr(limiter, "active_backend", None)
        or getattr(limiter, "backend", "memory")
        or "memory"
    ).strip().lower()
    if backend not in {"memory", "redis"}:
        # Minimal harness without limiter → fold config like /healthz.
        backend = requested if (requested != "redis" or redis_url_configured) else "memory"
    return {
        "backend": backend,
        "requested": requested,
        "redis_url_configured": redis_url_configured,
        "required": bool(getattr(settings, "public_api_key_required", False)) if settings is not None else False,
        "using_memory_fallback": requested == "redis" and backend == "memory",
    }


def _job_queue_public_snapshot(request: Request, settings: Any) -> dict[str, Any]:
    """Local job-queue port honesty for public /status (same as /healthz modules.job_queue)."""
    requested = (
        str(getattr(settings, "job_queue_backend", "sqlite") or "sqlite").strip().lower()
        if settings is not None
        else "sqlite"
    )
    if requested not in {"sqlite", "memory"}:
        # Settings rejects redis/nats at boot; normalize any other label for honesty.
        requested = "sqlite"
    job_queue = getattr(request.app.state, "job_queue", None)
    backend = str(getattr(job_queue, "backend", "sqlite") or "sqlite")
    return {
        "backend": backend,
        "requested": requested,
        "implemented": requested in {"sqlite", "memory"},
    }


def _recent_dedup_public_snapshot(request: Request, settings: Any) -> dict[str, Any]:
    """Local recent-dedup port honesty for public /status (same as /healthz modules.recent_dedup)."""
    requested = (
        str(getattr(settings, "recent_dedup_backend", "memory") or "memory").strip().lower()
        if settings is not None
        else "memory"
    )
    if requested not in {"memory", "redis"}:
        requested = "memory"
    recent = getattr(request.app.state, "recent_dedup", None)
    backend = str(
        getattr(recent, "active_backend", None) or getattr(recent, "backend", "memory") or "memory"
    ).strip().lower()
    if backend not in {"memory", "redis"}:
        backend = "memory"
    return {
        "backend": backend,
        "requested": requested,
        "using_memory_fallback": requested == "redis" and backend == "memory",
    }


def _port_backend_public_snapshot(request: Request, attr: str, *, default: str) -> dict[str, Any]:
    """Single-field modular port label for public /status (catalog/tags/random_*)."""
    port = getattr(request.app.state, attr, None)
    backend = str(getattr(port, "backend", default) or default)
    return {"backend": backend}


async def _query_gallery_stats(engine) -> dict[str, Any]:
    async def _op() -> dict[str, Any]:
        async with engine.connect() as conn:
            images_total = int((await conn.exec_driver_sql("SELECT COUNT(*) FROM images;")).scalar_one())
            images_enabled = int((await conn.exec_driver_sql("SELECT COUNT(*) FROM images WHERE status=1;")).scalar_one())
            illust_total = int((await conn.exec_driver_sql("SELECT COUNT(DISTINCT illust_id) FROM images;")).scalar_one())
            authors_total = int(
                (
                    await conn.exec_driver_sql(
                        "SELECT COUNT(DISTINCT user_id) FROM images WHERE user_id IS NOT NULL;"
                    )
                ).scalar_one()
            )

            r18_count = int((await conn.exec_driver_sql("SELECT COUNT(*) FROM images WHERE status=1 AND x_restrict=1;")).scalar_one())
            safe_count = int((await conn.exec_driver_sql("SELECT COUNT(*) FROM images WHERE status=1 AND x_restrict=0;")).scalar_one())
            r18_unknown = int(
                (await conn.exec_driver_sql("SELECT COUNT(*) FROM images WHERE status=1 AND x_restrict IS NULL;")).scalar_one()
            )

            ai_count = int((await conn.exec_driver_sql("SELECT COUNT(*) FROM images WHERE status=1 AND ai_type=1;")).scalar_one())
            non_ai_count = int((await conn.exec_driver_sql("SELECT COUNT(*) FROM images WHERE status=1 AND ai_type=0;")).scalar_one())
            ai_unknown = int(
                (await conn.exec_driver_sql("SELECT COUNT(*) FROM images WHERE status=1 AND ai_type IS NULL;")).scalar_one()
            )

        return {
            "gallery": {
                "images_total": images_total,
                "images_enabled": images_enabled,
                "illust_total": illust_total,
                "authors_total": authors_total,
            },
            "breakdown": {
                "r18": {"r18": r18_count, "safe": safe_count, "unknown": r18_unknown},
                "ai": {"ai": ai_count, "non_ai": non_ai_count, "unknown": ai_unknown},
            },
        }

    return await with_sqlite_busy_retry(_op)


def _build_status_html(
    *,
    base_url: str,
    status_code: int,
    payload: dict[str, Any],
    public_api_key_required: bool = False,
) -> str:
    base = (base_url or "").rstrip("/")
    if not base:
        base = ""

    def u(path: str) -> str:
        path_norm = (path or "").strip()
        if not path_norm.startswith("/"):
            path_norm = "/" + path_norm
        return f"{base}{path_norm}"

    api_status = str(payload.get("api_status") or "unknown")
    api_status_code = int(payload.get("api_status_code") or status_code)
    updated_at = str(payload.get("updated_at") or "")
    public_key_note = (
        "本部署已开启 PUBLIC_API_KEY_REQUIRED：调用 /random 等接口需 X-API-Key 或 ?api_key=（见 /docs）。"
        if public_api_key_required
        else ""
    )

    gallery = payload.get("gallery") if isinstance(payload.get("gallery"), dict) else {}
    images_total = _as_nonneg_stat(gallery.get("images_total"))
    images_enabled = _as_nonneg_stat(gallery.get("images_enabled"))
    illust_total = _as_nonneg_stat(gallery.get("illust_total"))
    authors_total = _as_nonneg_stat(gallery.get("authors_total"))

    random_stats = payload.get("random") if isinstance(payload.get("random"), dict) else {}
    random_total = _as_nonneg_stat(random_stats.get("total_requests"))
    random_in_flight = _as_nonneg_stat(random_stats.get("in_flight"))
    last_window_requests = _as_nonneg_stat(random_stats.get("last_window_requests"))
    last_window_success_rate = float(random_stats.get("last_window_success_rate") or 0.0)
    if not math.isfinite(last_window_success_rate):
        last_window_success_rate = 0.0
    last_window_success_rate = clamp_float(float(last_window_success_rate), min_v=0.0, max_v=1.0)

    # Dual-run readiness chip (local circuit snapshot; no outbound probe).
    eng = payload.get("random_engine") if isinstance(payload.get("random_engine"), dict) else {}
    eng_enabled = bool(eng.get("enabled"))
    eng_url_ok = bool(eng.get("url_configured"))
    eng_traffic = _as_nonneg_stat(eng.get("traffic_percent"))
    eng_circuit = eng.get("circuit") if isinstance(eng.get("circuit"), dict) else {}
    eng_state = str(eng_circuit.get("state") or "closed")
    if eng_enabled and eng_url_ok:
        eng_chip = f"dual-run: on · traffic {eng_traffic}% · circuit {eng_state}"
    elif eng_url_ok:
        eng_chip = f"dual-run: off · circuit {eng_state}"
    else:
        eng_chip = f"dual-run: not configured · circuit {eng_state}"

    # Image Edge readiness chip (config only; same shape as /healthz modules.image_edge).
    # Public JSON omits has_secret; when flag+bases but not ready, secret is the usual gap.
    edge = payload.get("image_edge") if isinstance(payload.get("image_edge"), dict) else {}
    edge_ready = bool(edge.get("ready"))
    edge_flag = bool(edge.get("enabled_flag"))
    edge_bases = _as_nonneg_stat(edge.get("base_url_count"))
    if edge_ready:
        edge_chip = f"image-edge: ready · bases {edge_bases}"
    elif edge_flag and edge_bases > 0:
        edge_chip = f"image-edge: not ready · flag on · bases {edge_bases} · no-secret"
    elif edge_flag:
        edge_chip = f"image-edge: not ready · flag on · bases {edge_bases}"
    else:
        edge_chip = f"image-edge: off · bases {edge_bases}"

    # CF API proxy readiness chip (config only; same shape as /healthz modules.cf_api_proxy).
    # Public JSON omits has_secret; when flag+bases but not ready, secret is the usual gap.
    cf = payload.get("cf_api_proxy") if isinstance(payload.get("cf_api_proxy"), dict) else {}
    cf_ready = bool(cf.get("ready"))
    cf_flag = bool(cf.get("enabled_flag"))
    cf_bases = _as_nonneg_stat(cf.get("base_url_count"))
    if cf_ready:
        cf_chip = f"cf-api: ready · bases {cf_bases}"
    elif cf_flag and cf_bases > 0:
        cf_chip = f"cf-api: not ready · flag on · bases {cf_bases} · no-secret"
    elif cf_flag:
        cf_chip = f"cf-api: not ready · flag on · bases {cf_bases}"
    else:
        cf_chip = f"cf-api: off · bases {cf_bases}"

    # R2 prewarm readiness chip (config only; same as /healthz modules.r2_prewarm without secret values).
    # Public JSON omits secret_configured; when flag+url but not ready, secret is the usual gap.
    r2 = payload.get("r2_prewarm") if isinstance(payload.get("r2_prewarm"), dict) else {}
    r2_ready = bool(r2.get("ready"))
    r2_flag = bool(r2.get("enabled_flag"))
    r2_url = bool(r2.get("url_configured"))
    if r2_ready:
        r2_chip = "r2-prewarm: ready"
    elif r2_flag and r2_url:
        r2_chip = "r2-prewarm: not ready · flag+url · no-secret"
    elif r2_flag:
        r2_chip = "r2-prewarm: not ready · flag on"
    else:
        r2_chip = "r2-prewarm: off"

    # API-key rate-limit chip (same fields as /healthz modules.api_key_rate_limit; no Redis URL value).
    rl = payload.get("api_key_rate_limit") if isinstance(payload.get("api_key_rate_limit"), dict) else {}
    rl_required = bool(rl.get("required"))
    rl_backend = str(rl.get("backend") or "memory")
    rl_requested = str(rl.get("requested") or "memory")
    rl_fallback = bool(rl.get("using_memory_fallback"))
    rl_redis_url = bool(rl.get("redis_url_configured"))
    if not rl_required:
        rl_chip = f"api-key-rl: off · {rl_backend}"
    elif rl_fallback:
        rl_chip = f"api-key-rl: required · {rl_requested}→{rl_backend}"
    else:
        rl_chip = f"api-key-rl: required · {rl_backend}"
    if rl_requested == "redis" and not rl_redis_url:
        rl_chip += " · no-redis-url"

    # Job queue chip (sqlite/memory only; redis/nats rejected at settings load).
    jq = payload.get("job_queue") if isinstance(payload.get("job_queue"), dict) else {}
    jq_backend = str(jq.get("backend") or "sqlite")
    jq_requested = str(jq.get("requested") or "sqlite")
    jq_implemented = bool(jq.get("implemented", True))
    if jq_backend == jq_requested:
        jq_chip = f"job-queue: {jq_backend}"
    else:
        jq_chip = f"job-queue: {jq_requested}→{jq_backend}"
    jq_chip += " · implemented" if jq_implemented else " · not-implemented"

    # Recent dedup chip (memory default; redis fail-open).
    rd = payload.get("recent_dedup") if isinstance(payload.get("recent_dedup"), dict) else {}
    rd_backend = str(rd.get("backend") or "memory")
    rd_requested = str(rd.get("requested") or "memory")
    rd_fallback = bool(rd.get("using_memory_fallback"))
    if rd_fallback:
        rd_chip = f"recent-dedup: {rd_requested}→{rd_backend}"
    else:
        rd_chip = f"recent-dedup: {rd_backend}"

    # Dialect / factory labels (same as /healthz modules.catalog|tags|random_*).
    cat = payload.get("catalog") if isinstance(payload.get("catalog"), dict) else {}
    tags = payload.get("tags") if isinstance(payload.get("tags"), dict) else {}
    rs = payload.get("random_service") if isinstance(payload.get("random_service"), dict) else {}
    rp = payload.get("random_pick") if isinstance(payload.get("random_pick"), dict) else {}
    catalog_backend = str(cat.get("backend") or "sqlite")
    tags_backend = str(tags.get("backend") or "sqlite")
    rs_backend = str(rs.get("backend") or "default")
    rp_backend = str(rp.get("backend") or "sqlite")
    ports_chip = f"ports: cat {catalog_backend} · tags {tags_backend} · svc {rs_backend} · pick {rp_backend}"

    json_url = u("/status.json")
    docs_url = u("/docs")
    random_url = u("/random")
    wtf_url = u("/wtf")

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="color-scheme" content="light" />
  <title>Status · Random Mage</title>
  <style>
    :root {{
      --bg: #f4efe6;
      --card: rgba(255, 250, 243, 0.80);
      --card-2: rgba(255, 250, 243, 0.92);
      --border: rgba(58, 38, 26, 0.14);
      --text: rgba(43, 29, 22, 0.94);
      --muted: rgba(67, 51, 44, 0.78);
      --muted2: rgba(67, 51, 44, 0.64);
      --link: #a2522c;
      --accent: #c07046;
      --ok: #1f7a56;
      --bad: #b42318;
      --warn: #b45309;
      --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      --sans: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji","Segoe UI Emoji";
    }}

    * {{ box-sizing: border-box; }}
    html, body {{ height: 100%; }}
    body {{
      margin: 0;
      font-family: var(--sans);
      color: var(--text);
      background:
        radial-gradient(1000px 620px at 12% 8%, rgba(192, 112, 70, 0.18), transparent 58%),
        radial-gradient(900px 560px at 88% 0%, rgba(162, 82, 44, 0.14), transparent 60%),
        radial-gradient(760px 760px at 60% 92%, rgba(31, 122, 86, 0.10), transparent 58%),
        var(--bg);
    }}

    a {{ color: var(--link); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}

    .wrap {{
      max-width: 1020px;
      margin: 0 auto;
      padding: 28px 18px 54px;
    }}
    @media (max-width: 520px) {{
      .wrap {{ padding: 20px 14px 44px; }}
    }}

    .hero {{
      border: 1px solid var(--border);
      background: linear-gradient(180deg, rgba(255,250,243,0.95), rgba(255,250,243,0.74));
      border-radius: 14px;
      box-shadow: 0 10px 30px rgba(40, 24, 16, 0.10);
      padding: 16px 16px 14px;
    }}
    .hero h1 {{
      margin: 0;
      font-size: 20px;
      letter-spacing: 0.2px;
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .sub {{
      margin-top: 8px;
      color: var(--muted);
      line-height: 1.55;
      font-size: 13px;
    }}

    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 10px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: var(--card-2);
      font-size: 12px;
      color: rgba(43, 29, 22, 0.88);
      white-space: nowrap;
    }}
    .dot {{
      width: 9px;
      height: 9px;
      border-radius: 50%;
      background: var(--ok);
      box-shadow: 0 0 0 4px rgba(31, 122, 86, 0.12);
    }}
    .dot.bad {{
      background: var(--bad);
      box-shadow: 0 0 0 4px rgba(180, 35, 24, 0.10);
    }}

    .chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 10px;
    }}
    .chip {{
      padding: 6px 10px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: var(--card-2);
      font-size: 12px;
      color: rgba(43, 29, 22, 0.86);
      white-space: nowrap;
    }}

    .grid {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 14px;
      margin-top: 14px;
    }}
    @media (min-width: 860px) {{
      .grid {{ grid-template-columns: 1fr 1fr; }}
    }}

    .card {{
      border: 1px solid var(--border);
      background: var(--card);
      border-radius: 14px;
      padding: 16px 16px 14px;
      box-shadow: 0 10px 30px rgba(40, 24, 16, 0.06);
    }}
    @media (max-width: 520px) {{
      .card {{ padding: 14px 14px 12px; }}
    }}
    .card h2 {{
      font-size: 15px;
      margin: 0 0 10px;
      letter-spacing: 0.2px;
    }}

    .kpi {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }}
    @media (min-width: 980px) {{
      .kpi {{ grid-template-columns: 1fr 1fr 1fr 1fr; }}
    }}
    .k {{
      border: 1px solid var(--border);
      background: rgba(43, 29, 22, 0.03);
      border-radius: 12px;
      padding: 10px 10px 9px;
      min-height: 66px;
    }}
    .k .label {{
      color: var(--muted2);
      font-size: 12px;
      line-height: 1.2;
    }}
    .k .val {{
      margin-top: 6px;
      font-size: 18px;
      letter-spacing: 0.2px;
      font-weight: 650;
    }}
    .k .hint {{
      margin-top: 2px;
      color: var(--muted2);
      font-size: 11px;
      line-height: 1.25;
    }}

    .row {{
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
      margin-top: 10px;
    }}
    select {{
      border: 1px solid var(--border);
      background: var(--card-2);
      border-radius: 10px;
      padding: 8px 10px;
      font-size: 12px;
      color: rgba(43, 29, 22, 0.90);
      outline: none;
    }}
    .pie-wrap {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 14px;
      margin-top: 10px;
    }}
    @media (min-width: 700px) {{
      .pie-wrap {{ grid-template-columns: 220px 1fr; align-items: center; }}
    }}
    .pie {{
      width: 180px;
      height: 180px;
      border-radius: 50%;
      border: 1px solid var(--border);
      background: conic-gradient(#ddd 0deg, #eee 360deg);
      box-shadow: inset 0 0 0 8px rgba(255, 250, 243, 0.8);
    }}
    .legend {{
      display: grid;
      gap: 8px;
    }}
    .li {{
      display: flex;
      align-items: center;
      gap: 8px;
      color: rgba(43, 29, 22, 0.88);
      font-size: 13px;
      line-height: 1.3;
    }}
    .sw {{
      width: 12px;
      height: 12px;
      border-radius: 4px;
      border: 1px solid rgba(58, 38, 26, 0.18);
      background: #ddd;
      flex: 0 0 auto;
    }}
    .muted {{ color: var(--muted2); }}
    .footer {{
      margin-top: 14px;
      color: var(--muted2);
      font-size: 12px;
      line-height: 1.6;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>
        Status · Random Mage
        <span class="badge" title="当前页面也是一个健康信号（可访问说明 API 在线）">
          <span class="dot {'bad' if api_status != 'ok' else ''}"></span>
          <strong style="font-weight:650;">{api_status.upper()}</strong>
          <span class="muted">{api_status_code}</span>
        </span>
      </h1>
      <div class="sub">
        最后更新：<span class="muted">{updated_at}</span> · JSON：<a href="{json_url}">{json_url}</a>
      </div>
      <div class="chips" aria-label="quick-links">
        <a class="chip" href="{random_url}"><strong>/random</strong> 随机出图</a>
        <a class="chip" href="{docs_url}"><strong>/docs</strong> 使用文档</a>
        <a class="chip" href="{wtf_url}"><strong>/wtf</strong> 瀑布流</a>
        <span class="chip" title="process-local dual-run circuit (same as /healthz modules.random_engine.circuit; no outbound probe)">{eng_chip}</span>
        <span class="chip" title="Image Edge config readiness (same as /healthz modules.image_edge; no secrets / no outbound edge probe)">{edge_chip}</span>
        <span class="chip" title="CF API proxy config readiness (same as /healthz modules.cf_api_proxy; no secrets / no outbound worker probe)">{cf_chip}</span>
        <span class="chip" title="R2 prewarm config readiness (same as /healthz modules.r2_prewarm ready; no secrets / no webhook probe)">{r2_chip}</span>
        <span class="chip" title="Public API key rate-limit backend (same as /healthz modules.api_key_rate_limit; no Redis URL / no probe)">{rl_chip}</span>
        <span class="chip" title="Job queue port (same as /healthz modules.job_queue; redis/nats rejected at boot)">{jq_chip}</span>
        <span class="chip" title="Recent-dedup port (same as /healthz modules.recent_dedup; no Redis URL / no probe)">{rd_chip}</span>
        <span class="chip" title="Modular port dialect labels (same as /healthz modules.catalog|tags|random_service|random_pick)">{ports_chip}</span>
      </div>
    </div>

    <div class="grid">
      <section class="card">
        <h2>图库概览</h2>
        <div class="kpi">
          <div class="k"><div class="label">总图片数</div><div class="val">{images_total}</div><div class="hint">images</div></div>
          <div class="k"><div class="label">总作品数</div><div class="val">{illust_total}</div><div class="hint">DISTINCT illust_id</div></div>
          <div class="k"><div class="label">总作者数</div><div class="val">{authors_total}</div><div class="hint">DISTINCT user_id</div></div>
          <div class="k"><div class="label">可用图片数</div><div class="val">{images_enabled}</div><div class="hint">status=1</div></div>
        </div>
      </section>

      <section class="card">
        <h2>请求概览（/random）</h2>
        <div class="kpi">
          <div class="k"><div class="label">总请求数</div><div class="val">{random_total}</div><div class="hint">跨重启持久化</div></div>
          <div class="k"><div class="label">实时并发</div><div class="val">{random_in_flight}</div><div class="hint">in_flight</div></div>
          <div class="k"><div class="label">近 60 秒请求</div><div class="val">{last_window_requests}</div><div class="hint">window</div></div>
          <div class="k"><div class="label">近 60 秒成功率</div><div class="val">{last_window_success_rate*100:.1f}%</div><div class="hint">2xx/3xx</div></div>
        </div>
      </section>
    </div>

    <section class="card" style="margin-top: 14px;">
      <h2>占比图</h2>
      <div class="row">
        <span class="muted">选择维度：</span>
        <select id="metric">
          <option value="r18">R18 / 非 R18</option>
          <option value="ai">AI / 非 AI</option>
        </select>
        <span class="muted" id="metricNote"></span>
      </div>
      <div class="pie-wrap">
        <div class="pie" id="pie" aria-label="pie"></div>
        <div class="legend" id="legend" aria-label="legend"></div>
      </div>
      <div class="footer">
        说明：占比统计基于 <code>status=1</code> 的图片；未补全字段会落到 unknown。<br/>
        /status 只读展示，不需要登录。{(" " + public_key_note) if public_key_note else ""}
      </div>
    </section>
  </div>

  <script>
    const DATA = {json.dumps(payload, ensure_ascii=False, separators=(",", ":"))};

    function fmtInt(n) {{
      const x = Number(n || 0);
      if (!Number.isFinite(x)) return "0";
      return String(Math.trunc(x));
    }}

    function renderPie(slices) {{
      const pie = document.getElementById("pie");
      const legend = document.getElementById("legend");
      const total = slices.reduce((s, it) => s + (Number(it.value) || 0), 0) || 0;

      if (!total) {{
        pie.style.background = "conic-gradient(#ddd 0deg, #eee 360deg)";
        legend.innerHTML = '<div class="muted">暂无数据</div>';
        return;
      }}

      let acc = 0;
      const stops = [];
      for (const it of slices) {{
        const v = Math.max(0, Number(it.value) || 0);
        const a0 = (acc / total) * 360;
        acc += v;
        const a1 = (acc / total) * 360;
        stops.push(`${{it.color}} ${{a0}}deg ${{a1}}deg`);
      }}
      pie.style.background = `conic-gradient(${{stops.join(", ")}})`;

      legend.innerHTML = slices.map(it => {{
        const v = Math.max(0, Number(it.value) || 0);
        const pct = total > 0 ? (v / total) * 100 : 0;
        return `
          <div class="li">
            <span class="sw" style="background:${{it.color}}"></span>
            <div><strong>${{it.label}}</strong> <span class="muted">${{fmtInt(v)}} · ${{pct.toFixed(1)}}%</span></div>
          </div>
        `;
      }}).join("");
    }}

    function pickMetric(metric) {{
      const note = document.getElementById("metricNote");
      const bd = (DATA.breakdown || {{}});
      if (metric === "ai") {{
        note.textContent = "（ai_type）";
        const ai = bd.ai || {{}};
        renderPie([
          {{ label: "AI", value: ai.ai || 0, color: "#c07046" }},
          {{ label: "非 AI", value: ai.non_ai || 0, color: "#1f7a56" }},
          {{ label: "未知", value: ai.unknown || 0, color: "#8f857c" }},
        ]);
        return;
      }}
      // default: r18
      note.textContent = "（x_restrict）";
      const r = bd.r18 || {{}};
      renderPie([
        {{ label: "R18", value: r.r18 || 0, color: "#b42318" }},
        {{ label: "非 R18", value: r.safe || 0, color: "#1f7a56" }},
        {{ label: "未知", value: r.unknown || 0, color: "#8f857c" }},
      ]);
    }}

    const sel = document.getElementById("metric");
    pickMetric(sel.value);
    sel.addEventListener("change", () => pickMetric(sel.value));
  </script>
</body>
</html>
"""


@router.get(
    "/status.json",
    summary="Public modular status snapshot",
    description=(
        "Machine-readable public status: gallery/random counters plus local modular config "
        "under `data.*` — same honesty shapes as `/healthz` → `modules.*` for "
        "`random_engine` (incl. process circuit), `image_edge`, `cf_api_proxy`, `r2_prewarm`, "
        "`api_key_rate_limit`, `job_queue`, `recent_dedup`, and dialect labels "
        "`catalog`/`tags`/`random_service`/`random_pick`. No secrets and no outbound probes. "
        "Middleware-exempt when `PUBLIC_API_KEY_REQUIRED` (same class as `/healthz`/`/docs`). "
        "HTML twin: `/status`."
    ),
)
async def status_json(request: Request) -> JSONResponse:
    rid = get_or_create_request_id(request)
    set_request_id_on_state(request, rid)

    engine = request.app.state.engine
    settings = getattr(request.app.state, "settings", None)
    api_status = "ok"
    api_status_code = 200
    payload: dict[str, Any] = {"api_status": api_status, "api_status_code": api_status_code, "updated_at": iso_utc_ms()}

    stats = getattr(request.app.state, "random_request_stats", None)
    if stats is not None:
        payload["random"] = asdict(await stats.snapshot())
    else:
        payload["random"] = {
            "total_requests": 0,
            "total_ok": 0,
            "total_error": 0,
            "in_flight": 0,
            "window_seconds": 60,
            "last_window_requests": 0,
            "last_window_ok": 0,
            "last_window_error": 0,
            "last_window_success_rate": 0.0,
        }

    # Local dual-run circuit (same fields as /healthz modules.random_engine; no outbound probe).
    payload["random_engine"] = _random_engine_public_snapshot(settings)
    # Image Edge config readiness (same fields as /healthz modules.image_edge; no secrets / probe).
    payload["image_edge"] = _image_edge_public_snapshot(settings)
    # CF API proxy config readiness (same fields as /healthz modules.cf_api_proxy; no secrets / probe).
    payload["cf_api_proxy"] = _cf_api_proxy_public_snapshot(settings)
    # R2 prewarm config readiness (public subset of /healthz modules.r2_prewarm; no secrets / probe).
    payload["r2_prewarm"] = _r2_prewarm_public_snapshot(settings)
    # API-key rate-limit backend honesty (same as /healthz modules.api_key_rate_limit; no Redis URL/probe).
    payload["api_key_rate_limit"] = _api_key_rate_limit_public_snapshot(request, settings)
    # Modular ports (same shapes as /healthz modules.job_queue / recent_dedup).
    payload["job_queue"] = _job_queue_public_snapshot(request, settings)
    payload["recent_dedup"] = _recent_dedup_public_snapshot(request, settings)
    payload["catalog"] = _port_backend_public_snapshot(request, "catalog_store", default="sqlite")
    payload["tags"] = _port_backend_public_snapshot(request, "tag_store", default="sqlite")
    payload["random_service"] = _port_backend_public_snapshot(request, "random_service", default="default")
    payload["random_pick"] = _port_backend_public_snapshot(request, "random_pick", default="sqlite")

    try:
        payload.update(await _query_gallery_stats(engine))
    except Exception as exc:
        api_status = "degraded"
        api_status_code = 503
        payload["api_status"] = api_status
        payload["api_status_code"] = api_status_code
        payload["error"] = {"type": type(exc).__name__, "message": str(exc)}

    resp = JSONResponse(status_code=int(api_status_code), content={"ok": api_status_code == 200, "data": payload, "request_id": rid})
    set_request_id_header(resp, rid)
    return resp


@router.get(
    "/status",
    include_in_schema=False,
    summary="Public modular status HTML",
    description=(
        "Human-facing status dashboard mirroring `/status.json` modular `data.*` chips "
        "(random_engine/image_edge/cf_api_proxy/r2_prewarm/api_key_rate_limit/job_queue/"
        "recent_dedup + dialect labels). API-key exempt; no secrets / no outbound probes. "
        "JSON twin: GET /status.json (in OpenAPI)."
    ),
)
async def status_page(request: Request) -> HTMLResponse:
    rid = get_or_create_request_id(request)
    set_request_id_on_state(request, rid)

    engine = request.app.state.engine
    settings = getattr(request.app.state, "settings", None)
    api_status = "ok"
    api_status_code = 200

    payload: dict[str, Any] = {"api_status": api_status, "api_status_code": api_status_code, "updated_at": iso_utc_ms()}

    stats = getattr(request.app.state, "random_request_stats", None)
    if stats is not None:
        payload["random"] = asdict(await stats.snapshot())

    # Local dual-run circuit for HTML chip + DATA payload (no outbound probe).
    payload["random_engine"] = _random_engine_public_snapshot(settings)
    payload["image_edge"] = _image_edge_public_snapshot(settings)
    payload["cf_api_proxy"] = _cf_api_proxy_public_snapshot(settings)
    payload["r2_prewarm"] = _r2_prewarm_public_snapshot(settings)
    payload["api_key_rate_limit"] = _api_key_rate_limit_public_snapshot(request, settings)
    payload["job_queue"] = _job_queue_public_snapshot(request, settings)
    payload["recent_dedup"] = _recent_dedup_public_snapshot(request, settings)
    payload["catalog"] = _port_backend_public_snapshot(request, "catalog_store", default="sqlite")
    payload["tags"] = _port_backend_public_snapshot(request, "tag_store", default="sqlite")
    payload["random_service"] = _port_backend_public_snapshot(request, "random_service", default="default")
    payload["random_pick"] = _port_backend_public_snapshot(request, "random_pick", default="sqlite")

    try:
        payload.update(await _query_gallery_stats(engine))
    except Exception as exc:
        api_status = "degraded"
        api_status_code = 503
        payload["api_status"] = api_status
        payload["api_status_code"] = api_status_code
        payload["error"] = {"type": type(exc).__name__, "message": str(exc)}

    public_api_key_required = bool(getattr(settings, "public_api_key_required", False))
    html = _build_status_html(
        base_url=str(getattr(request, "base_url", "") or "").rstrip("/"),
        status_code=int(api_status_code),
        payload=payload,
        public_api_key_required=public_api_key_required,
    )
    resp = HTMLResponse(content=html, status_code=int(api_status_code), headers={"Cache-Control": "no-store"})
    set_request_id_header(resp, rid)
    return resp
