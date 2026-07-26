# 2026-07-26 全面复核基准(校准审计)

> 分支:`dev` @ `08862c0e3b653d47cbbf48a9a2f42d4a53bf165b`(与 origin/dev 一致)
> main/dev merge-base:`565946a2b0c59efbc6b03e8e885f989d6ab44615`;dev 领先 main 378 提交
> 审计方式:35 个只读子代理 + 对抗性复核 + 本机门禁实跑 + 公网 curl 探测
> 对照基线:根目录 `codexreview.md`(49 项 B1-B8/H1-H20/M1-M21)与 `docs/codexreview-remediation-plan.md`
> 本文档是后续所有修复任务的**唯一状态基准**;文档自报状态标签一律以本文为准。每完成一项须同步更新本文与 remediation plan,禁止无证据勾完成。

---

## 0. 总口径

复核基线(2026-07-26 上午,tip `08862c0`):codexreview 49 项 ≈ **7 项真解决 / 10 项部分完成 / 32 项未动**。

DoD 阶段 A(出口与体验)已达成;B(Engine 爬坡)/ C(Redis 多实例)/ D(PG 真切)/ E(队列策略)未达成。

### 0.1 本轮修复进展(2026-07-26,基线之后)

阶段 0 门禁全绿已完成:GAP-1(`e35a382`)、前端类型/lint(`0c6b366`)、13 个 vitest 漂移(`cfa45c8`)、5 个后端漂移 + 2 个顺序依赖(`71f1751`/`042ac4f`)、CI dev+Go(`7f6e8cd`)。实测:backend 813 passed/1 skipped(PG 门控),frontend lint/typecheck 0 errors、vitest 73/73、build PASS。

阶段 1 已闭环(代码侧):B2(`3e784b7`)、B3(`80af2b9`)、H5(`f936aee`)、H6(`9a6ffab`)、M4/M5(`b8aaf1d`)、M6(`d1757e4`)、M7(`ccb37b9`)、M8(`25a53c8`)、M9(`ebb5fa4`)。阶段 1 剩余:H7 完整 ETL、PG-CUTOVER-1..4(需 Docker)。Live PG 证据(真机迁移/并发 claim/争用/trgm 计划)仍被 Docker daemon 不可用阻塞,测试以 `TEST_POSTGRES_URL` 门控,命令记录于各测试 docstring。**M8 运维影响:线上 SQLite 生产部署在滚动到含 `25a53c8` 的镜像前必须设 `ALLOW_PROD_SQLITE=true`。**

## 1. 公网现状(2026-07-26 实测)

`review出现偏差.txt` 与旧记忆中"公网仍 9e6c958、OPS-ROLL 阻塞"的记载**已过期**:

- `GET https://i.mukyu.ru/version` → git_commit=`08862c0`(等于 dev tip,已滚动)
- `GET /random` → 200 本域直出,`x-image-edge: stream`(H0 语义线上生效)
- `GET /random?redirect=1` → 302 到 workers.dev(符合设计)
- `/healthz`:engine enabled=false traffic=0;catalog/tags/job_queue=sqlite;rate_limit/dedup=memory;image_edge ready=true

即 §U 的 OPS-ROLL 已完成。**"公网滚动"任务不再存在。**

## 2. 门禁实测(2026-07-26)

| 门禁 | 结果 |
|---|---|
| backend `py -3.11 -m pytest backend/tests -q` | **791 passed / 7 failed**(约 226s) |
| frontend lint | **3 errors**(TagsPage.test.tsx 未用变量等) |
| frontend typecheck | **5 errors**,其中 `src/pages/RecommendationPage.tsx:92` 缺 `x_api_key` 为生产源码真类型错,其余为测试 mock 类型 |
| frontend vitest | **13 failed / 60 passed**(全部测试漂移/脆弱选择器) |
| frontend build | PASS,但单 chunk 1.30 MB(gzip ~407 KB) |
| Go gofmt/vet/test/build | 全绿;`go test -race` 被本机 GCC 不支持 64-bit cgo 阻塞(记录,CI 补) |
| CI | push 只监听 main/test,不监听 dev;无 Go job/coverage/镜像构建/PG service |

### 2.1 backend 7 个失败分类

**5 个语义漂移**(产品语义是新的、故意的,旧测试没对账;修法=按新语义改断言,禁止 skip):

1. `test_error_codes.py::test_error_codes_no_token_available_defers_hydrate_job` — B4 已故意改 no-token 为 permanent
2. `test_admin_hydration_runs_actions.py::test_admin_hydration_run_pause_resume_cancel` — no-token 现在 409
3. `test_admin_modular_ports_status.py` — OpenAPI 文案断言 1 个
4. `test_docs_page_debug.py` — OpenAPI 文案断言 1 个
5. `test_public_api_key_required.py` — OpenAPI 文案断言 1 个

**2 个顺序依赖**:`test_log_redaction.py` 两条全量跑失败、单文件跑 11 passed(测试隔离问题,需恢复 handler 状态)。

## 3. 49 项状态矩阵(2026-07-26 独立复核)

### 3.1 已真实解决(有代码+测试证据,勿重开)

| 项 | 证据 |
|---|---|
| B1 生产弱密钥拒绝 | `9c95eef`,config.py 启动强校验+测试 |
| B4 hydrate 瞬时错误不进永久 DLQ | `0ce6191` |
| B5 导入归属不可变+回滚墓碑+Engine 事件收敛 | `9e472f4` |
| B8 日志脱敏覆盖子 logger/Uvicorn handler | `00d76b2` — **但引入新回归 GAP-1,见 §5** |
| H4 默认随机查询 `(status,x_restrict,random_key)` 索引+EXPLAIN 断言 | `70f439c`/`c1ff93a`(PG 侧证据仍随 B2) |
| H11 img-worker 冷路径流式化+HEAD 元数据化+singleflight | `fe7f3cf`,76 tests(Miniflare/Range/真网证据仍缺) |
| H12 代理 URI cache 全指纹 key+取消安全租约 | `3072c3e`/`75b9cd3` |

规划项已完成:P1-1 random.py 拆分(1425→280 行,逻辑入 core/random_* 十模块)、P1-3 过滤子句单源(`backend/app/db/pick_filters.py`)、P1-9 出图代理选择单点(`backend/app/core/proxy_mirror.py`)、P2-1 /feed 批量(`f90b29a`)、P2-2 quality_samples≤64 硬闸、P2-3 runtime_settings 2s TTL 缓存、P2-6 Redis API key 限流、§T 全部 DONE 项(H0/TAGS-1/ENGINE-1 keyset/REDIS-1/TOKEN-IMPORT/CF 默认拆分/QUEUE-1 purge/QUEUE-2 fail-loud,8/8 抽查属实)。

### 3.2 诚实的部分完成(继续推进,按剩余缺口)

| 项 | 已做 | 剩余缺口 |
|---|---|---|
| B6 Engine 快照安全 | partial 禁入、manifest+count+hash、state_version CAS(`3f09bec`/`8d6aba9`) | durable outbox、事件 sequence/gap/replay、重启持久化恢复 |
| B7 CF 信任边界 | 任意 base 注册被拒、逐跳 redirect 校验、部署 intent 状态机(`f92381d`/`257c8d4`/`b9d07f4`/`0abbf4d`) | 认证 challenge、可恢复 register/delete、DNS/rebinding 控制 |
| H6 PG 重试 | 消息匹配扩展 | SQLAlchemy 包装的 generic DBAPIError(SQLSTATE 40001/40P01)仍无效 |
| H8 BFF 池隔离 | data/control 连接池已拆(`52ebc9b`) | 字节面仍全部过 Python BFF,未卸载到 CF route/service binding |
| H15 部署双写 | durable identity+intent | delete/register 的非原子双写未动 |
| H20 CI | 5 个 job 存在 | push 不监听 dev、无 Go job、无 coverage、无镜像构建、无 PG service |
| M8 | 占位值校验 done | prod 未禁 SQLite |
| M18 | 契约补字段 done | r18 描述仍与实现相反 |
| M19 | 过滤边界 parity 测试大量补齐 | seed 与 PRNG 仍不统一 |
| M21 | README Engine 诚实性 done | cutover checklist 与 image-edge 契约 metrics 表仍漂移 |
| 规划 P0-2/P0-3 | executor 终态栅栏+协作取消(`7365d36`/`0ac8d89`+测试) | 无 per-claim fencing token(worker_id=进程 pid,cancel→retry 1s 内同 worker 重 claim 时僵尸写入可穿透栅栏);renew 持续异常超 TTL 无自弃;self-requeue 分支无专项测试 |
| 规划 P0-5 | prod 校验 done | SECRET_KEY 仍一钥两用(JWT 签名 + API key HMAC pepper),拆分未动 |

### 3.3 完全未动(主工作量)

**Blocker/High:**

- **B2**:`backend/alembic/env.py:30-31` 仍把 `+asyncpg`/`+psycopg` 剥成裸 `postgresql://`(默认 psycopg2),而 requirements 全家没有任何 sync PG 驱动 → **PG 迁移在任何环境都 ModuleNotFoundError。这是代码缺口不是环境缺口**(此前 BLOCKED_ENV 结论已纠正为错误)。
- **B3**:claim 无 `FOR UPDATE SKIP LOCKED`,PG 双 worker 可重复领取;无双连接并发测试。
- **H1**:Go pick 仍读锁内 O(N) 全扫+打分;无 bitmap/posting、无 immutable snapshot 指针、无 benchmark。
- **H2**:每 event 批次写锁内全索引重建;`RANDOM_ENGINE_SYNC_ENABLED` 独立开关未落地(仍 URL 门控)。
- **H3**:snapshot 仍整包 JSON 一次性 POST,无 begin/chunk/commit,62 万级多重内存峰值。
- **H5**:`driver_param_marker` 的 `%s` 在三个调用点(worker 心跳读取、admin summary、persisted random totals)asyncpg 下必炸且被 except 吞掉;现有测试反而把 `%s` 固化为预期。
- **H7**:legacy 工具不含 tags/image_tags 完整迁移、imports id 映射、COPY、checkpoint/resume、count/hash/orphan 校验。
- **H9/H10**:多 base 在线 failover 未接入生产链路;Worker 基础设施错误仍污染图片 fail_count。
- **H13/H14**:gate 状态与业务状态混淆、无总 deadline、OAuth POST 可重放;R2 enqueue 仍同步串行且 2xx 掩盖 partial failure。
- **H16**:无 /livez /readyz 拆分;指标进程隔离。
- **H17**:admin api-keys 深链 404(`backend/app/web/admin_ui.py:35` 的 `p.startswith("api")` 误伤 `api-keys`)。
- **H18**:Settings 批量保存逐 key 提交非事务。
- **H19**:ProxyPools 分页丢草稿+weight=0 被改 1。

**Medium(M1-M7/M9-M17/M20 全部未动):** Port 泄漏 ORM(M1)、DatabaseJobQueue 标签(M2)、payload 本机文件(M3)、ORM/migration 漂移(M4)、0019 无去重预检(M5)、PG %LIKE%(M6)、admin images 全表聚合(M7)、SQLite 并发预算(M9)、totals last-writer-wins(M10)、process-local 状态岛(M11)、Dashboard 假绿(M12)、pool #1 硬编码(M13)、无代码分割(主 JS 1.30 MB)+隐藏面板轮询(M14)、God Component(M15)、巨型函数(M16)、blanket except(M17)、runtime secrets 明文 JSON(M20)。

M12 补充证据(2026-07-26 前端测试对账时确认,来自 DashboardPage.tsx 实读):瘦身已删除且无替代面的可观测性信号——`dual-secret`(image_edge 轮换态)、逐服务 CF tag 的 `bases=N`/`no-secret` 原因、R2 `no-url` vs `no-secret` 区分、`engine index empty`、circuit 倒计时(`open ~13s`);engine `no-url` 降级为 hover tooltip。**假绿实锤:`DashboardPage.tsx:591-594` 两个 CF 池只要有一个 ready(n>0)合并 tag 即渲染绿色**,partial CF failure 显示整体健康。修 M12 时需恢复这些信号或给出等价 status 面。

**规划残留:**

- P0-4:审计中间件 `backend/app/main.py:461-462` 仍裸 `except Exception: pass`,且 `main.py:427` 只记 <400 的成功写操作(失败不入审计)。
- P1-7:wtf/status/docs HTML-in-Python 巨石**反而变胖**:wtf_page.py 1767→2019、status_page.py 546→885。
- P2-9:import 大 JSON 仍整文件进内存。
- P3-4:JWT 无吊销/刷新。

## 4. God 文件行数账本(2026-07-26 实测)

规则:**新功能禁止再往这些文件里堆;>800 行文件改动前先拆。** 仅 random.py 真瘦身;11 个后端旧巨石合计 -4.3%(几乎全来自 random.py),前端 7 页面合计 +9%。

| 文件 | 行数 |
|---|---:|
| `backend/app/api/public/wtf_page.py` | 2019 |
| `services/random-engine/cmd/random-engine/main.go` | 1760 |
| `backend/app/jobs/handlers/hydrate_metadata.py` | 1470 |
| `frontend/src/pages/MaintenancePage.tsx` | 1340 |
| `backend/app/api/admin/maintenance.py` | 1006 |
| `edge/img-worker/src/index.js` | 930 |
| `backend/app/api/admin/cf_workers.py` | 895 |
| `backend/app/api/public/status_page.py` | 885 |
| `frontend/src/pages/ProxiesPage.tsx` | 818 |
| `frontend/src/pages/PlaygroundPage.tsx` | 706 |
| `frontend/src/pages/CfWorkerPage.tsx` | 687 |
| `frontend/src/pages/DashboardPage.tsx` | 676 |
| `frontend/src/pages/RecommendationPage.tsx` | 642 |
| `frontend/src/pages/ProxyPoolsPage.tsx` | 572 |
| `backend/app/api/public/docs_page.py` | 558 |
| `backend/app/main.py` | 529 |
| `backend/app/api/public/random.py` | 280(拆分成功范例) |

共享层已存在:前端 `src/admin`+hooks 被 11 页复用;后端 core 61 模块。

## 5. 本次复核新发现问题(原 49 项清单没有,纳入任务)

| ID | 问题 | 证据 |
|---|---|---|
| GAP-1 | **B8 修复引入真回归(已本机复现)**:`backend/app/core/logging.py:14` RedactFilter 把 `record.args=()` 清空,而 uvicorn(0.40.0 pinned) AccessFormatter.formatMessage 强制解包 args 为 5 元组 → 装了过滤器后 uvicorn access 日志整条消失(ValueError 被 logging 吞掉)。修法:对 uvicorn.access 的 5 元组 args 逐元素 redact 而非清空,补"access 日志仍然可见且已脱敏"回归测试 | `logging.py:14` |
| GAP-2 | **admin 登录无爆破防护**:`/admin/api/auth/login` 可无限试口令,api_key 限流器显式豁免 `/admin` 前缀;JWT 存 localStorage;全站无 CSP/安全响应头 | auth/login 路由 + 限流器豁免逻辑 |
| GAP-3 | **审计/限流信任裸 X-Forwarded-For**,直连客户端可伪造 IP 写进审计表;compose 中 api 绑 0.0.0.0:23222 而 pg/redis/engine 绑 127.0.0.1(不对称);backend Dockerfile 无 USER(root 运行),与 random-engine 的非 root 不一致 | `main.py` `_best_effort_client_ip`、`deploy/docker-compose.yml`、`backend/Dockerfile` |
| GAP-4 | **无备份/恢复/密钥轮换故事**:FIELD_ENCRYPTION_KEY 丢失即全部加密字段永久不可解密且无轮换(IMAGE_EDGE_SECRET 反而有 previous-secret 轮换,不一致);SQLite 主库/pgdata 无备份工具或文档 | config/crypto 模块 |
| GAP-5 | **供应链**:edge 两个 Worker 无 lockfile(wrangler ^3.99.0 浮动)、CI 用 `npm install --no-audit`、无 dependabot/pip-audit、仓库无 LICENSE | `edge/*/package.json`、`.github/workflows/ci.yml:72,89` |
| GAP-6 | **仓库卫生**:工作区存在 `nul`、`backend/nul`(Windows 保留设备名,会破坏他人 checkout)、`sh.exe.stackdump`。既有未跟踪文件——不提交、不擅自删除;**建议用户自行处理:在 Git Bash 中 `rm ./nul backend/nul sh.exe.stackdump`(Windows 删除保留名需 `\\?\` 前缀或 Git Bash rm)** | `git status` |

## 6. 修复路线(依赖排序)

- **阶段 0 门禁全绿(最高优先)**:GAP-1 access 日志回归 → RecommendationPage 类型错 → 5 个漂移测试对账 + 2 个顺序依赖 → 前端 lint/vitest → CI 监听 dev + Go job。验收:backend 798 全绿、frontend 全绿、Go 全绿、CI 在 dev 真实运行。
- **阶段 1 PG 数据面**:B2 → B3 → H5 → H6 → M4/M5 → M6 → M7 → H7 → PG-CUTOVER-1..4(需 Docker;不可用则完成全部离线部分并记录精确命令)→ M8 收尾。
- **阶段 2 Engine 高性能化**:H1 → H2 → H3 → B6 收尾 → M18/M19 → 10k/100k/621k/1M benchmark → OPS-ENG 爬坡(ops)。H1-H3 完成前禁止 Engine 流量 100%。
- **阶段 3 Edge 数据面与 SLA**:H9 → H10 → H13 → H14 → H8 收尾 → H15 收尾 → H16 → M10/M11。
- **阶段 4 Admin、安全收尾与代码质量**:H17 → H18 → H19 → M12 → M13 → M14 → M15/M16(含 P1-7 静态化)→ M17 → M20 → M21;安全补强:P0-4 → P0-5 → GAP-2 登录限速 → GAP-3 XFF 策略 → fencing token(P0-2/3 残留)→ P2-9 → GAP-4 备份/轮换文档 → GAP-5 lockfile + LICENSE 决策(问用户)。

## 7. 不变量(绝不破坏)

`/random` 默认本域 200 + F5 换图;`/i` 与 legacy 直链兼容;api-worker allowlist;img HMAC;Engine ENABLED 默认 false;R2 永不默认;`JOB_QUEUE_BACKEND=redis|nats` 未实现前保持启动拒绝;SQLite 单机路径继续可用(PG 是升级不是切断);不提交任何 secrets。

## 8. 环境备忘

- Windows 11 + Git Bash;Python 用 `py -3.11`;本机 Docker daemon 此前不可用(每次接手先重新确认);`go test -race` 被本机 GCC 阻塞(CI 补)。
- 既有未跟踪文件(`.workflow/**`、`nul`、`backend/nul`、`sh.exe.stackdump`、`新建 文本文档.txt`、`review出现偏差.txt`、`任务全面详细要求.txt` 及各根目录 *.md/txt)视为他人既有工作:不删除、不覆盖、不提交。
- 只提交 dev;禁止 main、禁止 force push、禁止 `git add .`。
