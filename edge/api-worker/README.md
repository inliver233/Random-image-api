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
- **必填门禁（fail-closed）**：请求头 `X-Proxy-Secret: <PROXY_SECRET>`（与 backend `CF_API_PROXY_SECRET` 一致）。`PROXY_SECRET` 为空时 `/p/*` 一律 403。
- 可选 isolate 限流：`RATE_LIMIT_RPM`（默认 600；`0` 关闭）、`RATE_LIMIT_BURST`

## 部署

```bash
cd edge/api-worker
npm install
npx wrangler secret put PROXY_SECRET   # required (fail-closed)
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
- `PROXY_SECRET` 必填（空 secret = 拒绝所有 `/p/*`）
- Isolate 级 token-bucket 限流（默认 600 rpm；多 POP 容量叠加）
- 剥离 `CF-*` / `X-Forwarded-*` / Cookie
- 响应 `Cache-Control: no-store`
- 不在边缘存放 Pixiv refresh/access token（token 只在 backend）

## 部署脚本

```powershell
# 仓库根
.\scripts\edge\deploy-api-worker.ps1
# 多出口别名
.\scripts\edge\deploy-api-worker.ps1 -Name random-image-api-proxy-a
.\scripts\edge\deploy-api-worker.ps1 -Name random-image-api-proxy-b -SkipSecrets

# 健康 + 白名单门禁探测（不翻转 BFF 开关）
python scripts\edge\probe-api-proxy.py --base-url https://… --healthz
python scripts\edge\probe-api-proxy.py --bases https://a.example,https://b.example --secret $env:CF_API_PROXY_SECRET --healthz --proxy-path --out api-proxy-matrix.json
```

Cutover 顺序（ops only，生产 flag 默认关）：

1. Deploy Worker(s) + `PROXY_SECRET`
2. Probe `healthz` 全部 `service_ok`
3. Backend：`CF_API_PROXY_BASE_URLS` + `CF_API_PROXY_SECRET` 与 Worker 一致
4. 再设 `CF_API_PROXY_ENABLED=true`；Admin `/admin/api/maintenance/cf-api-proxy` 确认 ready
5. 回退：关 `CF_API_PROXY_ENABLED` → hydrate 走住宅代理池

## 观测

| 信号 | 来源 |
| --- | --- |
| Worker 健康 | `GET /healthz` → `allowed_hosts`, `secret_required` |
| 上游 host | 响应头 `X-Proxy-Host` |
| BFF 配置 | Admin `GET /admin/api/maintenance/cf-api-proxy` 或 `/healthz` → `modules.cf_api_proxy` |
| 探测脚本 | `scripts/edge/probe-api-proxy.py` |
