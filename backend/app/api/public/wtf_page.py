from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.request_id import get_or_create_request_id, set_request_id_header, set_request_id_on_state

router = APIRouter()


def _build_wtf_html(*, base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if not base:
        base = ""

    def u(path: str) -> str:
        path_norm = (path or "").strip()
        if not path_norm.startswith("/"):
            path_norm = "/" + path_norm
        return f"{base}{path_norm}"

    docs_url = u("/docs")
    status_url = u("/status")
    random_url = u("/random")

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="color-scheme" content="light" />
  <title>WTF · 瀑布流 · Random Mage</title>
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
      max-width: 980px;
      margin: 0 auto;
      padding: 22px 16px 44px;
    }}
    @media (max-width: 520px) {{
      .wrap {{ padding: 18px 12px 40px; }}
    }}

    .top {{
      border: 1px solid var(--border);
      background: linear-gradient(180deg, rgba(255,250,243,0.95), rgba(255,250,243,0.74));
      border-radius: 14px;
      box-shadow: 0 10px 30px rgba(40, 24, 16, 0.10);
      padding: 14px 14px 12px;
    }}
    .top h1 {{
      margin: 0;
      font-size: 18px;
      letter-spacing: 0.2px;
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .sub {{
      margin-top: 6px;
      color: var(--muted);
      line-height: 1.55;
      font-size: 13px;
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
    .row {{
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
      margin-top: 10px;
    }}
    .btn {{
      cursor: pointer;
      border: 1px solid var(--border);
      background: var(--card-2);
      border-radius: 10px;
      padding: 8px 10px;
      font-size: 12px;
      color: rgba(43, 29, 22, 0.90);
    }}
    .btn:hover {{ filter: brightness(0.98); }}
    .muted {{ color: var(--muted2); }}
    code {{
      font-family: var(--mono);
      font-size: 12px;
      background: rgba(43, 29, 22, 0.06);
      border: 1px solid rgba(58, 38, 26, 0.14);
      border-radius: 8px;
      padding: 1px 6px;
    }}

    .feed {{
      margin-top: 14px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }}
    .item {{
      border: 1px solid var(--border);
      background: var(--card);
      border-radius: 14px;
      overflow: hidden;
      box-shadow: 0 10px 30px rgba(40, 24, 16, 0.06);
      content-visibility: auto;
      contain-intrinsic-size: 800px;
    }}
    .item img {{
      width: 100%;
      height: auto;
      display: block;
      opacity: 0;
      transform: translateY(3px);
      transition: opacity 180ms ease, transform 220ms ease;
    }}
    .item.loaded img {{
      opacity: 1;
      transform: translateY(0px);
    }}
    .sentinel {{
      text-align: center;
      padding: 18px 0 0;
      color: var(--muted2);
      font-size: 12px;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <h1>WTF · 瀑布流随机</h1>
      <div class="sub">
        进入即自动加载，往下滑继续出图。<span class="muted">默认开启 <code>adaptive=1</code>（移动端偏竖图/更低像素门槛，PC 偏横图/更高门槛；不覆盖你显式传入的过滤参数）。</span>
      </div>
      <div class="chips">
        <a class="chip" href="{docs_url}"><strong>/docs</strong> 参数说明</a>
        <a class="chip" href="{status_url}"><strong>/status</strong> 运行状态</a>
        <a class="chip" href="{random_url}"><strong>/random</strong> 单张随机</a>
      </div>
      <div class="row">
        <button class="btn" id="toggle">暂停加载</button>
        <span class="muted" id="info"></span>
      </div>
    </div>

    <div id="feed" class="feed" aria-label="feed"></div>
    <div id="sentinel" class="sentinel">加载中…</div>
  </div>

  <script>
    const feed = document.getElementById("feed");
    const sentinel = document.getElementById("sentinel");
    const info = document.getElementById("info");
    const toggle = document.getElementById("toggle");

    const baseParams = new URLSearchParams(window.location.search);
    baseParams.delete("format");
    baseParams.delete("redirect");
    baseParams.delete("t");
    if (!baseParams.has("adaptive")) baseParams.set("adaptive", "1");
    baseParams.set("redirect", "1");

    const baseQuery = baseParams.toString();
    info.textContent = baseQuery ? `当前过滤: ?${{baseQuery}}` : "当前过滤: （无）";

    let paused = false;
    let seq = 0;
    let inflight = 0;
    let rendered = 0;
    let target = 0;

    function isMobile() {{
      return window.matchMedia && window.matchMedia("(max-width: 520px)").matches;
    }}

    function cfg() {{
      const mobile = isMobile();
      const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
      const effectiveType = conn && conn.effectiveType ? String(conn.effectiveType) : "";
      const slow = effectiveType.includes("2g") || effectiveType.includes("3g");

      const base = mobile
        ? { initial: 8, step: 6, maxInflight: 8 }
        : { initial: 12, step: 10, maxInflight: 14 };

      return {{
        initial: base.initial,
        step: base.step,
        maxInflight: slow ? Math.max(4, Math.floor(base.maxInflight / 2)) : base.maxInflight,
      }};
    }}

    function buildUrl() {{
      const p = new URLSearchParams(baseParams);
      p.set("t", String(Date.now()) + "_" + String(seq++));
      return "/random?" + p.toString();
    }}

    function updateSentinel() {{
      const status = paused ? "已暂停" : "加载中";
      sentinel.textContent = `${{status}} · 已显示 ${{rendered}} 张 · in-flight ${{inflight}}`;
    }}

    function appendItem(imgEl) {{
      const item = document.createElement("div");
      item.className = "item";
      item.appendChild(imgEl);
      feed.appendChild(item);
      requestAnimationFrame(() => item.classList.add("loaded"));
    }}

    function startOne() {{
      if (paused) return;
      const c = cfg();
      if (inflight >= c.maxInflight) return;
      if ((rendered + inflight) >= target) return;

      inflight += 1;
      updateSentinel();

      const img = new Image();
      img.decoding = "async";
      img.referrerPolicy = "no-referrer";
      img.alt = "";

      let tries = 0;
      const load = () => {{
        tries += 1;
        img.src = buildUrl();
      }};

      img.onload = () => {{
        inflight = Math.max(0, inflight - 1);
        rendered += 1;
        appendItem(img);
        updateSentinel();
        ensure();
      }};
      img.onerror = () => {{
        if (tries < 3) {{
          setTimeout(load, 250 * tries);
          return;
        }}
        inflight = Math.max(0, inflight - 1);
        updateSentinel();
        ensure();
      }};

      load();
    }}

    function ensure() {{
      if (paused) return;
      const c = cfg();
      while (inflight < c.maxInflight && (rendered + inflight) < target) {{
        startOne();
      }}
    }}

    function bump() {{
      const c = cfg();
      target = Math.max(target, rendered + inflight + c.step);
      ensure();
    }}

    toggle.addEventListener("click", () => {{
      paused = !paused;
      toggle.textContent = paused ? "继续加载" : "暂停加载";
      updateSentinel();
      if (!paused) {{
        ensure();
      }}
    }});

    const io = new IntersectionObserver((entries) => {{
      for (const e of entries) {{
        if (e.isIntersecting) bump();
      }}
    }}, {{ root: null, rootMargin: "2200px 0px", threshold: 0 }});
    io.observe(sentinel);

    // Initial fill.
    target = cfg().initial;
    ensure();
    updateSentinel();

    window.addEventListener("resize", () => {{
      const c = cfg();
      target = Math.max(target, c.initial);
      ensure();
    }});
  </script>
</body>
</html>
"""


@router.get("/wtf", include_in_schema=False)
async def wtf_page(request: Request) -> HTMLResponse:
    rid = get_or_create_request_id(request)
    set_request_id_on_state(request, rid)
    html = _build_wtf_html(base_url=str(getattr(request, "base_url", "") or "").rstrip("/"))
    resp = HTMLResponse(content=html, status_code=200, headers={"Cache-Control": "no-store"})
    set_request_id_header(resp, rid)
    return resp
