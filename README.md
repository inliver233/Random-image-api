# Random Mage (new-pixiv-api)

一个自托管的随机图片 API：把 Pixiv 原图链接导入到本地数据库后，即可通过 `/random` 按 **标签 / 热度 / 分辨率 / R18 / AI / 作品类型** 等条件组合筛选并随机出图；同时提供管理后台（Web UI）+ 后台 Worker，用于导入、补全元数据、代理探测等任务。

> 免责声明：本项目为非官方实现；请遵守 Pixiv ToS/版权与当地法律法规，自行承担使用风险。不要将任何 refresh_token / 密钥 / 代理密码提交到仓库。

## 功能概览

- 公共 API
  - `GET /random`：默认直接返回图片流；也可 `format=json|simple_json` 返回 JSON；支持大量筛选参数
  - `GET /images` / `GET /images/{id}`：按条件分页查看已入库图片
  - `GET /i/{image_id}.{ext}`：本地代理/缓存路径（`/random?redirect=1` 会跳转到这里）
  - `GET /tags`、`GET /authors`、`GET /healthz`、`GET /version`、`GET /docs`（人类可读文档页）
  - Swagger / OpenAPI：`GET /api/docs`、`GET /openapi.json`
- 管理后台（`/admin`）
  - 登录后可管理：Pixiv refresh_token、代理池、导入、任务队列、运行时开关、审计/统计等
- Worker（后台任务）
  - 导入图片 URL、补全元数据（tags/宽高/浏览收藏等）、修复失效 URL、代理探测等
- 可选能力
  - 公网访问保护：启用 Public API Key + 限流
  - `imgproxy`：对外提供签名处理 URL（可隐藏 origin URL）
  - 代理路由：为上游图片/接口选择代理

## 一键部署（Docker Compose，推荐）

前置：安装 Docker + Docker Compose。

1) 准备环境变量

```bash
cp deploy/.env.example deploy/.env
```

然后编辑 `deploy/.env`，至少建议修改：

- `SECRET_KEY`：JWT/API Key 哈希用（生产环境必须修改）
- `ADMIN_PASSWORD`：后台密码（生产环境必须修改）
- `FIELD_ENCRYPTION_KEY`：用于加密保存 refresh_token（生产环境必须提供；建议用 secret 挂载或文件方式）

2) 启动

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

3) 访问

- API 基址：`http://localhost:23222`
- 健康检查：`GET http://localhost:23222/healthz`
- 文档页：`GET http://localhost:23222/docs`
- 后台 UI：`http://localhost:23222/admin`
- Swagger：`http://localhost:23222/api/docs`

## 首次使用（导入数据 + 补全元数据）

这个项目不会“自动替你抓全站图片”，你需要先把图片记录导入数据库。

1) 登录后台

- 打开 `http://localhost:23222/admin`
- 使用 `deploy/.env` 中的 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 登录

2) 导入图片原图 URL

后台提供“导入”入口，支持：

- 直接粘贴多行文本（每行一个 URL）
- 上传 `.txt` 文件（同样每行一个 URL）

导入内容应为 Pixiv 原图链接（例如 `i.pximg.net/.../img-original/...` 这类）。导入时会自动去重（按 illust_id + page）。

3) 配置 Pixiv refresh_token（可选，但强烈建议）

若你希望 Worker 自动补全/刷新元数据（tags、宽高、浏览/收藏/评论、R18 标记、AI 类型等），需要：

- 在后台添加一个或多个 refresh_token
- 配置 `PIXIV_OAUTH_CLIENT_ID` / `PIXIV_OAUTH_CLIENT_SECRET`（以及可选的 `PIXIV_OAUTH_HASH_SECRET`）

> 安全提示：refresh_token 属于敏感凭据；请只放在运行环境里（`deploy/.env` / secret 管理），不要写进代码/仓库。

4) 确认 Worker 正常

`/healthz` 会同时检查 DB、队列、以及 Worker 心跳；如果 `worker_ok=false`，通常意味着 Worker 没启动或无法连接数据库卷。

## 配置说明（节选）

所有配置均通过环境变量注入（见 `deploy/.env.example`）：

- `APP_ENV`：`dev` / `prod`（`prod` 会启用更严格的必填校验）
- `DATABASE_URL`：默认 SQLite（容器内 `/app/data/app.db`，通过 `../data:/app/data` 挂载）
- `PUBLIC_API_KEY_REQUIRED` / `PUBLIC_API_KEY_RPM` / `PUBLIC_API_KEY_BURST`：公网访问保护（可选）
- `WORKER_*`、`IMPORT_*`、`SQLITE_BUSY_TIMEOUT_MS`：Worker/导入/SQLite 运行参数
- `IMGPROXY_*`：imgproxy 集成（可选）

## 本地开发（可选）

后端（Python 3.11）：

```bash
cd backend
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r ..\requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

前端管理台（Node 20）：

```bash
cd frontend
npm ci
npm run dev
```

如需让前端直连本地后端，设置 `VITE_API_BASE_URL`（例如 `http://localhost:8000`）。

## 运行测试

- 后端：`python -m pytest -q backend/tests`
- 前端：`cd frontend && npm test`

