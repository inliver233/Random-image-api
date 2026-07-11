import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Card, Descriptions, Form, InputNumber, Skeleton, Space, Switch, Tag, Typography } from "antd";
import React from "react";

import { ActionAlerts } from "../admin/ActionAlerts";
import { QueryState } from "../admin/QueryState";
import { useActionAlerts } from "../admin/useActionAlerts";
import { apiJson } from "../api/client";

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
  tags?: { backend: string };
  job_queue: { backend: string; requested: string };
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
      engineAlerts.setError(err);
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
    },
    onError: (err) => {
      engineAlerts.setError(err);
    },
  });

  const engine = engineStatus.data;
  const health = engine?.health && typeof engine.health === "object" ? engine.health : null;
  const indexSize =
    health && typeof (health as { index_size?: unknown }).index_size === "number"
      ? Number((health as { index_size: number }).index_size)
      : null;
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
                {cfApi.has_secret ? "已配置" : "未配置（可选）"}
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
          只读配置状态。hydrate/import/heal 写库后 best-effort POST{" "}
          <Typography.Text code>{"{image_ids}"}</Typography.Text> 到{" "}
          <Typography.Text code>R2_PREWARM_URL/v1/prewarm</Typography.Text>
          。Worker 侧 R2 binding / <Typography.Text code>R2_MODE</Typography.Text> 见{" "}
          <Typography.Text code>edge/img-worker</Typography.Text>。默认关；不展示完整 URL。
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
          Catalog / TagStore / JobQueue / RecentDedup / RandomService 端口只读状态。Catalog/TagStore/JobQueue
          默认本地；RecentDedup 支持 RECENT_DEDUP_BACKEND=redis + REDIS_URL（失败回落 memory）。RandomService
          为默认 pick 计划工厂。不展示密钥或连接串。
        </Typography.Paragraph>

        <QueryState query={modularPortsStatus}>
          {modularPortsStatus.data ? (
            <Descriptions size="small" column={1} bordered style={{ maxWidth: 640, marginBottom: 16 }}>
              <Descriptions.Item label="Catalog">
                <Tag>{modularPortsStatus.data.catalog.backend}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="Tag Store">
                <Tag>{modularPortsStatus.data.tags?.backend ?? "sqlite"}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="Job Queue">
                <Tag>{modularPortsStatus.data.job_queue.backend}</Tag>
                {modularPortsStatus.data.job_queue.requested !== modularPortsStatus.data.job_queue.backend ? (
                  <Tag color="orange" style={{ marginLeft: 8 }}>
                    requested={modularPortsStatus.data.job_queue.requested}
                  </Tag>
                ) : null}
              </Descriptions.Item>
              <Descriptions.Item label="Recent Dedup">
                {modularPortsStatus.data.recent_dedup.active_backend === "redis" ? (
                  <Tag color="green">redis</Tag>
                ) : (
                  <Tag>memory</Tag>
                )}
                {modularPortsStatus.data.recent_dedup.using_memory_fallback ? (
                  <Tag color="orange" style={{ marginLeft: 8 }}>
                    redis→memory fallback
                  </Tag>
                ) : null}
              </Descriptions.Item>
              <Descriptions.Item label="Random Service">
                <Tag>{modularPortsStatus.data.random_service?.backend ?? "default"}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="Random Pick">
                <Tag>{modularPortsStatus.data.random_pick?.backend ?? "sqlite"}</Tag>
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
          只读状态 + 全量快照推送。切流由环境变量控制（默认关）：RANDOM_ENGINE_ENABLED /
          RANDOM_ENGINE_TRAFFIC_PERCENT。未配置 URL 时推送会失败。
        </Typography.Paragraph>

        <QueryState query={engineStatus}>
          {engine ? (
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
                {engine.healthy ? <Tag color="green">healthy</Tag> : <Tag color="orange">unreachable</Tag>}
              </Descriptions.Item>
              <Descriptions.Item label="索引规模">{indexSize ?? "—"}</Descriptions.Item>
              <Descriptions.Item label="快照 revision">{revision || "—"}</Descriptions.Item>
            </Descriptions>
          ) : null}
        </QueryState>

        <Space wrap>
          <Button onClick={() => void engineStatus.refetch()} loading={engineStatus.isFetching}>
            刷新状态
          </Button>
          <Button type="primary" onClick={() => pushSnapshot.mutate()} loading={pushSnapshot.isPending}>
            推送全量快照
          </Button>
          <Button onClick={() => compareFilters.mutate()} loading={compareFilters.isPending}>
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
