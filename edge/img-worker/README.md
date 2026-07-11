# random-image-edge (Cloudflare Worker)

自建 Pixiv pximg 出图边缘：签名路径 + Cache API + 固定 Referer。  
用于替代「本地 Python 反代 + 垃圾代理池 + 第三方镜像站」作为**公开出图主路径**。

## URL 约定

```
GET https://img.example.com/u/{exp}/{sig}/{b64url(path)}
```

- `path`：`i.pximg.net` 原始路径，如 `/img-original/img/.../123_p0.jpg`
- `exp`：Unix 秒级过期时间
- `sig`：`base64url(HMAC-SHA256(IMAGE_EDGE_SECRET, "{exp}\n{path}"))`

## 部署

```bash
cd edge/img-worker
npm install
npx wrangler secret put IMAGE_EDGE_SECRET
npx wrangler deploy
```

在 Cloudflare 仪表盘绑定自定义域名（推荐 `img.<your-domain>`）。

## 与后端集成

后端环境变量（见 `deploy/.env.example`）：

```
IMAGE_EDGE_ENABLED=true
IMAGE_EDGE_BASE_URLS=https://img.example.com
IMAGE_EDGE_SECRET=<same as wrangler secret>
IMAGE_EDGE_SIGN_TTL_SECONDS=604800
```

启用后，`/random?format=json|simple_json` 的 `urls.proxy` 优先返回签名边缘 URL；  
本地 `/i/{id}.{ext}` 与垃圾代理/镜像仅作 fallback。

## 安全

- 不接受 `?url=` 任意目标（防 open proxy）
- 路径前缀白名单 + 扩展名白名单
- 剥离客户端 Cookie / Authorization / CF-* 转发
- 出站固定 `Referer: https://www.pixiv.net/`

## 回退

若 CF 出口对 `i.pximg.net` 403 率过高：

1. 设置 `FALLBACK_MIRROR_HOST=i.pixiv.re`（仅应急）
2. 或切换 R2 预取模式（后续迭代）
