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
      --tile-cols: 4;
      --tile-gap: 16px;
      --masonry-gap: 0px;
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
    .btn.primary {{
      background: rgba(192, 112, 70, 0.16);
      border-color: rgba(162, 82, 44, 0.34);
    }}

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
    .muted.tight {{ font-size: 12px; }}
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
      position: relative;
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
      cursor: zoom-in;
    }}
    .item.loaded img {{
      opacity: 1;
      transform: translateY(0px);
    }}
    .item:hover img {{
      outline: 2px solid rgba(162, 82, 44, 0.42);
      outline-offset: -2px;
    }}
    .item.selected img {{
      outline: 3px solid rgba(162, 82, 44, 0.62);
      outline-offset: -3px;
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
      gap: var(--masonry-gap);
      align-items: flex-start;
      width: 100%;
    }}
    body.view-masonry .mcol {{
      flex: 1 1 0;
      display: flex;
      flex-direction: column;
      gap: var(--masonry-gap);
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
      grid-template-columns: repeat(var(--tile-cols), minmax(0, 1fr));
      gap: var(--tile-gap);
    }}
    @media (max-width: 1100px) {{
      :root {{ --tile-cols: 3; --tile-gap: 14px; }}
    }}
    @media (max-width: 760px) {{
      :root {{ --tile-cols: 2; --tile-gap: 12px; }}
    }}
    @media (max-width: 520px) {{
      :root {{ --tile-cols: 2; --tile-gap: 10px; }}
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

    details.settings {{
      margin-top: 10px;
      border: 1px solid var(--border);
      background: rgba(255, 250, 243, 0.90);
      border-radius: 12px;
      padding: 10px 10px 10px;
    }}
    details.settings summary {{
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      user-select: none;
      color: rgba(43, 29, 22, 0.92);
      font-size: 13px;
      list-style: none;
    }}
    details.settings summary::-webkit-details-marker {{ display: none; }}
    details.settings[open] summary {{ margin-bottom: 10px; }}
    .settings-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }}
    @media (max-width: 980px) {{
      .settings-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 520px) {{
      .settings-grid {{ grid-template-columns: 1fr; }}
    }}
    .field label {{
      display: block;
      margin: 0 0 5px;
      font-size: 11px;
      color: var(--muted2);
    }}
    .ctrl {{
      width: 100%;
      border: 1px solid var(--border);
      background: rgba(43, 29, 22, 0.04);
      border-radius: 10px;
      padding: 8px 10px;
      font-size: 12px;
      color: rgba(43, 29, 22, 0.92);
    }}
    textarea.ctrl {{
      min-height: 48px;
      resize: vertical;
      font-family: var(--mono);
      line-height: 1.4;
    }}
    .quick {{
      margin-top: 10px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }}
    .qchip {{
      cursor: pointer;
      border: 1px solid var(--border);
      background: rgba(255, 250, 243, 0.92);
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 12px;
      color: rgba(43, 29, 22, 0.90);
      white-space: nowrap;
    }}
    .qchip:hover {{ filter: brightness(0.98); }}
    .qchip.active {{
      border-color: rgba(162, 82, 44, 0.36);
      background: rgba(192, 112, 70, 0.12);
    }}
    .actions {{
      margin-top: 10px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }}

    .modal {{
      position: fixed;
      inset: 0;
      display: none;
      align-items: center;
      justify-content: center;
      padding: 18px;
      z-index: 9999;
    }}
    @media (max-width: 520px) {{
      .modal {{ padding: 10px; }}
    }}
    .modal.open {{ display: flex; }}
    .modal-backdrop {{
      position: absolute;
      inset: 0;
      background: rgba(24, 16, 12, 0.68);
      backdrop-filter: blur(4px);
    }}
    .modal-card {{
      position: relative;
      z-index: 1;
      width: min(1120px, 96vw);
      max-height: 92vh;
      border: 1px solid rgba(255, 255, 255, 0.14);
      background: rgba(255, 250, 243, 0.96);
      border-radius: 16px;
      box-shadow: 0 22px 60px rgba(0,0,0,0.28);
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }}
    .modal-card img {{
      width: 100%;
      height: auto;
      max-height: 82vh;
      object-fit: contain;
      background: rgba(43, 29, 22, 0.06);
    }}
    .modal-close {{
      position: absolute;
      top: 8px;
      right: 8px;
      width: 34px;
      height: 34px;
      border-radius: 10px;
      border: 1px solid rgba(58, 38, 26, 0.18);
      background: rgba(255, 250, 243, 0.92);
      cursor: pointer;
      font-size: 18px;
      line-height: 32px;
      color: rgba(43, 29, 22, 0.88);
    }}
    .modal-close:hover {{ filter: brightness(0.98); }}
    .modal-meta {{
      padding: 10px 12px 12px;
      border-top: 1px solid rgba(58, 38, 26, 0.10);
      font-size: 12px;
      line-height: 1.4;
      color: rgba(67, 51, 44, 0.78);
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .modal-meta a {{
      color: var(--link);
      text-decoration: none;
      font-family: var(--mono);
      font-size: 11px;
      padding: 2px 6px;
      border-radius: 8px;
    }}
    .modal-meta a:hover {{
      background: rgba(192, 112, 70, 0.14);
      text-decoration: none;
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

      <details class="settings" id="settings" open>
        <summary>
          <span><strong>筛选 / 布局</strong></span>
          <span class="muted tight" id="settingsHint">展开可快速调参（支持 URL 分享）</span>
        </summary>
        <div class="settings-grid" role="group" aria-label="wtf-settings">
          <div class="field">
            <label for="r18Sel">R18</label>
            <select id="r18Sel" class="ctrl">
              <option value="">默认（安全）</option>
              <option value="1">仅 R18</option>
              <option value="2">不限（含未知）</option>
            </select>
          </div>
          <div class="field">
            <label for="oriSel">方向</label>
            <select id="oriSel" class="ctrl">
              <option value="">任意</option>
              <option value="portrait">竖图</option>
              <option value="landscape">横图</option>
              <option value="square">方图</option>
            </select>
          </div>
          <div class="field">
            <label for="minPixels">最小像素（min_pixels）</label>
            <input id="minPixels" class="ctrl" type="number" min="0" step="100000" placeholder="0=不限，例如 2000000≈2MP" />
          </div>
          <div class="field">
            <label for="genderSel">快捷性别（仅 /wtf）</label>
            <select id="genderSel" class="ctrl">
              <option value="">不限</option>
              <option value="girls">只看女生（排除男生标签）</option>
              <option value="boys">只看男生（排除女生标签）</option>
            </select>
          </div>

          <div class="field" style="grid-column: span 2;">
            <label for="incTags">包含标签 included_tags（AND：换行/逗号；OR：|）</label>
            <textarea id="incTags" class="ctrl" placeholder="例如：loli&#10;girl|boy"></textarea>
          </div>
          <div class="field" style="grid-column: span 2;">
            <label for="excTags">排除标签 excluded_tags（任意命中即排除）</label>
            <textarea id="excTags" class="ctrl" placeholder="例如：@male 或 @female"></textarea>
          </div>

          <div class="field">
            <label for="mcolsSel">标准瀑布流列数（wtf_mcols）</label>
            <select id="mcolsSel" class="ctrl">
              <option value="">自动</option>
              <option value="1">1</option>
              <option value="2">2</option>
              <option value="3">3</option>
              <option value="4">4</option>
            </select>
          </div>
          <div class="field">
            <label for="mgap">标准瀑布流间距（wtf_mgap） <span class="muted" id="mgapVal"></span></label>
            <input id="mgap" class="ctrl" type="range" min="0" max="16" step="1" />
          </div>
          <div class="field">
            <label for="tcolsSel">小格子每行（wtf_tcols）</label>
            <select id="tcolsSel" class="ctrl">
              <option value="">自动</option>
              <option value="2">2</option>
              <option value="3">3</option>
              <option value="4">4</option>
              <option value="5">5</option>
              <option value="6">6</option>
            </select>
          </div>
          <div class="field">
            <label for="tgap">小格子间距（wtf_tgap） <span class="muted" id="tgapVal"></span></label>
            <input id="tgap" class="ctrl" type="range" min="8" max="28" step="1" />
          </div>
          <div class="field">
            <label for="tratioSel">小格子比例（wtf_tratio）</label>
            <select id="tratioSel" class="ctrl">
              <option value="">自动（跟随方向/自适应）</option>
              <option value="3/4">3/4</option>
              <option value="2/3">2/3</option>
              <option value="1/1">1/1</option>
              <option value="16/9">16/9</option>
            </select>
          </div>
        </div>

        <div class="quick" id="quickTags" aria-label="quick-tags">
          <span class="muted">快捷：</span>
          <button type="button" class="qchip" data-kind="inc" data-tag="loli">loli</button>
          <button type="button" class="qchip" data-kind="inc" data-tag="女の子">女の子</button>
          <button type="button" class="qchip" data-kind="inc" data-tag="ロリ">ロリ</button>
          <button type="button" class="qchip" data-kind="inc" data-tag="猫耳">猫耳</button>
          <button type="button" class="qchip" data-kind="inc" data-tag="眼鏡">眼鏡</button>
          <button type="button" class="qchip" data-kind="inc" data-tag="@female">@female（女生合集）</button>
          <button type="button" class="qchip" data-kind="inc" data-tag="@male">@male（男生合集）</button>
          <button type="button" class="qchip" data-kind="exc" data-tag="@male">排除男生</button>
          <button type="button" class="qchip" data-kind="exc" data-tag="@female">排除女生</button>
        </div>

        <div class="actions">
          <button class="btn primary" type="button" id="apply">应用并刷新</button>
          <button class="btn" type="button" id="clearTags">清空标签</button>
          <button class="btn" type="button" id="copyLink">复制当前链接</button>
          <span class="muted">提示：<code>@male</code>/<code>@female</code> 为 /wtf 端展开的“概要标签”。</span>
        </div>
      </details>
    </div>

    <div id="feed" class="feed" aria-label="feed"></div>
    <div id="sentinel" class="sentinel">加载中…</div>
  </div>

  <div id="modal" class="modal" aria-hidden="true">
    <div id="modalBackdrop" class="modal-backdrop"></div>
    <div class="modal-card" role="dialog" aria-modal="true" aria-label="预览">
      <button id="modalClose" class="modal-close" type="button" aria-label="关闭">×</button>
      <img id="modalImg" alt="" />
      <div id="modalMeta" class="modal-meta"></div>
    </div>
  </div>

  <script>
    const feed = document.getElementById("feed");
    const sentinel = document.getElementById("sentinel");
    const info = document.getElementById("info");
    const toggle = document.getElementById("toggle");
    const viewSeg = document.getElementById("viewSeg");
    const viewButtons = Array.from(viewSeg.querySelectorAll("button[data-view]"));

    const r18Sel = document.getElementById("r18Sel");
    const oriSel = document.getElementById("oriSel");
    const minPixels = document.getElementById("minPixels");
    const genderSel = document.getElementById("genderSel");
    const incTags = document.getElementById("incTags");
    const excTags = document.getElementById("excTags");
    const mcolsSel = document.getElementById("mcolsSel");
    const mgap = document.getElementById("mgap");
    const mgapVal = document.getElementById("mgapVal");
    const tcolsSel = document.getElementById("tcolsSel");
    const tgap = document.getElementById("tgap");
    const tgapVal = document.getElementById("tgapVal");
    const tratioSel = document.getElementById("tratioSel");
    const quickTags = document.getElementById("quickTags");
    const applyBtn = document.getElementById("apply");
    const clearTagsBtn = document.getElementById("clearTags");
    const copyLinkBtn = document.getElementById("copyLink");

    const modal = document.getElementById("modal");
    const modalBackdrop = document.getElementById("modalBackdrop");
    const modalClose = document.getElementById("modalClose");
    const modalImg = document.getElementById("modalImg");
    const modalMeta = document.getElementById("modalMeta");

    const qs = new URLSearchParams(window.location.search);
    const VIEW_KEY = "wtf_view";
    const WTF_GENDER_KEY = "wtf_gender";
    const WTF_MCOLS_KEY = "wtf_mcols";
    const WTF_MGAP_KEY = "wtf_mgap";
    const WTF_TCOLS_KEY = "wtf_tcols";
    const WTF_TGAP_KEY = "wtf_tgap";
    const WTF_TRATIO_KEY = "wtf_tratio";

    const TAGSET_MALE = [
      "男", "男性", "男の子", "少年", "男子", "青年", "お兄さん", "お兄ちゃん", "兄貴", "アニキ", "美少年", "イケメン", "ショタ",
    ];
    const TAGSET_FEMALE = [
      "女", "女性", "女の子", "少女", "女子", "乙女", "お姉さん", "お姉ちゃん", "美少女", "ロリ", "幼女",
    ];

    function clampInt(n, min, max) {{
      const x = Number(n);
      if (!Number.isFinite(x)) return min;
      return Math.max(min, Math.min(max, Math.trunc(x)));
    }}

    function optionalInt(raw, min, max) {{
      const s = raw == null ? "" : String(raw).trim();
      if (!s) return 0;
      const n = Number.parseInt(s, 10);
      if (!Number.isFinite(n)) return 0;
      return clampInt(n, min, max);
    }}

    function normalizeGender(raw) {{
      const v = String(raw || "").trim().toLowerCase();
      if (v === "girls" || v === "girl" || v === "female" || v === "f") return "girls";
      if (v === "boys" || v === "boy" || v === "male" || v === "m") return "boys";
      return "";
    }}

    function normalizeRatioStr(raw) {{
      const v = String(raw || "").trim();
      if (!v) return "";
      if (v === "auto") return "";
      const s = v.replace(/\\s+/g, "");
      const m = s.match(/^(\\d+(?:\\.\\d+)?)\\/(\\d+(?:\\.\\d+)?)$/);
      if (!m) return "";
      const w = Number(m[1]);
      const h = Number(m[2]);
      if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return "";
      return String(m[1]) + " / " + String(m[2]);
    }}

    function ratioToCompact(r) {{
      return String(r || "").trim().replace(/\\s+/g, "");
    }}

    const allItems = [];
    let masonryCols = [];
    let masonryHeights = [];
    let masonryCount = 0;
    let generation = 0;
    let selectedItem = null;

    let wtfGender = "";
    let masonryColsOverride = 0; // 0 = auto
    let masonryGapOverride = 0; // px
    let tileColsOverride = 0; // 0 = auto
    let tileGapOverride = 0; // 0 = auto
    let tileRatioOverride = ""; // "" = auto, else "w / h"

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
      if (Number.isInteger(masonryColsOverride) && masonryColsOverride > 0) return masonryColsOverride;
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
      const gap = Number.isFinite(Number(masonryGapOverride)) ? Number(masonryGapOverride) : 0;
      return masonryColWidth() * r + Math.max(0, gap);
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

    wtfGender = normalizeGender(qs.get(WTF_GENDER_KEY));
    masonryColsOverride = optionalInt(qs.get(WTF_MCOLS_KEY), 1, 4);
    masonryGapOverride = optionalInt(qs.get(WTF_MGAP_KEY), 0, 16);
    tileColsOverride = optionalInt(qs.get(WTF_TCOLS_KEY), 2, 6);
    tileGapOverride = optionalInt(qs.get(WTF_TGAP_KEY), 8, 28);
    tileRatioOverride = normalizeRatioStr(qs.get(WTF_TRATIO_KEY));

    const baseParams = new URLSearchParams(qs);
    baseParams.delete("view");
    baseParams.delete("format");
    baseParams.delete("redirect");
    baseParams.delete("t");
    baseParams.delete(WTF_GENDER_KEY);
    baseParams.delete(WTF_MCOLS_KEY);
    baseParams.delete(WTF_MGAP_KEY);
    baseParams.delete(WTF_TCOLS_KEY);
    baseParams.delete(WTF_TGAP_KEY);
    baseParams.delete(WTF_TRATIO_KEY);
    if (!baseParams.has("adaptive")) baseParams.set("adaptive", "1");

    let paused = false;
    let viewMode = normalizeView(qs.get("view") || storedView || "single");
    let seq = 0;
    let inflight = 0;
    let rendered = 0;
    let target = 0;
    let failStreak = 0;

    function applyLayoutVars() {{
      try {{
        const root = document.documentElement.style;
        if (masonryGapOverride > 0) root.setProperty("--masonry-gap", String(masonryGapOverride) + "px");
        else root.removeProperty("--masonry-gap");
        if (tileColsOverride > 0) root.setProperty("--tile-cols", String(tileColsOverride));
        else root.removeProperty("--tile-cols");
        if (tileGapOverride > 0) root.setProperty("--tile-gap", String(tileGapOverride) + "px");
        else root.removeProperty("--tile-gap");
      }} catch (e) {{}}

      try {{
        if (mgapVal) mgapVal.textContent = String(masonryGapOverride || 0) + "px";
      }} catch (e) {{}}
      try {{
        if (tgapVal) {{
          const raw = getComputedStyle(document.documentElement).getPropertyValue("--tile-gap");
          const v = tileGapOverride > 0 ? tileGapOverride : (Number.parseInt(String(raw || "").trim(), 10) || 0);
          tgapVal.textContent = String(v) + "px";
        }}
      }} catch (e) {{}}
    }}

    function buildUrlParams() {{
      const p = new URLSearchParams(baseParams);

      if (viewMode && viewMode !== "single") p.set("view", viewMode);
      else p.delete("view");

      if (wtfGender) p.set(WTF_GENDER_KEY, wtfGender);
      else p.delete(WTF_GENDER_KEY);

      if (masonryColsOverride > 0) p.set(WTF_MCOLS_KEY, String(masonryColsOverride));
      else p.delete(WTF_MCOLS_KEY);
      if (masonryGapOverride > 0) p.set(WTF_MGAP_KEY, String(masonryGapOverride));
      else p.delete(WTF_MGAP_KEY);

      if (tileColsOverride > 0) p.set(WTF_TCOLS_KEY, String(tileColsOverride));
      else p.delete(WTF_TCOLS_KEY);
      if (tileGapOverride > 0) p.set(WTF_TGAP_KEY, String(tileGapOverride));
      else p.delete(WTF_TGAP_KEY);
      if (tileRatioOverride) p.set(WTF_TRATIO_KEY, ratioToCompact(tileRatioOverride));
      else p.delete(WTF_TRATIO_KEY);

      return p;
    }}

    function updateUrlAndInfo() {{
      try {{
        const showParams = new URLSearchParams(baseParams);
        const baseQuery = showParams.toString();
        const extra = [];
        if (wtfGender === "girls") extra.push("只看女生");
        if (wtfGender === "boys") extra.push("只看男生");
        info.textContent = (baseQuery ? ("当前过滤: ?" + baseQuery) : "当前过滤: （无）") + (extra.length ? (" · " + extra.join(" ")) : "");
      }} catch (e) {{}}

      try {{
        const p = buildUrlParams();
        const q = p.toString();
        history.replaceState(null, "", q ? ("?" + q) : window.location.pathname);
      }} catch (e) {{}}
    }}

    function parseTagLines(raw) {{
      const text = String(raw || "");
      const parts = text.split(/[\\n,]+/g);
      const out = [];
      const seen = new Set();
      for (const part of parts) {{
        const t = String(part || "").trim();
        if (!t || seen.has(t)) continue;
        seen.add(t);
        out.push(t);
      }}
      return out;
    }}

    function expandMacroForInclude(token) {{
      const key = String(token || "").trim().toLowerCase();
      if (key === "@male" || key === "male") return [TAGSET_MALE.join("|")];
      if (key === "@female" || key === "female") return [TAGSET_FEMALE.join("|")];
      return [token];
    }}

    function expandMacroForExclude(token) {{
      const key = String(token || "").trim().toLowerCase();
      if (key === "@male" || key === "male") return TAGSET_MALE;
      if (key === "@female" || key === "female") return TAGSET_FEMALE;
      return [token];
    }}

    function setMultiParam(key, values) {{
      baseParams.delete(key);
      const seen = new Set();
      for (const v of values) {{
        const t = String(v || "").trim();
        if (!t || seen.has(t)) continue;
        seen.add(t);
        baseParams.append(key, t);
      }}
    }}

    function syncControlsFromParams() {{
      try {{
        const r18v = String(baseParams.get("r18") || "").trim();
        if (r18Sel) r18Sel.value = (r18v === "1" || r18v === "2") ? r18v : "";
      }} catch (e) {{}}
      try {{
        const ov = String(baseParams.get("orientation") || "").trim();
        if (oriSel) oriSel.value = ov || "";
      }} catch (e) {{}}
      try {{
        const pv = String(baseParams.get("min_pixels") || "").trim();
        if (minPixels) minPixels.value = pv || "";
      }} catch (e) {{}}
      try {{ if (genderSel) genderSel.value = wtfGender || ""; }} catch (e) {{}}
      try {{ if (mcolsSel) mcolsSel.value = masonryColsOverride > 0 ? String(masonryColsOverride) : ""; }} catch (e) {{}}
      try {{ if (mgap) mgap.value = String(masonryGapOverride || 0); }} catch (e) {{}}
      try {{ if (tcolsSel) tcolsSel.value = tileColsOverride > 0 ? String(tileColsOverride) : ""; }} catch (e) {{}}
      try {{
        if (tgap) {{
          const raw = getComputedStyle(document.documentElement).getPropertyValue("--tile-gap");
          const v = tileGapOverride > 0 ? tileGapOverride : (Number.parseInt(String(raw || "").trim(), 10) || 16);
          tgap.value = String(v);
        }}
      }} catch (e) {{}}
      try {{
        if (tratioSel) {{
          tratioSel.value = tileRatioOverride ? ratioToCompact(tileRatioOverride) : "";
        }}
      }} catch (e) {{}}
      try {{ if (incTags) incTags.value = baseParams.getAll("included_tags").join("\\n"); }} catch (e) {{}}
      try {{ if (excTags) excTags.value = baseParams.getAll("excluded_tags").join("\\n"); }} catch (e) {{}}

      applyLayoutVars();
      updateUrlAndInfo();
    }}

    syncControlsFromParams();

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

    function pickTileRatio() {{
      return tileRatioOverride ? tileRatioOverride : pickRatioFromParams();
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
        document.documentElement.style.setProperty("--tile-ar", pickTileRatio());
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
      updateUrlAndInfo();
    }}

    for (const b of viewButtons) {{
      b.addEventListener("click", () => {{
        applyView(String(b.dataset.view || "single"), true);
      }});
    }}
    applyView(viewMode, false);

    function buildRandomParamsForRequest() {{
      const p = new URLSearchParams(baseParams);

      try {{
        const inc = p.getAll("included_tags");
        if (inc && inc.length) {{
          p.delete("included_tags");
          for (const t of inc) {{
            const expanded = expandMacroForInclude(t);
            for (const v of expanded) {{
              const s = String(v || "").trim();
              if (!s) continue;
              p.append("included_tags", s);
            }}
          }}
        }}
      }} catch (e) {{}}

      try {{
        const rawExc = p.getAll("excluded_tags");
        const out = [];
        const seen = new Set();
        const pushOne = (v) => {{
          const s = String(v || "").trim();
          if (!s || seen.has(s)) return;
          seen.add(s);
          out.push(s);
        }};
        for (const t of rawExc) {{
          const expanded = expandMacroForExclude(t);
          for (const v of expanded) pushOne(v);
        }}
        if (wtfGender === "girls") {{
          for (const v of TAGSET_MALE) pushOne(v);
        }} else if (wtfGender === "boys") {{
          for (const v of TAGSET_FEMALE) pushOne(v);
        }}
        if (out.length || rawExc.length) {{
          p.delete("excluded_tags");
          for (const v of out) p.append("excluded_tags", v);
        }}
      }} catch (e) {{}}

      return p;
    }}

    function buildRandomJsonUrl() {{
      const p = buildRandomParamsForRequest();
      p.set("format", "simple_json");
      p.set("t", String(Date.now()) + "_" + String(seq++));
      return "/random?" + p.toString();
    }}

    function buildProxyQuery() {{
      const qp = new URLSearchParams();
      const pr = String(baseParams.get("proxy") || "").trim();
      if (pr) {{
        qp.set("proxy", pr);
      }} else {{
        const pc = String(baseParams.get("pixiv_cat") || "").trim();
        if (pc === "1") qp.set("pixiv_cat", "1");
        const mh = String(baseParams.get("pximg_mirror_host") || "").trim();
        if (mh) qp.set("pximg_mirror_host", mh);
      }}
      const s = qp.toString();
      return s ? ("?" + s) : "";
    }}

    function selectItem(item) {{
      try {{
        if (selectedItem && selectedItem !== item) selectedItem.classList.remove("selected");
      }} catch (e) {{}}
      selectedItem = item;
      try {{
        if (selectedItem) selectedItem.classList.add("selected");
      }} catch (e) {{}}
    }}

    function openModalForItem(item) {{
      if (!modal || !modalImg) return;
      if (!item) return;
      const img = item.querySelector("img");
      if (!img || !img.src) return;
      selectItem(item);

      try {{
        modalImg.src = img.src;
      }} catch (e) {{}}

      try {{
        if (modalMeta) {{
          modalMeta.textContent = "";
          const meta = item.querySelector(".meta");
          if (meta) {{
            for (const n of Array.from(meta.childNodes)) {{
              modalMeta.appendChild(n.cloneNode(true));
            }}
          }}
        }}
      }} catch (e) {{}}

      try {{
        modal.classList.add("open");
        modal.setAttribute("aria-hidden", "false");
        document.body.style.overflow = "hidden";
      }} catch (e) {{}}
    }}

    function closeModal() {{
      if (!modal) return;
      try {{
        modal.classList.remove("open");
        modal.setAttribute("aria-hidden", "true");
      }} catch (e) {{}}
      try {{ document.body.style.overflow = ""; }} catch (e) {{}}
    }}

    try {{
      if (modalBackdrop) modalBackdrop.addEventListener("click", () => closeModal());
      if (modalClose) modalClose.addEventListener("click", () => closeModal());
      window.addEventListener("keydown", (e) => {{
        if (!modal || !modal.classList.contains("open")) return;
        const k = e && e.key ? String(e.key) : "";
        if (k === "Escape") closeModal();
      }});
    }} catch (e) {{}}

    try {{
      feed.addEventListener("click", (ev) => {{
        const t = ev && ev.target ? ev.target : null;
        const img = t && t.closest ? t.closest("img") : null;
        if (!img) return;
        const item = img.closest ? img.closest(".item") : null;
        if (!item || item.classList.contains("pending")) return;
        openModalForItem(item);
      }});
    }} catch (e) {{}}

    function syncParamsFromControls(opts) {{
      const reset = opts && opts.reset === true;

      const prevMCols = masonryColsOverride;
      const prevMG = masonryGapOverride;
      const prevTCols = tileColsOverride;
      const prevTG = tileGapOverride;
      const prevTR = tileRatioOverride;
      const prevGender = wtfGender;

      try {{
        const v = String(r18Sel && r18Sel.value || "").trim();
        if (!v || v === "0") baseParams.delete("r18");
        else baseParams.set("r18", v);
      }} catch (e) {{}}
      try {{
        const v = String(oriSel && oriSel.value || "").trim();
        if (!v) baseParams.delete("orientation");
        else baseParams.set("orientation", v);
      }} catch (e) {{}}
      try {{
        const raw = String(minPixels && minPixels.value || "").trim();
        const n = raw ? Number.parseInt(raw, 10) : 0;
        if (!raw || !Number.isFinite(n) || n <= 0) baseParams.delete("min_pixels");
        else baseParams.set("min_pixels", String(Math.max(0, Math.trunc(n))));
      }} catch (e) {{}}

      try {{ wtfGender = normalizeGender(genderSel && genderSel.value); }} catch (e) {{}}

      try {{
        const inc = parseTagLines(incTags && incTags.value);
        setMultiParam("included_tags", inc);
      }} catch (e) {{}}
      try {{
        const exc = parseTagLines(excTags && excTags.value);
        setMultiParam("excluded_tags", exc);
      }} catch (e) {{}}

      try {{ masonryColsOverride = optionalInt(mcolsSel && mcolsSel.value, 1, 4); }} catch (e) {{}}
      try {{ masonryGapOverride = optionalInt(mgap && mgap.value, 0, 16); }} catch (e) {{}}
      try {{ tileColsOverride = optionalInt(tcolsSel && tcolsSel.value, 2, 6); }} catch (e) {{}}
      try {{ tileGapOverride = optionalInt(tgap && tgap.value, 8, 28); }} catch (e) {{}}
      try {{ tileRatioOverride = normalizeRatioStr(tratioSel && tratioSel.value); }} catch (e) {{}}

      applyLayoutVars();
      if (viewMode === "tiles") {{
        try {{ document.documentElement.style.setProperty("--tile-ar", pickTileRatio()); }} catch (e) {{}}
      }}
      updateUrlAndInfo();

      try {{
        if (quickTags) {{
          const incSet = new Set(parseTagLines(incTags && incTags.value));
          const excSet = new Set(parseTagLines(excTags && excTags.value));
          for (const b of Array.from(quickTags.querySelectorAll("button[data-kind][data-tag]"))) {{
            const kind = String(b.dataset.kind || "");
            const tag = String(b.dataset.tag || "");
            b.classList.toggle("active", (kind === "exc" ? excSet : incSet).has(tag));
          }}
        }}
      }} catch (e) {{}}

      if (reset) {{
        resetFeed();
        return;
      }}

      if (viewMode === "masonry" && (prevMCols !== masonryColsOverride)) {{
        rebuildLayout();
      }}
      if (prevMG !== masonryGapOverride || prevTCols !== tileColsOverride || prevTG !== tileGapOverride || prevTR !== tileRatioOverride || prevGender !== wtfGender) {{
        updateSentinel();
      }}
    }}

    try {{
      if (applyBtn) applyBtn.addEventListener("click", () => syncParamsFromControls({{ reset: true }}));
      if (clearTagsBtn) clearTagsBtn.addEventListener("click", () => {{
        try {{ if (incTags) incTags.value = ""; }} catch (e) {{}}
        try {{ if (excTags) excTags.value = ""; }} catch (e) {{}}
        syncParamsFromControls({{ reset: true }});
      }});
      if (copyLinkBtn) copyLinkBtn.addEventListener("click", async () => {{
        const text = String(window.location.href || "");
        try {{
          if (navigator.clipboard && navigator.clipboard.writeText) {{
            await navigator.clipboard.writeText(text);
            copyLinkBtn.textContent = "已复制";
            setTimeout(() => {{ copyLinkBtn.textContent = "复制当前链接"; }}, 1200);
            return;
          }}
        }} catch (e) {{}}
        try {{ window.prompt("复制链接：", text); }} catch (e) {{}}
      }});

      const quickToggle = (ta, tag) => {{
        if (!ta) return;
        const vals = parseTagLines(ta.value);
        const idx = vals.indexOf(tag);
        if (idx >= 0) vals.splice(idx, 1);
        else vals.push(tag);
        ta.value = vals.join("\\n");
      }};

      if (quickTags) quickTags.addEventListener("click", (ev) => {{
        const t = ev && ev.target ? ev.target : null;
        const b = t && t.closest ? t.closest("button[data-kind][data-tag]") : null;
        if (!b) return;
        const kind = String(b.dataset.kind || "");
        const tag = String(b.dataset.tag || "");
        if (!tag) return;
        if (kind === "exc") quickToggle(excTags, tag);
        else quickToggle(incTags, tag);
        syncParamsFromControls({{ reset: true }});
      }});

      const onFastChange = (el) => {{
        if (!el) return;
        el.addEventListener("change", () => syncParamsFromControls({{ reset: true }}));
      }};
      onFastChange(r18Sel);
      onFastChange(oriSel);
      onFastChange(genderSel);

      if (minPixels) {{
        minPixels.addEventListener("change", () => syncParamsFromControls({{ reset: true }}));
        minPixels.addEventListener("keydown", (e) => {{
          if (!e) return;
          if (e.key === "Enter") syncParamsFromControls({{ reset: true }});
        }});
      }}

      const onLayout = (el) => {{
        if (!el) return;
        el.addEventListener("change", () => syncParamsFromControls({{ reset: false }}));
      }};
      onLayout(mcolsSel);
      onLayout(tcolsSel);
      onLayout(tratioSel);
      if (mgap) mgap.addEventListener("input", () => syncParamsFromControls({{ reset: false }}));
      if (tgap) tgap.addEventListener("input", () => syncParamsFromControls({{ reset: false }}));

      const bindCtrlEnter = (ta) => {{
        if (!ta) return;
        ta.addEventListener("keydown", (e) => {{
          if (!e) return;
          if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {{
            syncParamsFromControls({{ reset: true }});
          }}
        }});
      }};
      bindCtrlEnter(incTags);
      bindCtrlEnter(excTags);

      // Init quick-tag highlight / labels.
      syncParamsFromControls({{ reset: false }});
    }} catch (e) {{}}

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

    function resetFeed() {{
      generation += 1;
      try {{ closeModal(); }} catch (e) {{}}
      try {{ selectItem(null); }} catch (e) {{}}

      paused = false;
      try {{ toggle.textContent = "暂停加载"; }} catch (e) {{}}

      inflight = 0;
      rendered = 0;
      target = 0;
      failStreak = 0;

      try {{ allItems.length = 0; }} catch (e) {{}}
      rebuildLayout();

      target = cfg().initial;
      ensure();
      updateSentinel();
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

      const myGen = generation;
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
        if (myGen !== generation) {{
          done = true;
          try {{ item.remove(); }} catch (e) {{}}
          return;
        }}
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
        if (myGen !== generation) {{
          done = true;
          try {{ item.remove(); }} catch (e) {{}}
          return;
        }}
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
        if (done) return;
        if (myGen !== generation) {{
          done = true;
          try {{ item.remove(); }} catch (e) {{}}
          return;
        }}
        try {{
          const data = await fetchRandomData();
          if (done) return;
          if (myGen !== generation) {{
            done = true;
            try {{ item.remove(); }} catch (e) {{}}
            return;
          }}
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
          if (myGen !== generation) {{
            done = true;
            try {{ item.remove(); }} catch (e) {{}}
            return;
          }}
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
          if (done) return;
          if (myGen !== generation) {{
            done = true;
            try {{ item.remove(); }} catch (e) {{}}
            return;
          }}
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
        if (myGen !== generation) {{
          done = true;
          try {{ item.remove(); }} catch (e) {{}}
          return;
        }}
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
