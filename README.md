# Random Mage (new-pixiv-api)

自托管的 Pixiv 随机图片服务：将原图链接导入本地数据库后，通过 `GET /random` 按标签、热度、分辨率、R18、AI、作品类型等条件组合筛选并随机出图。项目同时提供管理后台（Web UI）与后台 Worker（导入/补全/代理探测等任务）。

官方演示站：
- `https://i.mukyu.ru/random`
- `https://i.mukyu.ru/docs`
- `https://i.mukyu.ru/api/docs`

> 免责声明：本项目为非官方实现。请遵守 Pixiv ToS、版权与当地法律法规；不要将 `refresh_token`、密钥、代理密码提交到仓库。

## 近期功能更新（基于最近提交）

- `ec9d373`：增强 `/wtf` 顶部筛选布局，支持标签排除筛选、`proxy` 参数自定义反代、选中反馈与单击放大。
- `ef66583`：支持导入 PixivBatchDownloader 导出的 `.json`，并默认关闭该格式的导入后补全。
- `9e437d8` + `80cd41f` + `5159d82` + `53e0890`：持续优化瀑布流页，新增标准/小格子两种展示模式。
- `6a843ce`：新增公开状态页 `/status` 与 JSON 版本 `/status.json`，并持久化随机请求总量。
- `638a285` + `ee0549b`：增强 pximg 反代能力（含 pixiv.cat / 镜像），改进 docs/status 页面与并发场景稳定性。
- `810b2da`：修复大量导入下的数据库冲突问题。

## 项目能力总览

### 1) 公开 API

| 接口 | 说明 |
| --- | --- |
| `GET /random` | 随机取图（默认图片流），支持 `format=json/simple_json` 与复杂过滤条件 |
| `GET /images` | 分页查询入库图片，支持标签、尺寸、时间等筛选 |
| `GET /images/{image_id}` | 查询单张图片及标签 |
| `GET /i/{image_id}.{ext}` | 本地代理/缓存路径（`/random?redirect=1` 跳转到这里） |
| `GET /{illust_id}.{ext}` / `GET /{illust_id}-{page}.{ext}` | 兼容旧式直链路径 |
| `GET /tags` / `GET /authors` | 标签与作者检索 |
| `GET /healthz` | 健康检查（DB、Worker 心跳、队列状态） |
| `GET /version` | 版本、构建时间、commit |
| `GET /docs` | 人类可读文档页 |
| `GET /status` / `GET /status.json` | 公开运行状态（图库统计 + 随机请求统计） |
| `GET /wtf` | 瀑布流浏览页（标准/小格子视图） |
| `GET /api/docs` / `GET /openapi.json` | Swagger/OpenAPI |

`/random` 的常用能力：
- 输出模式：`format=image|json|simple_json`，`redirect=1`。
- 策略：`strategy=quality|random`，`quality_samples`，可配置 recommendation 权重与倍率。
- 过滤：`r18`、`r18_strict`、`ai_type`、`illust_type`、`orientation/layout`。
- 阈值：`min_width`、`min_height`、`min_pixels`、`min_bookmarks`、`min_views`、`min_comments`。
- 标签：`included_tags`、`excluded_tags`（支持 `|` 表示组内 OR）。
- 其他：`user_id`、`illust_id`、`created_from`、`created_to`、`seed`、`attempts`、`adaptive=1`。

标签语义说明：
- 组间是 AND：`included_tags=a&included_tags=b`
- 组内是 OR：`included_tags=a|b`

### 2) 管理后台（`/admin` + `/admin/api/*`）

核心模块：
- 登录鉴权：JWT 登录/登出。
- 导入中心：支持文本粘贴、`.txt` 上传、PixivBatchDownloader `.json` 上传、预检查（dry-run）、回滚。
- 图片管理：分页筛选、删除、批量删除、清库。
- 补全管理：创建/手动触发补全任务、暂停/恢复/取消。
- 任务队列：查看任务、重试、取消、转入 DLQ。
- 令牌管理：Pixiv refresh_token 增删改查、测试刷新、失败重置。
- 代理体系：代理节点导入/编辑/清理/探测，代理池维护。
- 绑定关系：Token 与代理池绑定重算、覆盖、清除覆盖。
- 系统设置：随机默认策略、代理路由模式、图片反代行为、安全开关等。
- 审计日志、汇总统计、公共 API Key 管理、请求日志清理。

### 3) Worker（后台任务执行）

内置任务类型包括：
- `import_images`
- `hydrate_metadata`
- `heal_url`
- `proxy_probe`
- `easy_proxies_import`

关键特性：
- 按 `enabled token` 数自动调整并发（可关闭）。
- 写入 `worker.last_seen_at` 心跳到运行时设置。
- SQLite busy 重试与并发参数调优，降低高并发导入冲突概率。

## 快速部署（Docker Compose，推荐）

前置：`Docker` + `Docker Compose`。

1. 准备环境变量

```bash
cp deploy/.env.example deploy/.env
```

2. 编辑 `deploy/.env`（至少修改以下项）
- `SECRET_KEY`
- `ADMIN_PASSWORD`
- `FIELD_ENCRYPTION_KEY`（生产环境必须提供）

3. 启动

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

4. 访问
- API：`http://localhost:23222`
- 文档页：`http://localhost:23222/docs`
- 状态页：`http://localhost:23222/status`
- Swagger：`http://localhost:23222/api/docs`
- 管理后台：`http://localhost:23222/admin`

## 首次使用建议流程

1. 登录管理后台：使用 `ADMIN_USERNAME` / `ADMIN_PASSWORD`。
2. 导入图片链接（导入页）：
   - 方式 A：粘贴多行原图 URL。
   - 方式 B：上传 `.txt`（每行一个 URL）。
   - 方式 C：上传 PixivBatchDownloader `.json`（会尽量提取并写入元数据/标签，默认不再额外补全）。
3. （可选但建议）配置 Pixiv refresh_token：用于后续补全/刷新元数据。
4. 观察状态：
   - `GET /healthz`：看 `worker_ok` 与队列状态。
   - 管理后台任务页：看导入与补全进度。

## 关键配置项（节选）

以 `deploy/.env.example` 为准，常用项如下：

| 变量 | 作用 |
| --- | --- |
| `APP_ENV` | 运行环境（`dev` / `prod`） |
| `DATABASE_URL` | 数据库连接（默认 SQLite） |
| `SECRET_KEY` | JWT / API Key 哈希密钥 |
| `FIELD_ENCRYPTION_KEY` / `FIELD_ENCRYPTION_KEY_FILE` | 敏感字段加密密钥 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | 后台账号密码 |
| `PIXIV_OAUTH_CLIENT_ID` / `PIXIV_OAUTH_CLIENT_SECRET` / `PIXIV_OAUTH_HASH_SECRET` | Pixiv OAuth 参数 |
| `WORKER_AUTO_CONCURRENCY` / `WORKER_MAX_CONCURRENCY` | Worker 并发策略 |
| `IMGPROXY_*` | imgproxy 签名 URL 能力 |
| `PUBLIC_API_KEY_REQUIRED` / `PUBLIC_API_KEY_RPM` / `PUBLIC_API_KEY_BURST` | 公开接口 API Key 与限流 |

高级运行参数（按需）：
- `SQLITE_BUSY_TIMEOUT_MS`、`SQLITE_POOL_SIZE`、`SQLITE_MAX_OVERFLOW`、`SQLITE_POOL_TIMEOUT_S`
- `IMPORT_MAX_BYTES`、`IMPORT_INLINE_MAX_ACCEPTED`
- `RANDOM_TOTALS_PERSIST_INTERVAL_SECONDS`
- `WORKER_HEARTBEAT_INTERVAL_SECONDS`、`WORKER_HEARTBEAT_STALE_SECONDS`

## 本地开发

### 后端（Python 3.11）

```bash
cd backend
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r ..\requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

另开一个终端运行 Worker：

```bash
cd backend
.\.venv\Scripts\activate
python -m app.worker
```

### 前端（Node 20）

```bash
cd frontend
npm ci
npm run dev
```

如需前端直连本地后端，可设置 `VITE_API_BASE_URL=http://localhost:8000`。

## 测试与检查

- 后端：`python -m pytest -q backend/tests`
- 前端：`cd frontend && npm test`
- 一键脚本：
  - `scripts/test_backend.ps1`
  - `scripts/test_frontend.ps1`
  - `scripts/test_all.ps1`

## 目录结构（简版）

```text
.
├─ backend/
│  ├─ app/
│  │  ├─ api/          # public + admin 路由
│  │  ├─ core/         # 配置、安全、代理路由、指标
│  │  ├─ db/           # 模型与查询
│  │  ├─ jobs/         # 队列、调度、任务处理器
│  │  └─ worker.py
│  └─ tests/
├─ frontend/           # React + Vite 管理后台
├─ deploy/             # compose 与环境变量示例
└─ scripts/            # 测试与辅助脚本
```

## 合规与安全建议

- 切勿将任何真实令牌或密钥提交到仓库。
- 公网部署建议开启 `PUBLIC_API_KEY_REQUIRED` 并配置限流。
- 生产环境务必设置强密码与独立密钥。

