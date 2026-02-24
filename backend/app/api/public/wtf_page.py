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
      max-width: 1180px;
      margin: 0 auto;
      padding: 22px 16px 44px;
    }}
    @media (min-width: 1400px) {{
      .wrap {{ max-width: 1280px; }}
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

    .seg {{
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--border);
      background: rgba(43, 29, 22, 0.06);
      border-radius: 999px;
      padding: 2px;
      gap: 2px;
    }}
    .segbtn {{
      cursor: pointer;
      border: 0;
      background: transparent;
      border-radius: 999px;
      padding: 7px 10px;
      font-size: 12px;
      color: rgba(43, 29, 22, 0.78);
      user-select: none;
      white-space: nowrap;
    }}
    .segbtn:hover {{ filter: brightness(0.98); }}
    .segbtn.active {{
      background: var(--card-2);
      color: rgba(43, 29, 22, 0.92);
      box-shadow: 0 6px 18px rgba(40, 24, 16, 0.10);
    }}
    .segbtn:focus-visible {{
      outline: 2px solid rgba(162, 82, 44, 0.55);
      outline-offset: 2px;
    }}

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
    @keyframes shimmer {{
      0% {{ background-position: 0% 0; }}
      100% {{ background-position: 200% 0; }}
    }}
    .item.pending {{
      background: linear-gradient(
        90deg,
        rgba(255, 250, 243, 0.86),
        rgba(255, 250, 243, 0.56),
        rgba(255, 250, 243, 0.86)
      );
      background-size: 200% 100%;
      animation: shimmer 1100ms ease-in-out infinite;
      border-color: rgba(58, 38, 26, 0.10);
      box-shadow: none;
    }}
    .item.pending img {{
      aspect-ratio: var(--ar, 16 / 9);
    }}
    .meta {{
      display: none;
      padding: 8px 10px 10px;
      border-top: 1px solid rgba(58, 38, 26, 0.10);
      background: rgba(255, 250, 243, 0.92);
      color: rgba(67, 51, 44, 0.78);
      font-size: 12px;
      line-height: 1.4;
      gap: 6px;
      flex-wrap: wrap;
    }}
    .meta a {{
      color: var(--link);
      text-decoration: none;
      font-family: var(--mono);
      font-size: 11px;
    }}
    .meta a:hover {{ text-decoration: underline; }}
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
      padding: 12px 0 0;
      color: var(--muted2);
      font-size: 11px;
    }}

    body.view-masonry .wrap {{
      max-width: none;
      padding-left: 0;
      padding-right: 0;
    }}
    body.view-masonry .top {{
      margin: 0 16px;
    }}
    @media (max-width: 520px) {{
      body.view-masonry .top {{ margin: 0 12px; }}
    }}
    body.view-masonry .feed {{
      margin-top: 14px;
      display: flex;
      flex-direction: row;
      gap: 0;
      align-items: flex-start;
      width: 100%;
    }}
    body.view-masonry .mcol {{
      flex: 1 1 0;
      display: flex;
      flex-direction: column;
      gap: 0;
      line-height: 0;
      min-width: 0;
    }}
    body.view-masonry .item {{
      width: 100%;
      break-inside: avoid;
      border: none;
      border-radius: 0;
      box-shadow: none;
      background: transparent;
      contain-intrinsic-size: 620px;
    }}
    body.view-masonry .item.pending {{
      border: none;
      border-radius: 0;
      box-shadow: none;
    }}
    body.view-masonry .item img {{
      border-radius: 0;
      transition: opacity 180ms ease;
      transform: none;
    }}
    body.view-masonry .item.loaded img {{
      transform: none;
    }}

    body.view-tiles .feed {{
      margin-top: 14px;
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 16px;
    }}
    @media (max-width: 1100px) {{
      body.view-tiles .feed {{
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 14px;
      }}
    }}
    @media (max-width: 760px) {{
      body.view-tiles .feed {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 12px;
      }}
    }}
    @media (max-width: 520px) {{
      body.view-tiles .feed {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
      }}
    }}
    body.view-tiles .item {{
      border-radius: 14px;
      background: var(--card);
      border: 1px solid var(--border);
      box-shadow: 0 10px 30px rgba(40, 24, 16, 0.06);
      transition: transform 120ms ease, box-shadow 180ms ease, border-color 180ms ease;
    }}
    body.view-tiles .item:hover {{
      transform: translateY(-1px);
      border-color: rgba(162, 82, 44, 0.30);
      box-shadow: 0 14px 36px rgba(40, 24, 16, 0.10);
    }}
    body.view-tiles .item:focus-within {{
      border-color: rgba(162, 82, 44, 0.42);
    }}
    body.view-tiles .item img {{
      aspect-ratio: var(--tile-ar, 16 / 9);
      object-fit: cover;
      transform: none;
    }}
    body.view-tiles .item.pending img {{
      aspect-ratio: var(--tile-ar, var(--ar, 16 / 9));
    }}
    body.view-tiles .meta {{
      display: flex;
    }}
    body.view-tiles .meta a {{
      padding: 2px 6px;
      border-radius: 8px;
    }}
    body.view-tiles .meta a:hover {{
      background: rgba(192, 112, 70, 0.14);
      text-decoration: none;
    }}
    body.view-tiles .meta a:focus-visible {{
      outline: 2px solid rgba(162, 82, 44, 0.55);
      outline-offset: 2px;
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
        <div class="seg" id="viewSeg" role="tablist" aria-label="布局切换">
          <button class="segbtn" type="button" data-view="single" role="tab" aria-selected="true">单列</button>
          <button class="segbtn" type="button" data-view="masonry" role="tab" aria-selected="false">标准瀑布流</button>
          <button class="segbtn" type="button" data-view="tiles" role="tab" aria-selected="false">小格子</button>
        </div>
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
    const viewSeg = document.getElementById("viewSeg");
    const viewButtons = Array.from(viewSeg.querySelectorAll("button[data-view]"));

    const qs = new URLSearchParams(window.location.search);
    const VIEW_KEY = "wtf_view";

    const allItems = [];
    let masonryCols = [];
    let masonryHeights = [];
    let masonryCount = 0;

    function normalizeView(raw) {{
      const v = String(raw || "").trim().toLowerCase();
      if (v === "masonry" || v === "mason" || v === "standard") return "masonry";
      if (v === "tiles" || v === "tile" || v === "grid" || v === "small") return "tiles";
      return "single";
    }}

    function ratioToNumber(r) {{
      const s = String(r || "").replace(/\\s+/g, "");
      const m = s.match(/^(\\d+(?:\\.\\d+)?)\\/(\\d+(?:\\.\\d+)?)$/);
      if (!m) return 0.75;
      const w = Number(m[1]);
      const h = Number(m[2]);
      if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return 0.75;
      return h / w;
    }}

    function desiredMasonryCount() {{
      const w = Math.max(0, document.documentElement.clientWidth || window.innerWidth || 0);
      if (w <= 520) return 1;
      if (w <= 980) return 2;
      return 3;
    }}

    function masonryColWidth() {{
      const w = Math.max(320, feed.clientWidth || document.documentElement.clientWidth || window.innerWidth || 1000);
      return masonryCount > 0 ? (w / masonryCount) : w;
    }}

    function masonryEstimateHeight(item) {{
      const ratio = Number(item && item.dataset ? item.dataset.ratio : NaN);
      const r = Number.isFinite(ratio) && ratio > 0 ? ratio : 0.75;
      return masonryColWidth() * r;
    }}

    function rebuildLayout() {{
      try {{ feed.textContent = ""; }} catch (e) {{}}
      masonryCols = [];
      masonryHeights = [];
      masonryCount = 0;

      if (viewMode === "masonry") {{
        masonryCount = desiredMasonryCount();
        masonryHeights = new Array(masonryCount).fill(0);
        for (let i = 0; i < masonryCount; i++) {{
          const col = document.createElement("div");
          col.className = "mcol";
          masonryCols.push(col);
          feed.appendChild(col);
        }}

        for (const item of allItems) {{
          const h = masonryEstimateHeight(item);
          let best = 0;
          for (let i = 1; i < masonryHeights.length; i++) {{
            if (masonryHeights[i] < masonryHeights[best]) best = i;
          }}
          item.dataset.mcol = String(best);
          item.dataset.estH = String(h);
          masonryHeights[best] += h;
          masonryCols[best].appendChild(item);
        }}
        return;
      }}

      for (const item of allItems) {{
        try {{
          delete item.dataset.mcol;
          delete item.dataset.estH;
        }} catch (e) {{}}
        feed.appendChild(item);
      }}
    }}

    function mountNewItem(item) {{
      allItems.push(item);
      if (viewMode !== "masonry") {{
        feed.appendChild(item);
        return;
      }}

      if (!masonryCols.length || masonryCount !== desiredMasonryCount()) {{
        rebuildLayout();
        return;
      }}

      const h = masonryEstimateHeight(item);
      let best = 0;
      for (let i = 1; i < masonryHeights.length; i++) {{
        if (masonryHeights[i] < masonryHeights[best]) best = i;
      }}
      item.dataset.mcol = String(best);
      item.dataset.estH = String(h);
      masonryHeights[best] += h;
      masonryCols[best].appendChild(item);
    }}

    function updateMasonryEstimate(item) {{
      if (viewMode !== "masonry") return;
      if (!item || !item.dataset) return;
      const idx = Number(item.dataset.mcol);
      if (!Number.isInteger(idx) || idx < 0 || idx >= masonryHeights.length) return;
      const oldH = Number(item.dataset.estH);
      const prev = Number.isFinite(oldH) && oldH > 0 ? oldH : 0;
      const next = masonryEstimateHeight(item);
      masonryHeights[idx] = Math.max(0, masonryHeights[idx] - prev + next);
      item.dataset.estH = String(next);
    }}

    let storedView = "";
    try {{ storedView = localStorage.getItem(VIEW_KEY) || ""; }} catch (e) {{}}

    const baseParams = new URLSearchParams(qs);
    baseParams.delete("view");
    baseParams.delete("format");
    baseParams.delete("redirect");
    baseParams.delete("t");
    if (!baseParams.has("adaptive")) baseParams.set("adaptive", "1");

    const showParams = new URLSearchParams(baseParams);
    const baseQuery = showParams.toString();
    info.textContent = baseQuery ? ("当前过滤: ?" + baseQuery) : "当前过滤: （无）";

    let paused = false;
    let viewMode = normalizeView(qs.get("view") || storedView || "single");
    let seq = 0;
    let inflight = 0;
    let rendered = 0;
    let target = 0;
    let failStreak = 0;

    function isMobile() {{
      return window.matchMedia && window.matchMedia("(max-width: 520px)").matches;
    }}

    function pickRatioFromParams() {{
      const o = String(baseParams.get("orientation") || "").trim().toLowerCase();
      if (o === "square") return "1 / 1";
      if (o === "portrait") return "2 / 3";
      if (o === "landscape") return "16 / 9";

      const adaptiveRaw = String(baseParams.get("adaptive") || "").trim().toLowerCase();
      const adaptiveOn = baseParams.has("adaptive") && adaptiveRaw !== "0" && adaptiveRaw !== "false";
      if (adaptiveOn) return isMobile() ? "2 / 3" : "16 / 9";

      return isMobile() ? "3 / 4" : "16 / 9";
    }}

    function cfg() {{
      const mobile = isMobile();
      const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
      const effectiveType = conn && conn.effectiveType ? String(conn.effectiveType) : "";
      const slow = effectiveType.includes("2g") || effectiveType.includes("3g");

      let initial = mobile ? 12 : 18;
      let step = mobile ? 8 : 14;
      let maxInflight = mobile ? 12 : 22;

      if (viewMode === "masonry") {{
        initial = mobile ? 14 : 24;
        step = mobile ? 10 : 16;
        maxInflight = mobile ? 14 : 22;
      }} else if (viewMode === "tiles") {{
        initial = mobile ? 16 : 24;
        step = mobile ? 10 : 14;
        maxInflight = mobile ? 16 : 20;
      }}

      if (slow) {{
        maxInflight = Math.max(6, Math.floor(maxInflight * 0.6));
      }}

      return {{ initial: initial, step: step, maxInflight: maxInflight }};
    }}

    function applyView(mode, persist) {{
      viewMode = normalizeView(mode);
      document.body.classList.toggle("view-single", viewMode === "single");
      document.body.classList.toggle("view-masonry", viewMode === "masonry");
      document.body.classList.toggle("view-tiles", viewMode === "tiles");

      for (const b of viewButtons) {{
        const on = String(b.dataset.view || "") === viewMode;
        b.classList.toggle("active", on);
        b.setAttribute("aria-selected", on ? "true" : "false");
      }}

      if (viewMode === "tiles") {{
        document.documentElement.style.setProperty("--tile-ar", pickRatioFromParams());
      }} else {{
        document.documentElement.style.removeProperty("--tile-ar");
      }}

      if (persist) {{
        try {{ localStorage.setItem(VIEW_KEY, viewMode); }} catch (e) {{}}
      }}

      rebuildLayout();

      const c = cfg();
      target = Math.max(target, c.initial);
      ensure();
      updateSentinel();
    }}

    for (const b of viewButtons) {{
      b.addEventListener("click", () => {{
        applyView(String(b.dataset.view || "single"), true);
      }});
    }}
    applyView(viewMode, false);

    function buildRandomJsonUrl() {{
      const p = new URLSearchParams(baseParams);
      p.set("format", "simple_json");
      p.set("t", String(Date.now()) + "_" + String(seq++));
      return "/random?" + p.toString();
    }}

    function buildProxyQuery() {{
      const qp = new URLSearchParams();
      const pc = String(baseParams.get("pixiv_cat") || "").trim();
      if (pc === "1") qp.set("pixiv_cat", "1");
      const mh = String(baseParams.get("pximg_mirror_host") || "").trim();
      if (mh) qp.set("pximg_mirror_host", mh);
      const s = qp.toString();
      return s ? ("?" + s) : "";
    }}

    async function fetchRandomData() {{
      const url = buildRandomJsonUrl();
      const resp = await fetch(url, {{ cache: "no-store" }});
      if (resp.status === 404) {{
        const e = new Error("NO_MATCH");
        e.name = "NO_MATCH";
        throw e;
      }}
      if (!resp.ok) {{
        throw new Error("HTTP_" + String(resp.status));
      }}
      const body = await resp.json();
      if (!body || body.ok !== true || !body.data || !body.data.urls || !body.data.image) {{
        throw new Error("BAD_BODY");
      }}
      return body.data;
    }}

    function updateSentinel() {{
      const status = paused ? "已暂停" : "加载中";
      sentinel.textContent = status + " · 已显示 " + String(rendered) + " 张 · in-flight " + String(inflight);
    }}

    function setMeta(meta, data) {{
      try {{
        meta.textContent = "";
        const image = data && data.image ? data.image : null;
        if (!image) return;

        const illustId = String(image.illust_id || "").trim();
        const user = image.user || null;
        const userId = user && user.id ? String(user.id).trim() : "";
        const userName = user && user.name ? String(user.name).trim() : "";

        const links = [];
        if (userId) {{
          const a = document.createElement("a");
          a.href = "https://www.pixiv.net/users/" + userId;
          a.target = "_blank";
          a.rel = "noreferrer noopener";
          a.textContent = "pixiv.net/users/" + userId;
          if (userName) a.title = userName;
          links.push(a);
        }}
        if (illustId) {{
          const a = document.createElement("a");
          a.href = "https://www.pixiv.net/artworks/" + illustId;
          a.target = "_blank";
          a.rel = "noreferrer noopener";
          a.textContent = "pixiv.net/artworks/" + illustId;
          links.push(a);
        }}

        for (let i = 0; i < links.length; i++) {{
          if (i > 0) {{
            const sep = document.createElement("span");
            sep.textContent = " · ";
            meta.appendChild(sep);
          }}
          meta.appendChild(links[i]);
        }}
      }} catch (e) {{}}
    }}

    function startOne() {{
      if (paused) return;
      const c = cfg();
      if (inflight >= c.maxInflight) return;
      if ((rendered + inflight) >= target) return;

      inflight += 1;
      updateSentinel();

      const item = document.createElement("div");
      item.className = "item pending";
      const fallbackAr = pickRatioFromParams();
      item.style.setProperty("--ar", fallbackAr);
      item.dataset.ratio = String(ratioToNumber(fallbackAr));

      const img = document.createElement("img");
      img.decoding = "async";
      img.loading = "lazy";
      img.referrerPolicy = "no-referrer";
      img.alt = "";

      const meta = document.createElement("div");
      meta.className = "meta";

      item.appendChild(img);
      item.appendChild(meta);
      mountNewItem(item);

      let tries = 0;
      let done = false;

      const finishOk = () => {{
        if (done) return;
        done = true;
        inflight = Math.max(0, inflight - 1);
        rendered += 1;
        failStreak = 0;
        item.classList.remove("pending");
        requestAnimationFrame(() => item.classList.add("loaded"));
        updateSentinel();
        ensure();
      }};

      const finishFail = (reason) => {{
        if (done) return;
        done = true;
        inflight = Math.max(0, inflight - 1);
        failStreak += 1;
        try {{
          const idx = allItems.indexOf(item);
          if (idx >= 0) allItems.splice(idx, 1);
        }} catch (e) {{}}
        try {{
          const colIdx = Number(item.dataset.mcol);
          const est = Number(item.dataset.estH);
          if (Number.isInteger(colIdx) && colIdx >= 0 && colIdx < masonryHeights.length && Number.isFinite(est) && est > 0) {{
            masonryHeights[colIdx] = Math.max(0, masonryHeights[colIdx] - est);
          }}
        }} catch (e) {{}}
        try {{ item.remove(); }} catch (e) {{}}
        updateSentinel();
        if (String(reason || "") === "NO_MATCH") {{
          paused = true;
          toggle.textContent = "继续加载";
          sentinel.textContent = "没有匹配结果：请放宽过滤条件后重试。";
          return;
        }}
        if (failStreak >= 8) {{
          paused = true;
          toggle.textContent = "继续加载";
          sentinel.textContent = "连续失败较多：可能网络不稳定，请稍后再试或放宽条件。";
          return;
        }}
        ensure();
      }};

      const attempt = async () => {{
        tries += 1;
        try {{
          const data = await fetchRandomData();
          setMeta(meta, data);
          try {{
            const iw = data && data.image ? Number(data.image.width) : NaN;
            const ih = data && data.image ? Number(data.image.height) : NaN;
            if (Number.isFinite(iw) && Number.isFinite(ih) && iw > 0 && ih > 0) {{
              img.setAttribute("width", String(iw));
              img.setAttribute("height", String(ih));
              item.style.setProperty("--ar", String(iw) + " / " + String(ih));
              item.dataset.ratio = String(ih / iw);
              updateMasonryEstimate(item);
            }}
          }} catch (e) {{}}
          const proxy = data && data.urls ? String(data.urls.proxy || "") : "";
          if (!proxy || proxy[0] !== "/") throw new Error("BAD_PROXY");
          img.src = proxy + buildProxyQuery();
        }} catch (e) {{
          if (done) return;
          const name = e && e.name ? String(e.name) : "";
          const msg = e && e.message ? String(e.message) : "";
          if (name === "NO_MATCH" || msg === "NO_MATCH") {{
            finishFail("NO_MATCH");
            return;
          }}
          if (tries < 3) {{
            setTimeout(() => attempt(), 250 * tries);
            return;
          }}
          finishFail(msg || name || "ERROR");
        }}
      }};

      img.onload = () => {{
        try {{
          if ((!img.hasAttribute("width") || !img.hasAttribute("height")) && img.naturalWidth > 0 && img.naturalHeight > 0) {{
            img.setAttribute("width", String(img.naturalWidth));
            img.setAttribute("height", String(img.naturalHeight));
            item.style.setProperty("--ar", String(img.naturalWidth) + " / " + String(img.naturalHeight));
            item.dataset.ratio = String(img.naturalHeight / img.naturalWidth);
            updateMasonryEstimate(item);
          }}
        }} catch (e) {{}}
        finishOk();
      }};
      img.onerror = () => {{
        if (done) return;
        if (tries < 3) {{
          setTimeout(() => attempt(), 250 * tries);
          return;
        }}
        finishFail("IMAGE_ERROR");
      }};

      attempt();
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
      const dist = Math.max(0, document.documentElement.scrollHeight - (window.scrollY + window.innerHeight));
      let m = 1;
      if (dist < window.innerHeight * 0.8) m = 4;
      else if (dist < window.innerHeight * 1.6) m = 3;
      else if (dist < window.innerHeight * 2.6) m = 2;
      target = Math.max(target, rendered + inflight + c.step * m);
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
    }}, {{ root: null, rootMargin: "5200px 0px", threshold: 0 }});
    io.observe(sentinel);

    let scrollTick = 0;
    window.addEventListener("scroll", () => {{
      if (paused) return;
      if (scrollTick) return;
      scrollTick = 1;
      requestAnimationFrame(() => {{
        scrollTick = 0;
        const dist = Math.max(0, document.documentElement.scrollHeight - (window.scrollY + window.innerHeight));
        if (dist < window.innerHeight * 3.2) bump();
      }});
    }}, {{ passive: true }});

    // Initial fill.
    target = cfg().initial;
    ensure();
    updateSentinel();

    window.addEventListener("resize", () => {{
      const c = cfg();
      target = Math.max(target, c.initial);
      if (viewMode === "masonry" && masonryCount !== desiredMasonryCount()) {{
        rebuildLayout();
      }}
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
