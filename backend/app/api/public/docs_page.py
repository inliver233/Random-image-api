from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


def _build_docs_html(*, base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if not base:
        base = ""

    def u(path: str) -> str:
        path_norm = (path or "").strip()
        if not path_norm.startswith("/"):
            path_norm = "/" + path_norm
        return f"{base}{path_norm}"

    examples = {
        "img_default": u("/random"),
        "img_proxy_cat": u("/random?proxy=i-pixiv-cat"),
        "img_pixiv_cat": u("/random?pixiv_cat=1"),
        "img_pixiv_re": u("/random?pixiv_cat=1&pximg_mirror_host=re"),
        "img_redirect": u("/random?redirect=1"),
        "status": u("/status"),
        "status_json": u("/status.json"),
        "wtf": u("/wtf"),
        "wtf_r18": u("/wtf?r18=1"),
        "json_full": u("/random?format=json"),
        "json_simple": u("/random?format=simple_json"),
        "r18_only": u("/random?r18=1"),
        "safe_only": u("/random?r18=0"),
        "any_r18": u("/random?r18=2"),
        "portrait": u("/random?orientation=portrait"),
        "landscape": u("/random?orientation=landscape"),
        "adaptive": u("/random?adaptive=1"),
        "tag_loli": u("/random?included_tags=loli"),
        "pure_random": u("/random?strategy=random"),
        "quality_strong": u(
            "/random?strategy=quality&quality_samples=200&min_pixels=2000000&min_bookmarks=100&illust_type=illust&ai_type=0"
        ),
        "complex": u("/random?r18=1&illust_type=illust&orientation=portrait&min_pixels=2500000&min_bookmarks=2&min_comments=5&included_tags=loli"),
        "tags_api": u("/tags"),
        "authors_api": u("/authors"),
        "swagger": u("/api/docs"),
        "openapi": u("/openapi.json"),
    }

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="color-scheme" content="light" />
  <title>Random Mage Docs</title>
  <style>
    :root {{
      /* Clay / paper-like palette */
      --bg: #f4efe6;
      --card: rgba(255, 250, 243, 0.78);
      --card-2: rgba(255, 250, 243, 0.92);
      --border: rgba(58, 38, 26, 0.14);
      --text: rgba(43, 29, 22, 0.94);
      --muted: rgba(67, 51, 44, 0.78);
      --muted2: rgba(67, 51, 44, 0.66);
      --link: #a2522c;
      --accent: #c07046;
      --ok: #1f7a56;
      --warn: #b45309;
      --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      --sans: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji",
        "Segoe UI Emoji";
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
      padding: 32px 20px 64px;
    }}
    @media (max-width: 520px) {{
      .wrap {{ padding: 22px 14px 46px; }}
    }}

    .hero {{
      padding: 18px 18px 16px;
      border: 1px solid var(--border);
      background: linear-gradient(180deg, rgba(255,250,243,0.92), rgba(255,250,243,0.70));
      border-radius: 14px;
      box-shadow: 0 10px 30px rgba(40, 24, 16, 0.10);
    }}

    .hero h1 {{
      margin: 0;
      font-size: 22px;
      letter-spacing: 0.2px;
    }}
    @media (max-width: 520px) {{
      .hero h1 {{ font-size: 20px; }}
    }}
    .hero p {{
      margin: 10px 0 0;
      color: var(--muted);
      line-height: 1.55;
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
      font-size: 16px;
      margin: 0 0 10px;
      letter-spacing: 0.2px;
    }}
    .card p {{
      margin: 0 0 10px;
      color: var(--muted);
      line-height: 1.55;
    }}

    .kbd {{
      display: inline-block;
      font-family: var(--mono);
      font-size: 12px;
      padding: 2px 6px;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: rgba(43, 29, 22, 0.06);
      color: rgba(43, 29, 22, 0.92);
    }}

    pre {{
      margin: 10px 0 0;
      padding: 12px 12px;
      border-radius: 12px;
      border: 1px solid var(--border);
      background: rgba(43, 29, 22, 0.06);
      overflow: auto;
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.55;
      color: rgba(43, 29, 22, 0.92);
    }}
    @media (max-width: 520px) {{
      pre {{ font-size: 11px; }}
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
    .chip strong {{ color: rgba(43, 29, 22, 0.94); }}

    .table-wrap {{
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
      border-radius: 12px;
      border: 1px solid var(--border);
      margin-top: 10px;
      background: var(--card-2);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 680px;
    }}
    th, td {{
      padding: 10px 10px;
      border-bottom: 1px solid rgba(58, 38, 26, 0.10);
      vertical-align: top;
      font-size: 13px;
      line-height: 1.45;
      word-break: break-word;
    }}
    th {{
      text-align: left;
      background: rgba(43, 29, 22, 0.04);
      color: rgba(43, 29, 22, 0.92);
      font-weight: 600;
    }}
    td code {{
      font-family: var(--mono);
      font-size: 12px;
      color: rgba(43, 29, 22, 0.92);
      word-break: break-all;
    }}
    .muted {{ color: var(--muted2); }}
    .note {{
      margin-top: 12px;
      padding: 12px 12px;
      border-radius: 12px;
      border: 1px solid var(--border);
      background: rgba(180, 83, 9, 0.10);
      color: rgba(43, 29, 22, 0.92);
    }}
    .note strong {{ color: rgba(43, 29, 22, 0.95); }}
    .footer {{
      margin-top: 18px;
      color: var(--muted2);
      font-size: 12px;
      line-height: 1.6;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>Random Mage · 随机图片 API</h1>
      <p>质量优先随机 + 强力筛选，支持标签/热度/分辨率/R18/AI/作品类型等组合查询。</p>
      <div class="chips" aria-label="quick-links">
        <a class="chip" href="{examples["img_default"]}"><strong>/random</strong> 直接出图</a>
        <a class="chip" href="{examples["wtf"]}"><strong>/wtf</strong> 瀑布流</a>
        <a class="chip" href="{examples["status"]}"><strong>/status</strong> 运行状态</a>
        <a class="chip" href="{examples["json_full"]}"><strong>format=json</strong> 返回 JSON</a>
        <a class="chip" href="{examples["pure_random"]}"><strong>strategy=random</strong> 纯随机</a>
        <a class="chip" href="{examples["swagger"]}"><strong>/api/docs</strong> Swagger</a>
      </div>
    </div>

    <div class="grid">
      <section class="card">
        <h2>1) 最常用（直接出图）</h2>
        <p>默认返回图片（不是 JSON）。想要稳定 URL 可加 <span class="kbd">redirect=1</span> 跳转到本站缓存/代理路径。</p>
        <pre><code>{examples["img_default"]}
{examples["img_pixiv_cat"]}
{examples["img_pixiv_re"]}
{examples["img_redirect"]}</code></pre>
      </section>

      <section class="card">
        <h2>2) JSON / 调试信息</h2>
        <p><span class="kbd">format=json</span> 返回完整数据（含 tags / debug）；<span class="kbd">format=simple_json</span> 更轻量。</p>
        <pre><code>{examples["json_full"]}
{examples["json_simple"]}</code></pre>
      </section>

      <section class="card">
        <h2>3) R18 / 作品类型 / AI</h2>
        <p>R18 用 <span class="kbd">r18</span> 控制：0=仅全年龄（默认），1=仅R18，2=都可。</p>
        <pre><code>{examples["safe_only"]}
{examples["r18_only"]}
{examples["any_r18"]}</code></pre>
        <div class="note">
          <strong>提示：</strong>如果你库里有不少作品还没补全 x_restrict，且你想“更容易命中”，可用
          <span class="kbd">r18=0&amp;r18_strict=0</span>（允许未知的 x_restrict）。
        </div>
      </section>

      <section class="card">
        <h2>4) 横竖图 / 自适应</h2>
        <p><span class="kbd">orientation</span> 支持：any / portrait / landscape / square。自适应用 <span class="kbd">adaptive=1</span>：移动端默认竖图+更低像素门槛，PC 默认横图+更高门槛（不会覆盖你显式传入的过滤条件）。</p>
        <pre><code>{examples["portrait"]}
{examples["landscape"]}
{examples["adaptive"]}</code></pre>
      </section>
    </div>

    <section class="card" style="margin-top: 14px;">
      <h2>5) 状态页 / 瀑布流</h2>
      <p><span class="kbd">/status</span> 为公开仪表盘：展示 API 状态、图库概览、/random 请求统计；<span class="kbd">/status.json</span> 为机器可读 JSON。</p>
      <pre><code>{examples["status"]}
{examples["status_json"]}</code></pre>
      <p><span class="kbd">/wtf</span> 为瀑布流：支持 <span class="kbd">/random</span> 的全部过滤参数（例如 r18/标签/分辨率/热度/排除标签等），默认会补上 <span class="kbd">adaptive=1</span> 以更适合不同屏幕。</p>
      <p class="muted">/wtf 额外支持布局参数：<code>view=single|masonry|tiles</code>，以及 <code>wtf_mcols</code>/<code>wtf_mgap</code>/<code>wtf_tcols</code>/<code>wtf_tgap</code>/<code>wtf_tratio</code>（仅影响页面布局）。另有 <code>wtf_gender=girls|boys</code> 与概要标签 <code>@male</code>/<code>@female</code>（仅 /wtf 端展开，用于快速“只看女生/只看男生”）。</p>
      <pre><code>{examples["wtf"]}
{examples["wtf_r18"]}</code></pre>
    </section>

    <section class="card" style="margin-top: 14px;">
      <h2>筛选语法速记</h2>
      <div class="note">
        <strong>标签 AND / OR 速记：</strong>
        <span class="kbd">AND</span> 用“重复参数”（例如 <span class="kbd">included_tags=a&amp;included_tags=b</span>），
        <span class="kbd">OR</span> 用同一参数里的 <span class="kbd">|</span>（例如 <span class="kbd">included_tags=a|b</span>）。
        <span class="muted">如果你的客户端不方便输入 <span class="kbd">|</span>，也可以写成 <span class="kbd">%7C</span>。</span>
      </div>
      <div class="table-wrap" role="region" aria-label="filters-table" tabindex="0">
        <table>
          <thead>
            <tr>
              <th style="width: 170px;">参数</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
          <tr>
            <td><code>strategy</code></td>
            <td>
              随机策略：<code>quality</code>（默认，质量优先） / <code>random</code>（纯随机）。<br/>
              <span class="muted">quality 会先抽样 N 张候选再按热度/分辨率打分选图。</span>
            </td>
          </tr>
          <tr>
            <td><code>proxy</code></td>
            <td>
              指定本次请求的图片上游镜像（优先级最高）。会隐式开启第三方镜像（等价于 <code>pixiv_cat=1</code>），并覆盖地区自动选择。<br/>
              内置值：<code>cat</code>/<code>re</code>/<code>nl</code>，也支持 <code>pixiv-cat</code>/<code>i-pixiv-cat</code> 等写法。<br/>
              自定义镜像：可填写你自建镜像域名（需在管理端“自定义镜像白名单”允许）。<br/>
              示例：<a href="{examples["img_proxy_cat"]}">{examples["img_proxy_cat"]}</a>
            </td>
          </tr>
          <tr>
            <td><code>pixiv_cat</code></td>
            <td>
              <code>0</code>/<code>1</code>：强制使用第三方反向代理拉取图片上游（即使全局未开启）。<br/>
              <span class="muted">上游可为 i.pixiv.cat / i.pixiv.re / i.pixiv.nl。</span><br/>
              <span class="muted">未指定 pximg_mirror_host 时：大陆访问优先 i.pixiv.re，非大陆默认 i.pixiv.cat。</span><br/>
              <span class="muted">仅影响服务端拉图的上游域名，客户端仍访问本站域名。</span>
            </td>
          </tr>
          <tr>
            <td><code>pximg_mirror_host</code></td>
            <td>
              指定镜像域名（可选）：<code>i.pixiv.cat</code> / <code>i.pixiv.re</code> / <code>i.pixiv.nl</code>，也支持简写 <code>cat</code>/<code>re</code>/<code>nl</code>。<br/>
              示例：<a href="{examples["img_pixiv_re"]}">{examples["img_pixiv_re"]}</a>
            </td>
          </tr>
          <tr>
            <td><code>quality_samples</code></td>
            <td>
              质量抽样数量（1–1000）。越大越“挑剔”，但 DB 开销也更高。<br/>
              示例：<a href="{examples["quality_strong"]}">{examples["quality_strong"]}</a>
            </td>
          </tr>
          <tr>
            <td><code>included_tags</code></td>
            <td>
              必须包含的标签：参数之间是 <strong>AND</strong>，单个参数内用 <code>|</code> 表示 <strong>OR</strong>。<br/>
              例：<code>included_tags=girl|boy&amp;included_tags=white|black</code> 表示 (girl OR boy) AND (white OR black)。<br/>
              <span class="muted">提示：URL 里的 <code>&amp;</code> 是“参数分隔符”，要写 AND 就重复参数；要写 OR 就在同一参数里用 <code>|</code>。</span><br/>
              示例：<a href="{examples["tag_loli"]}">{examples["tag_loli"]}</a>
            </td>
          </tr>
          <tr>
            <td><code>excluded_tags</code></td>
            <td>
              必须不包含的标签：任意命中即排除。可重复传参：<code>excluded_tags=a&amp;excluded_tags=b</code>。<br/>
              <span class="muted"><code>excluded_tags=a|b</code> 等价于排除 a 或 b（效果同上）。</span>
            </td>
          </tr>
          <tr>
            <td><code>min_width</code><br/><code>min_height</code><br/><code>min_pixels</code></td>
            <td>分辨率门槛：宽/高/像素数（例如 2000000 ≈ 2MP）。</td>
          </tr>
          <tr>
            <td><code>min_bookmarks</code><br/><code>min_views</code><br/><code>min_comments</code></td>
            <td>热度门槛：收藏/浏览/评论（需要补全元数据后更准确）。</td>
          </tr>
          <tr>
            <td><code>ai_type</code></td>
            <td><code>any</code> / <code>0</code>（非AI） / <code>1</code>（AI）。</td>
          </tr>
          <tr>
            <td><code>illust_type</code></td>
            <td><code>any</code> / <code>illust</code> / <code>manga</code> / <code>ugoira</code>。</td>
          </tr>
          <tr>
            <td><code>user_id</code></td>
            <td>只返回某个作者的作品（Pixiv 用户 ID）。</td>
          </tr>
          <tr>
            <td><code>created_from</code><br/><code>created_to</code></td>
            <td>按发布时间筛选（ISO8601，例如 <code>2024-01-01T00:00:00Z</code>）。</td>
          </tr>
          <tr>
            <td><code>seed</code></td>
            <td>固定随机种子（同参数 + 同 seed 更容易复现）。</td>
          </tr>
          <tr>
            <td><code>attempts</code></td>
            <td>上游失败时重试次数（1–10）。</td>
          </tr>
          </tbody>
        </table>
      </div>
      <div class="footer">
        其它接口：<a href="{examples["tags_api"]}">/tags</a>（标签列表）、<a href="{examples["authors_api"]}">/authors</a>（作者列表）。<br/>
        OpenAPI：<a href="{examples["openapi"]}">/openapi.json</a>，Swagger：<a href="{examples["swagger"]}">/api/docs</a>。<br/>
      </div>
    </section>

    <section class="card" style="margin-top: 14px;">
      <h2>复杂示例（组合查询）</h2>
      <p>示例：R18 + 插画 + 竖图 + 250万像素以上 + 收藏≥2 + 评论≥5 + 标签包含 loli（直接出图）。</p>
      <pre><code>{examples["complex"]}</code></pre>
    </section>

    <section class="card" style="margin-top: 14px;">
      <h2>6) 管理端导入（可选）</h2>
      <p class="muted">管理端支持从文件批量导入图片链接：.txt（每行一个 URL）或 PixivBatchDownloader 导出的 .json。</p>
      <ul>
        <li><strong>.json</strong> 导入会自动提取图片链接，并尽可能填充已有元数据/标签；不依赖 refresh token 也能使用（无需额外触发补全）。</li>
        <li><strong>.txt</strong> 导入仅包含 URL，推荐在有 refresh token 时启用导入后补全，以获得更完整的标签/尺寸/R18/AI 等信息。</li>
      </ul>
    </section>
  </div>
</body>
</html>
"""


@router.get("/docs", include_in_schema=False)
async def docs_page(request: Request) -> HTMLResponse:
    html = _build_docs_html(base_url=str(getattr(request, "base_url", "") or "").rstrip("/"))
    return HTMLResponse(content=html, status_code=200, headers={"Cache-Control": "no-store"})
