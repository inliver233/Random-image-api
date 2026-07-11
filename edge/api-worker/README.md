# random-image-api-proxy (Cloudflare Worker)

自建 **Pixiv API 出口池**（OAuth refresh + App API hydrate）。  
模式对齐 [ds2api CF worker](https://github.com/inliver233/ds2api/tree/new/proxy)：用 CF 边缘出口 IP 多样性替代「每请求住宅代理」；住宅代理仅作 backend fallback。

与 `edge/img-worker`（出图签名路径 + Cache）职责分离：本 Worker **不缓存**、**不签路径**，只做 allowlist 反代。

## URL 约定

```
https://{worker}/p/{host}/{path}?query
```

例：

```
https://api-edge.example.com/p/app-api.pixiv.net/v1/illust/detail?illust_id=1&filter=for_android
https://api-edge.example.com/p/oauth.secure.pixiv.net/auth/token
```

- `host` 必须在 `ALLOWED_HOSTS` 白名单（默认 Pixiv OAuth + App API）
- 可选门禁：请求头 `X-Proxy-Secret: <PROXY_SECRET>`（与 backend `CF_API_PROXY_SECRET` 一致）

## 部署

```bash
cd edge/api-worker
npm install
npx wrangler secret put PROXY_SECRET   # 推荐
npx wrangler deploy
```

多出口：同一脚本部署多个 worker 名/域名，backend 用 CSV：

```
CF_API_PROXY_BASE_URLS=https://api-a.example.com,https://api-b.example.com
```

## 与后端集成

`deploy/.env`：

```
CF_API_PROXY_ENABLED=true
CF_API_PROXY_BASE_URLS=https://api-edge.example.com
CF_API_PROXY_SECRET=<same as wrangler PROXY_SECRET>
```

启用后 hydrate 的 OAuth refresh 与 `/v1/illust/detail` **优先**经 CF 出口；失败再回退住宅代理池（若 runtime 仍配置了 proxy）。

## 安全

- **无** `?url=` 任意目标（防 open proxy）
- Host 精确白名单
- 剥离 `CF-*` / `X-Forwarded-*` / Cookie
- 响应 `Cache-Control: no-store`
- 不在边缘存放 Pixiv refresh/access token（token 只在 backend）

## 观测

| 信号 | 来源 |
| --- | --- |
| Worker 健康 | `GET /healthz` → `allowed_hosts`, `secret_required` |
| 上游 host | 响应头 `X-Proxy-Host` |
| BFF 配置 | Admin `GET /admin/api/maintenance/cf-api-proxy` 或 `/healthz` → `modules.cf_api_proxy` |
