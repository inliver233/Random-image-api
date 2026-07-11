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

启用后（公开主路径）：
- `/random?format=json|simple_json` 的 `urls.proxy` → 签名边缘 URL
- `/random?format=image` / `redirect=1` → 302 到签名边缘 URL
- `/i/{id}.{ext}` → 302 到签名边缘 URL（`?local=1` 强制本地流）

本地 `/i` 流式反代、垃圾代理池、第三方镜像仅作 **fallback / 应急**（Worker 镜像链默认空）。

## 安全

- 不接受 `?url=` 任意目标（防 open proxy）
- 路径前缀白名单 + 扩展名白名单
- 剥离客户端 Cookie / Authorization / CF-* 转发
- 出站固定 `Referer: https://www.pixiv.net/`
- 双密钥轮换：`IMAGE_EDGE_SECRET` + `IMAGE_EDGE_SECRET_PREVIOUS`（仅校验旧签）

## 密钥轮换

```bash
# 1) Worker 同时持有新旧密钥
npx wrangler secret put IMAGE_EDGE_SECRET          # 新
npx wrangler secret put IMAGE_EDGE_SECRET_PREVIOUS # 旧
# 2) 后端 IMAGE_EDGE_SECRET 改为新密钥（仍只签发新密钥）
# 3) 等待 ≥ IMAGE_EDGE_SIGN_TTL_SECONDS 后删除 PREVIOUS
```

## 回退

若 CF 出口对 `i.pximg.net` 403 率过高：

1. 设置多镜像应急链（推荐）：
   ```
   FALLBACK_MIRROR_HOSTS=i.pixiv.re,i.pixiv.cat,i.pixiv.nl
   ```
   或兼容单值：`FALLBACK_MIRROR_HOST=i.pixiv.re`
2. 成功响应带 `X-Edge-Via` 标明实际出站 host
3. 软熔断：连续 origin 403 达阈值后短时跳过 origin，直连镜像链（`X-Edge-Circuit: origin-open`）
4. 切换 R2 模式（可选）：

```toml
# wrangler.toml
[[r2_buckets]]
binding = "R2"
bucket_name = "random-image-pximg"
```

```
R2_MODE=read_through   # Cache → R2 → origin；命中后异步 R2.put（默认）
R2_MODE=r2_only        # 仅 R2（需预热；上游 403 高时 SLA 模式）
R2_MODE=off            # 忽略 R2 binding
```

预热：

```
POST /v1/prewarm
X-Prewarm-Secret: <PREWARM_SECRET 或 IMAGE_EDGE_SECRET>
{ "paths": ["/img-original/img/.../x_p0.jpg"] }
```

对象键：`pximg{path}`。

## 部署脚本

```powershell
# 仓库根
.\scripts\edge\deploy-img-worker.ps1
# 单点
python scripts\edge\probe-img-edge.py --base-url https://… --secret … --path /img-original/… --twice --healthz
# 多地区矩阵 → edge-matrix.json（含 mode_suggestion；不翻转 BFF 开关）
python scripts\edge\probe-img-edge.py --bases https://a.example,https://b.example --secret … --path /img-original/… --twice --healthz --out edge-matrix.json
```

## 观测（运维）

| 信号 | 来源 |
| --- | --- |
| Cache HIT / MISS | 响应头 `X-Edge-Cache`（按 path 缓存，与签名无关） |
| 上游 / R2 | `X-Edge-Via`、`X-Edge-Storage`、`X-Edge-Circuit`；soft circuit |
| BFF 切流路径 | Prometheus `new_pixiv_image_delivery_total`（edge_redirect / local_*） |
| 配置是否可签 URL | Admin `GET /admin/api/maintenance/image-edge` 或 `/healthz` → `modules.image_edge` |
| Worker R2 | `/healthz` → `r2` / `r2_mode` |

多地区 403 POC：`--bases` 矩阵 + `summary.mode_suggestion` → 选 B / B+R2 / B2；详见 `contracts/image-edge.md` Mode decision。
