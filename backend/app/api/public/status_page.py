from __future__ import annotations

import json
import math
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.core.request_id import get_or_create_request_id, set_request_id_header, set_request_id_on_state
from app.core.time import iso_utc_ms
from app.db.session import with_sqlite_busy_retry

router = APIRouter()


def _clamp_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


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

            tag_id = (
                await conn.exec_driver_sql(
                    "SELECT id FROM tags WHERE name = ? COLLATE NOCASE LIMIT 1;",
                    ("loli",),
                )
            ).scalar_one_or_none()
            if tag_id is None:
                loli_count = 0
                loli_found = False
            else:
                loli_found = True
                loli_count = int(
                    (
                        await conn.exec_driver_sql(
                            """
SELECT COUNT(DISTINCT it.image_id)
FROM image_tags it
JOIN images i ON i.id = it.image_id
WHERE it.tag_id = ? AND i.status = 1;
""".strip(),
                            (int(tag_id),),
                        )
                    ).scalar_one()
                )

        non_loli_count = max(0, int(images_enabled) - int(loli_count))

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
                "loli": {"loli": loli_count, "non_loli": non_loli_count, "tag_found": bool(loli_found)},
            },
        }

    return await with_sqlite_busy_retry(_op)


def _build_status_html(*, base_url: str, status_code: int, payload: dict[str, Any]) -> str:
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

    gallery = payload.get("gallery") if isinstance(payload.get("gallery"), dict) else {}
    images_total = _clamp_int(gallery.get("images_total"))
    illust_total = _clamp_int(gallery.get("illust_total"))
    authors_total = _clamp_int(gallery.get("authors_total"))

    random_stats = payload.get("random") if isinstance(payload.get("random"), dict) else {}
    random_total = _clamp_int(random_stats.get("total_requests"))
    random_in_flight = _clamp_int(random_stats.get("in_flight"))
    last_window_requests = _clamp_int(random_stats.get("last_window_requests"))
    last_window_success_rate = float(random_stats.get("last_window_success_rate") or 0.0)
    if not math.isfinite(last_window_success_rate):
        last_window_success_rate = 0.0
    last_window_success_rate = max(0.0, min(float(last_window_success_rate), 1.0))

    json_url = u("/status.json")
    docs_url = u("/docs")
    random_url = u("/random")
    admin_url = u("/admin")

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
        <a class="chip" href="{admin_url}"><strong>/admin</strong> 管理后台</a>
      </div>
    </div>

    <div class="grid">
      <section class="card">
        <h2>图库概览</h2>
        <div class="kpi">
          <div class="k"><div class="label">总图片数</div><div class="val">{images_total}</div><div class="hint">images</div></div>
          <div class="k"><div class="label">总作品数</div><div class="val">{illust_total}</div><div class="hint">DISTINCT illust_id</div></div>
          <div class="k"><div class="label">总作者数</div><div class="val">{authors_total}</div><div class="hint">DISTINCT user_id</div></div>
          <div class="k"><div class="label">更多统计</div><div class="val">—</div><div class="hint"><a href="{admin_url}">后台首页</a></div></div>
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
          <option value="loli">萝莉 / 非萝莉</option>
        </select>
        <span class="muted" id="metricNote"></span>
      </div>
      <div class="pie-wrap">
        <div class="pie" id="pie" aria-label="pie"></div>
        <div class="legend" id="legend" aria-label="legend"></div>
      </div>
      <div class="footer">
        说明：占比统计基于 <code>status=1</code> 的图片；未补全字段会落到 unknown。<br/>
        /status 只读展示，不需要登录。
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
      if (metric === "loli") {{
        const l = bd.loli || {{}};
        note.textContent = (l.tag_found === false) ? "（tags 表中未找到 loli 标签）" : "（标签：loli）";
        renderPie([
          {{ label: "萝莉", value: l.loli || 0, color: "#c07046" }},
          {{ label: "非萝莉", value: l.non_loli || 0, color: "#1f7a56" }},
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


@router.get("/status.json", include_in_schema=False)
async def status_json(request: Request) -> JSONResponse:
    rid = get_or_create_request_id(request)
    set_request_id_on_state(request, rid)

    engine = request.app.state.engine
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


@router.get("/status", include_in_schema=False)
async def status_page(request: Request) -> HTMLResponse:
    rid = get_or_create_request_id(request)
    set_request_id_on_state(request, rid)

    engine = request.app.state.engine
    api_status = "ok"
    api_status_code = 200

    payload: dict[str, Any] = {"api_status": api_status, "api_status_code": api_status_code, "updated_at": iso_utc_ms()}

    stats = getattr(request.app.state, "random_request_stats", None)
    if stats is not None:
        payload["random"] = asdict(await stats.snapshot())

    try:
        payload.update(await _query_gallery_stats(engine))
    except Exception as exc:
        api_status = "degraded"
        api_status_code = 503
        payload["api_status"] = api_status
        payload["api_status_code"] = api_status_code
        payload["error"] = {"type": type(exc).__name__, "message": str(exc)}

    html = _build_status_html(
        base_url=str(getattr(request, "base_url", "") or "").rstrip("/"),
        status_code=int(api_status_code),
        payload=payload,
    )
    resp = HTMLResponse(content=html, status_code=int(api_status_code), headers={"Cache-Control": "no-store"})
    set_request_id_header(resp, rid)
    return resp
