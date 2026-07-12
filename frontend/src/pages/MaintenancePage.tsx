import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Descriptions, Form, InputNumber, Skeleton, Space, Switch, Tag, Typography } from "antd";
import React from "react";

import { ActionAlerts } from "../admin/ActionAlerts";
import { messageFromError, requestIdFromError } from "../admin/errors";
import { QueryState } from "../admin/QueryState";
import { useActionAlerts } from "../admin/useActionAlerts";
import { ApiError, apiJson } from "../api/client";

/** Map known random-engine English ops messages for admin UI. */
const RANDOM_ENGINE_MESSAGE_ZH: Record<string, string> = {
  "RANDOM_ENGINE_URL not configured": "未配置 RANDOM_ENGINE_URL（Go 选图服务地址）",
  "HTTP client unavailable": "HTTP 客户端不可用",
  "random-engine snapshot failed": "Random Engine 快照推送失败",
  "random-engine filter-count failed": "Random Engine 过滤计数失败",
  "engine unreachable while dual-run traffic enabled": "双跑已开启但 Engine 不可达",
  "engine index empty — push snapshot before cutover": "Engine 索引为空 — 切流前请先推送快照",
  "traffic_percent=0 (engine not receiving picks)": "traffic_percent=0（Engine 未接收选图流量）",
};

function localizeRandomEngineMessage(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const text = String(raw).trim();
  if (!text) return null;
  return RANDOM_ENGINE_MESSAGE_ZH[text] ?? text;
}

/** Prefer raw body.message so ZH map can match before generic code translation. */
function rawMessageFromError(err: unknown): string {
  if (err instanceof ApiError) {
    const bodyMsg = err.body && typeof err.body.message === "string" ? err.body.message.trim() : "";
    if (bodyMsg) return bodyMsg;
  }
  return messageFromError(err);
}

function applyRandomEngineError(alerts: ReturnType<typeof useActionAlerts>, err: unknown): void {
  alerts.setErrorMessage(localizeRandomEngineMessage(rawMessageFromError(err)) || "操作失败", requestIdFromError(err));
}

type CleanupFormValues = {
  keep_days: number;
  max_delete_rows: number;
  chunk_size: number;
  dry_run: boolean;
};

type CleanupResponse = {
  ok: true;
  dry_run: boolean;
  cutoff: string;
  deleted?: number;
  would_delete?: number;
  has_more: boolean;
  request_id: string;
};

type RandomEngineStatusResponse = {
  ok: true;
  enabled: boolean;
  url: string;
  traffic_percent?: number;
  timeout_ms?: number;
  healthy: boolean;
  health: Record<string, unknown> | null;
  index_size?: number | null;
  index_empty?: boolean | null;
  ready_for_traffic?: boolean;
  cutover_warning?: string | null;
  request_id: string;
};

type RandomEngineSnapshotResponse = {
  ok: true;
  revision: string;
  engine: Record<string, unknown>;
  request_id: string;
};

type RandomEngineCompareResponse = {
  ok: true;
  match: boolean;
  python_filtered: number;
  engine_filtered: number;
  delta: number;
  engine_index_size: number;
  engine_revision: string;
  r18_strict: number;
  request_id: string;
};

type ImageEdgeStatusResponse = {
  ok: true;
  enabled_flag: boolean;
  ready: boolean;
  base_urls: string[];
  base_url_count: number;
  sign_ttl_seconds: number;
  has_secret: boolean;
  has_secret_previous: boolean;
  missing: string[];
  request_id: string;
};

type CfApiProxyStatusResponse = {
  ok: true;
  enabled_flag: boolean;
  ready: boolean;
  base_urls: string[];
  base_url_count: number;
  has_secret: boolean;
  missing: string[];
  request_id: string;
};

type R2PrewarmStatusResponse = {
  ok: true;
  enabled_flag: boolean;
  ready: boolean;
  url_configured: boolean;
  secret_configured?: boolean;
  payload_shape?: string;
  url_preview: string;
  missing: string[];
  request_id: string;
};

type ApiKeyRateLimitStatusResponse = {
  ok: true;
  required: boolean;
  rpm: number;
  burst: number;
  configured_backend: string;
  active_backend: string;
  redis_url_configured: boolean;
  using_memory_fallback: boolean;
  request_id: string;
};

type ModularPortsStatusResponse = {
  ok: true;
  catalog: { backend: string };
  tags: { backend: string };
  job_queue: {
    backend: string;
    requested: string;
    implemented?: boolean;
    using_sqlite_fallback?: boolean;
  };
  recent_dedup: {
    configured_backend: string;
    active_backend: string;
    using_memory_fallback: boolean;
  };
  random_service?: { backend: string };
  random_pick?: { backend: string };
  request_id: string;
};

export function MaintenancePage() {
  const [form] = Form.useForm<CleanupFormValues>();
  const alerts = useActionAlerts();
  const engineAlerts = useActionAlerts();
  const queryClient = useQueryClient();
  const [compareResult, setCompareResult] = React.useState<RandomEngineCompareResponse | null>(null);

  const imageEdgeStatus = useQuery({
    queryKey: ["admin", "maintenance", "image-edge"],
    queryFn: () => apiJson<ImageEdgeStatusResponse>("/admin/api/maintenance/image-edge"),
    refetchInterval: 30_000,
  });

  const cfApiProxyStatus = useQuery({
    queryKey: ["admin", "maintenance", "cf-api-proxy"],
    queryFn: () => apiJson<CfApiProxyStatusResponse>("/admin/api/maintenance/cf-api-proxy"),
    refetchInterval: 30_000,
  });

  const r2PrewarmStatus = useQuery({
    queryKey: ["admin", "maintenance", "r2-prewarm"],
    queryFn: () => apiJson<R2PrewarmStatusResponse>("/admin/api/maintenance/r2-prewarm"),
    refetchInterval: 30_000,
  });

  const apiKeyRlStatus = useQuery({
    queryKey: ["admin", "maintenance", "api-key-rate-limit"],
    queryFn: () => apiJson<ApiKeyRateLimitStatusResponse>("/admin/api/maintenance/api-key-rate-limit"),
    refetchInterval: 30_000,
  });

  const modularPortsStatus = useQuery({
    queryKey: ["admin", "maintenance", "modular-ports"],
    queryFn: () => apiJson<ModularPortsStatusResponse>("/admin/api/maintenance/modular-ports"),
    refetchInterval: 30_000,
  });

  const engineStatus = useQuery({
    queryKey: ["admin", "maintenance", "random-engine"],
    queryFn: () => apiJson<RandomEngineStatusResponse>("/admin/api/maintenance/random-engine"),
    refetchInterval: 15_000,
  });

  const cleanup = useMutation({
    mutationFn: (values: CleanupFormValues) =>
      apiJson<CleanupResponse>("/admin/api/maintenance/request-logs/cleanup", {
        method: "POST",
        body: JSON.stringify({
          keep_days: values.keep_days,
          max_delete_rows: values.max_delete_rows,
          chunk_size: values.chunk_size,
          dry_run: Boolean(values.dry_run),
        }),
      }),
    onMutate: () => {
      alerts.clear();
    },
    onSuccess: (data) => {
      const msg = data.dry_run ? "预览完成" : "清理完成";
      const detail = data.dry_run
        ? `cutoff=${data.cutoff}，would_delete=${data.would_delete ?? 0}，has_more=${String(data.has_more)}`
        : `cutoff=${data.cutoff}，deleted=${data.deleted ?? 0}，has_more=${String(data.has_more)}`;
      alerts.setSuccess(`${msg}（${detail}）`, data.request_id);
    },
    onError: (err) => {
      alerts.setError(err);
    },
  });

  const pushSnapshot = useMutation({
    mutationFn: () =>
      apiJson<RandomEngineSnapshotResponse>("/admin/api/maintenance/random-engine/snapshot", {
        method: "POST",
        body: JSON.stringify({}),
      }),
    onMutate: () => {
      engineAlerts.clear();
    },
    onSuccess: (data) => {
      engineAlerts.setSuccess(`快照已推送 revision=${data.revision}`, data.request_id);
      void queryClient.invalidateQueries({ queryKey: ["admin", "maintenance", "random-engine"] });
    },
    onError: (err) => {
      applyRandomEngineError(engineAlerts, err);
    },
  });

  const compareFilters = useMutation({
    mutationFn: () =>
      apiJson<RandomEngineCompareResponse>("/admin/api/maintenance/random-engine/compare-filters", {
        method: "POST",
        body: JSON.stringify({ r18: 0 }),
      }),
    onMutate: () => {
      engineAlerts.clear();
      setCompareResult(null);
    },
    onSuccess: (data) => {
      setCompareResult(data);
      const msg = data.match
        ? `过滤基数一致 python=${data.python_filtered} engine=${data.engine_filtered}`
        : `过滤基数不一致 delta=${data.delta}（python=${data.python_filtered} engine=${data.engine_filtered}）`;
      engineAlerts.setSuccess(msg, data.request_id);
      void queryClient.invalidateQueries({ queryKey: ["admin", "maintenance", "random-engine"] });
    },
    onError: (err) => {
      applyRandomEngineError(engineAlerts, err);
    },
  });

  const engine = engineStatus.data;
  const health = engine?.health && typeof engine.health === "object" ? engine.health : null;
  const indexSize =
    typeof engine?.index_size === "number"
      ? Number(engine.index_size)
      : health && typeof (health as { index_size?: unknown }).index_size === "number"
        ? Number((health as { index_size: number }).index_size)
        : null;
  const indexEmpty =
    typeof engine?.index_empty === "boolean"
      ? engine.index_empty
      : indexSize === null
        ? null
        : indexSize <= 0;
  const readyForTraffic = Boolean(engine?.ready_for_traffic);
  const engineUrlConfigured = Boolean(engine?.url && String(engine.url).trim());
  const engineTraffic =
    typeof engine?.traffic_percent === "number" ? Number(engine.traffic_percent) : null;
  const engineIdle =
    Boolean(engine) && (!engine?.enabled || engineTraffic === 0 || !engineUrlConfigured);
  const cutoverWarning = localizeRandomEngineMessage(
    typeof engine?.cutover_warning === "string" ? engine.cutover_warning : null,
  );
  const revision =
    health && typeof (health as { snapshot_revision?: unknown }).snapshot_revision === "string"
      ? String((health as { snapshot_revision: string }).snapshot_revision)
      : null;
  const edge = imageEdgeStatus.data;
  const cfApi = cfApiProxyStatus.data;
  const r2 = r2PrewarmStatus.data;

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        维护工具
      </Typography.Title>

      <Card title="Image Edge（CF 出图）">
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          只读配置状态（不展示密钥）。公开出图优先签 CF Worker URL；默认{" "}
          <Typography.Text code>IMAGE_EDGE_ENABLED=false</Typography.Text>
          ，未 ready 时回退本地 /i。多地区 403 POC 与 wrangler 部署在进程外完成。
        </Typography.Paragraph>

        <QueryState query={imageEdgeStatus}>
          {edge ? (
            <Descriptions size="small" column={1} bordered style={{ maxWidth: 640, marginBottom: 16 }}>
              <Descriptions.Item label="开关 flag">
                {edge.enabled_flag ? <Tag color="blue">ENABLED</Tag> : <Tag>OFF</Tag>}
              </Descriptions.Item>
              <Descriptions.Item label="可签 URL">
                {edge.ready ? <Tag color="green">ready</Tag> : <Tag color="orange">not ready</Tag>}
              </Descriptions.Item>
              <Descriptions.Item label="Base URLs">
                {edge.base_urls?.length ? edge.base_urls.join(", ") : "（未配置）"}
              </Descriptions.Item>
              <Descriptions.Item label="TTL 秒">{edge.sign_ttl_seconds}</Descriptions.Item>
              <Descriptions.Item label="密钥">
                {edge.has_secret ? "已配置" : "缺失"}
                {edge.has_secret_previous ? "（含 previous 轮换）" : ""}
              </Descriptions.Item>
              <Descriptions.Item label="缺失项">
                {edge.missing?.length ? edge.missing.join(", ") : "—"}
              </Descriptions.Item>
            </Descriptions>
          ) : null}
        </QueryState>

        <Button onClick={() => void imageEdgeStatus.refetch()} loading={imageEdgeStatus.isFetching}>
          刷新状态
        </Button>
      </Card>

      <Card title="CF API Proxy（hydrate/OAuth 出口）">
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          只读配置状态（不展示密钥）。hydrate 的 OAuth refresh 与 illust detail 优先经 CF Worker
          出口；失败回退住宅代理池。默认{" "}
          <Typography.Text code>CF_API_PROXY_ENABLED=false</Typography.Text>
          。部署见 <Typography.Text code>edge/api-worker</Typography.Text>。
        </Typography.Paragraph>

        <QueryState query={cfApiProxyStatus}>
          {cfApi ? (
            <Descriptions size="small" column={1} bordered style={{ maxWidth: 640, marginBottom: 16 }}>
              <Descriptions.Item label="开关 flag">
                {cfApi.enabled_flag ? <Tag color="blue">ENABLED</Tag> : <Tag>OFF</Tag>}
              </Descriptions.Item>
              <Descriptions.Item label="可走 CF">
                {cfApi.ready ? <Tag color="green">ready</Tag> : <Tag color="orange">not ready</Tag>}
              </Descriptions.Item>
              <Descriptions.Item label="Base URLs">
                {cfApi.base_urls?.length ? cfApi.base_urls.join(", ") : "（未配置）"}
              </Descriptions.Item>
              <Descriptions.Item label="共享密钥">
                {cfApi.has_secret ? "已配置" : "缺失（Worker fail-closed 必需）"}
              </Descriptions.Item>
              <Descriptions.Item label="缺失项">
                {cfApi.missing?.length ? cfApi.missing.join(", ") : "—"}
              </Descriptions.Item>
            </Descriptions>
          ) : null}
        </QueryState>

        <Button onClick={() => void cfApiProxyStatus.refetch()} loading={cfApiProxyStatus.isFetching}>
          刷新状态
        </Button>
      </Card>

      <Card title="R2 Prewarm（Mode B2 钩子）">
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          只读配置状态。hydrate/import/heal 写库后 BFF 将 catalog image_ids 映射为 pximg paths，best-effort POST{" "}
          <Typography.Text code>{'{ "paths": [...] }'}</Typography.Text> +{" "}
          <Typography.Text code>X-Prewarm-Secret</Typography.Text> 到{" "}
          <Typography.Text code>R2_PREWARM_URL/v1/prewarm</Typography.Text>
          。Worker 侧 R2 binding / <Typography.Text code>R2_MODE</Typography.Text> 见{" "}
          <Typography.Text code>edge/img-worker</Typography.Text>。默认关；不展示完整 URL/密钥。
        </Typography.Paragraph>

        <QueryState query={r2PrewarmStatus}>
          {r2 ? (
            <Descriptions size="small" column={1} bordered style={{ maxWidth: 640, marginBottom: 16 }}>
              <Descriptions.Item label="开关 flag">
                {r2.enabled_flag ? <Tag color="blue">ENABLED</Tag> : <Tag>OFF</Tag>}
              </Descriptions.Item>
              <Descriptions.Item label="可 enqueue">
                {r2.ready ? <Tag color="green">ready</Tag> : <Tag color="orange">not ready</Tag>}
              </Descriptions.Item>
              <Descriptions.Item label="URL">
                {r2.url_configured ? r2.url_preview || "已配置" : "（未配置）"}
              </Descriptions.Item>
              <Descriptions.Item label="密钥">
                {r2.secret_configured ? "已配置" : "缺失（R2_PREWARM_SECRET / IMAGE_EDGE_SECRET）"}
              </Descriptions.Item>
              <Descriptions.Item label="载荷">
                {r2.payload_shape === "paths" ? "paths（Worker 契约）" : r2.payload_shape || "paths"}
              </Descriptions.Item>
              <Descriptions.Item label="缺失项">
                {r2.missing?.length ? r2.missing.join(", ") : "—"}
              </Descriptions.Item>
            </Descriptions>
          ) : null}
        </QueryState>

        <Button onClick={() => void r2PrewarmStatus.refetch()} loading={r2PrewarmStatus.isFetching}>
          刷新状态
        </Button>
      </Card>

      <Card title="API Key 限流">
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          公开接口 X-API-Key 限流后端：默认 process-local memory；可选 Redis
          （PUBLIC_API_KEY_RATE_LIMIT_BACKEND=redis + REDIS_URL）。Redis 不可用时 fail-open 到
          memory，不挡主路径。不展示 Redis URL。
        </Typography.Paragraph>

        <QueryState query={apiKeyRlStatus}>
          {apiKeyRlStatus.data ? (
            <Descriptions size="small" column={1} bordered style={{ maxWidth: 640, marginBottom: 16 }}>
              <Descriptions.Item label="强制 API Key">
                {apiKeyRlStatus.data.required ? <Tag color="blue">REQUIRED</Tag> : <Tag>OFF</Tag>}
              </Descriptions.Item>
              <Descriptions.Item label="RPM / Burst">
                {apiKeyRlStatus.data.rpm} / {apiKeyRlStatus.data.burst}
              </Descriptions.Item>
              <Descriptions.Item label="配置后端">
                {apiKeyRlStatus.data.configured_backend}
              </Descriptions.Item>
              <Descriptions.Item label="实际后端">
                {apiKeyRlStatus.data.active_backend === "redis" ? (
                  <Tag color="green">redis</Tag>
                ) : (
                  <Tag>memory</Tag>
                )}
                {apiKeyRlStatus.data.using_memory_fallback ? (
                  <Tag color="orange" style={{ marginLeft: 8 }}>
                    redis→memory fallback
                  </Tag>
                ) : null}
              </Descriptions.Item>
              <Descriptions.Item label="REDIS_URL">
                {apiKeyRlStatus.data.redis_url_configured ? "已配置" : "未配置"}
              </Descriptions.Item>
            </Descriptions>
          ) : null}
        </QueryState>

        <Button onClick={() => void apiKeyRlStatus.refetch()} loading={apiKeyRlStatus.isFetching}>
          刷新状态
        </Button>
      </Card>

      <Card title="模块端口（Phase 4）">
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          Catalog / TagStore / JobQueue / RecentDedup / RandomService / RandomPick 端口只读状态。每行展示
          active（实际）与 requested/configured（配置意图）。JOB_QUEUE_BACKEND=memory 为 SQLite jobs
          表的别名标签（仍持久化）。redis/nats 队列未实现 — 配置会直接启动失败（不再静默回落）。RecentDedup 支持
          RECENT_DEDUP_BACKEND=redis + REDIS_URL（失败回落 memory）。Catalog/TagStore/RandomPick 由
          DATABASE_URL 方言派生。不展示密钥或连接串。
        </Typography.Paragraph>

        <QueryState query={modularPortsStatus}>
          {modularPortsStatus.data ? (
            <Descriptions size="small" column={1} bordered style={{ maxWidth: 720, marginBottom: 16 }}>
              <Descriptions.Item label="Catalog">
                <Tag>active={modularPortsStatus.data.catalog.backend}</Tag>
                <Typography.Text type="secondary" style={{ marginLeft: 8 }}>
                  source=DATABASE_URL
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="Tag Store">
                <Tag>active={modularPortsStatus.data.tags.backend}</Tag>
                <Typography.Text type="secondary" style={{ marginLeft: 8 }}>
                  source=DATABASE_URL
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="Job Queue">
                <Tag
                  color={
                    modularPortsStatus.data.job_queue.using_sqlite_fallback
                      ? "orange"
                      : modularPortsStatus.data.job_queue.backend === "memory"
                        ? "blue"
                        : undefined
                  }
                >
                  active={modularPortsStatus.data.job_queue.backend}
                  {modularPortsStatus.data.job_queue.backend === "memory" ? " (SQLite storage)" : ""}
                </Tag>
                <Tag style={{ marginLeft: 8 }}>
                  requested={modularPortsStatus.data.job_queue.requested}
                </Tag>
                {modularPortsStatus.data.job_queue.implemented === false ? (
                  <Tag color="orange" style={{ marginLeft: 8 }}>
                    not implemented
                  </Tag>
                ) : modularPortsStatus.data.job_queue.implemented === true ? (
                  <Tag color="green" style={{ marginLeft: 8 }}>
                    implemented
                  </Tag>
                ) : null}
                {modularPortsStatus.data.job_queue.using_sqlite_fallback ? (
                  <Tag color="orange" style={{ marginLeft: 8 }}>
                    {modularPortsStatus.data.job_queue.requested}→sqlite fallback
                  </Tag>
                ) : null}
              </Descriptions.Item>
              <Descriptions.Item label="Recent Dedup">
                <Tag
                  color={
                    modularPortsStatus.data.recent_dedup.active_backend === "redis"
                      ? "green"
                      : modularPortsStatus.data.recent_dedup.using_memory_fallback
                        ? "orange"
                        : undefined
                  }
                >
                  active={modularPortsStatus.data.recent_dedup.active_backend}
                </Tag>
                <Tag style={{ marginLeft: 8 }}>
                  requested={modularPortsStatus.data.recent_dedup.configured_backend}
                </Tag>
                {modularPortsStatus.data.recent_dedup.using_memory_fallback ? (
                  <Tag color="orange" style={{ marginLeft: 8 }}>
                    redis→memory fallback
                  </Tag>
                ) : null}
              </Descriptions.Item>
              <Descriptions.Item label="Random Service">
                <Tag>active={modularPortsStatus.data.random_service?.backend ?? "default"}</Tag>
                <Typography.Text type="secondary" style={{ marginLeft: 8 }}>
                  factory=default
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="Random Pick">
                <Tag>active={modularPortsStatus.data.random_pick?.backend ?? "sqlite"}</Tag>
                <Typography.Text type="secondary" style={{ marginLeft: 8 }}>
                  source=DATABASE_URL
                </Typography.Text>
              </Descriptions.Item>
            </Descriptions>
          ) : null}
        </QueryState>

        <Button onClick={() => void modularPortsStatus.refetch()} loading={modularPortsStatus.isFetching}>
          刷新状态
        </Button>
      </Card>

      <Card title="Random Engine（Go 双跑）">
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          只读状态 + 全量快照推送。RANDOM_ENGINE_ENABLED 默认关；一旦开启，RANDOM_ENGINE_TRAFFIC_PERCENT
          默认 100（全量 dual-run，除非显式调低）。未配置 URL 时推送会失败。
        </Typography.Paragraph>

        <QueryState query={engineStatus}>
          {engine ? (
            <>
              {cutoverWarning && !engineIdle ? (
                <Alert
                  type="warning"
                  showIcon
                  style={{ maxWidth: 640, marginBottom: 16 }}
                  message="双跑切流风险"
                  description={cutoverWarning}
                />
              ) : null}
              <Descriptions size="small" column={1} bordered style={{ maxWidth: 640, marginBottom: 16 }}>
                <Descriptions.Item label="URL">{engine.url || "（未配置）"}</Descriptions.Item>
                <Descriptions.Item label="双跑开关">
                  {engine.enabled ? <Tag color="green">ENABLED</Tag> : <Tag>OFF</Tag>}
                </Descriptions.Item>
                <Descriptions.Item label="切流 %">
                  {typeof engine.traffic_percent === "number" ? engine.traffic_percent : "—"}
                </Descriptions.Item>
                <Descriptions.Item label="超时 ms">
                  {typeof engine.timeout_ms === "number" ? engine.timeout_ms : "—"}
                </Descriptions.Item>
                <Descriptions.Item label="健康">
                  {!engineUrlConfigured ? (
                    <Tag>未配置</Tag>
                  ) : engine.healthy ? (
                    <Tag color="green">healthy</Tag>
                  ) : (
                    <Tag color="orange">unreachable</Tag>
                  )}
                </Descriptions.Item>
                <Descriptions.Item label="索引规模">
                  {indexSize ?? "—"}
                  {indexEmpty === true ? (
                    <Tag color="orange" style={{ marginLeft: 8 }}>
                      empty
                    </Tag>
                  ) : null}
                </Descriptions.Item>
                <Descriptions.Item label="可切流">
                  {readyForTraffic ? (
                    <Tag color="green">ready</Tag>
                  ) : engineIdle ? (
                    <Tag>未启用 / 未切流</Tag>
                  ) : (
                    <Tag color="orange">not ready</Tag>
                  )}
                </Descriptions.Item>
                <Descriptions.Item label="就绪检查">
                  <Space size={[4, 4]} wrap>
                    <Tag color={engine.enabled ? "green" : undefined}>enabled={String(Boolean(engine.enabled))}</Tag>
                    <Tag color={engineTraffic != null && engineTraffic > 0 ? "green" : undefined}>
                      traffic&gt;0={String(engineTraffic != null && engineTraffic > 0)}
                    </Tag>
                    <Tag color={engine.healthy ? "green" : undefined}>healthy={String(Boolean(engine.healthy))}</Tag>
                    <Tag color={indexEmpty === false ? "green" : undefined}>
                      index_nonempty={String(indexEmpty === false)}
                    </Tag>
                  </Space>
                </Descriptions.Item>
                <Descriptions.Item label="快照 revision">{revision || "—"}</Descriptions.Item>
              </Descriptions>
            </>
          ) : null}
        </QueryState>

        <Space wrap>
          <Button onClick={() => void engineStatus.refetch()} loading={engineStatus.isFetching}>
            刷新状态
          </Button>
          <Button
            type="primary"
            onClick={() => pushSnapshot.mutate()}
            loading={pushSnapshot.isPending}
            disabled={!engineUrlConfigured}
          >
            推送全量快照
          </Button>
          <Button
            onClick={() => compareFilters.mutate()}
            loading={compareFilters.isPending}
            disabled={!engineUrlConfigured}
          >
            对比过滤基数（默认 r18=0）
          </Button>
        </Space>

        {compareResult ? (
          <Descriptions size="small" column={1} bordered style={{ maxWidth: 640, marginTop: 16 }}>
            <Descriptions.Item label="match">
              {compareResult.match ? <Tag color="green">一致</Tag> : <Tag color="red">不一致</Tag>}
            </Descriptions.Item>
            <Descriptions.Item label="Python filtered">{compareResult.python_filtered}</Descriptions.Item>
            <Descriptions.Item label="Engine filtered">{compareResult.engine_filtered}</Descriptions.Item>
            <Descriptions.Item label="delta">{compareResult.delta}</Descriptions.Item>
            <Descriptions.Item label="Engine index">{compareResult.engine_index_size}</Descriptions.Item>
            <Descriptions.Item label="Engine revision">{compareResult.engine_revision || "—"}</Descriptions.Item>
            <Descriptions.Item label="r18_strict">{compareResult.r18_strict}</Descriptions.Item>
          </Descriptions>
        ) : null}

        <div style={{ marginTop: 16 }}>
          <ActionAlerts
            message={engineAlerts.message}
            requestId={engineAlerts.requestId}
            errorMessage={engineAlerts.errorMessage}
            errorRequestId={engineAlerts.errorRequestId}
            requestIdPlacement="description"
          />
        </div>
      </Card>

      <Card title="请求日志清理">
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          清理过期 request_logs，建议先 dry-run 预览再实际删除。可多次执行直到 has_more=false。
        </Typography.Paragraph>

        <Form
          form={form}
          layout="vertical"
          initialValues={{ keep_days: 14, max_delete_rows: 50000, chunk_size: 2000, dry_run: true }}
          onFinish={(values) => cleanup.mutate(values)}
          style={{ maxWidth: 520 }}
        >
          <Form.Item label="保留天数" name="keep_days" rules={[{ required: true }]}>
            <InputNumber min={0} max={36500} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item label="单次最大删除行数" name="max_delete_rows" rules={[{ required: true }]}>
            <InputNumber min={1} max={10_000_000} step={1000} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item label="分块大小" name="chunk_size" rules={[{ required: true }]}>
            <InputNumber min={1} max={100_000} step={100} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item label="仅预览（dry_run）" name="dry_run" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Space wrap>
            <Button type="primary" htmlType="submit" loading={cleanup.isPending}>
              执行
            </Button>
            <Button
              onClick={() => {
                form.setFieldsValue({ dry_run: true });
                form.submit();
              }}
              loading={cleanup.isPending}
            >
              预览
            </Button>
          </Space>
        </Form>

        {cleanup.isPending ? <Skeleton active style={{ marginTop: 16 }} /> : null}
        <div style={{ marginTop: 16 }}>
          <ActionAlerts
            message={alerts.message}
            requestId={alerts.requestId}
            errorMessage={alerts.errorMessage}
            errorRequestId={alerts.errorRequestId}
            requestIdPlacement="description"
          />
        </div>
      </Card>
    </Space>
  );
}
