import { useMutation, useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Row, Space, Tag, Typography } from "antd";
import React from "react";
import { useNavigate } from "react-router-dom";

import { QueryState } from "../admin/QueryState";
import { requestIdWithMessageDescription } from "../admin/errors";
import { apiJson } from "../api/client";

type SummaryResponse = {
  ok: true;
  counts: {
    images: { total: number; enabled: number };
    tokens: { total: number; enabled: number };
    proxies: { endpoints_total: number; endpoints_enabled: number };
    proxy_pools: { total: number; enabled: number };
    bindings: { total: number };
    jobs: { counts: Record<string, number> };
    worker: { last_seen_at: string | null };
  };
  request_id: string;
};

type SettingsResponse = {
  ok: true;
  settings: {
    proxy: {
      enabled: boolean;
      fail_closed: boolean;
      route_mode: string;
      allowlist_domains: string[];
      default_pool_id?: string;
    };
    random: Record<string, unknown>;
    security: { hide_origin_url_in_public_json: boolean };
    rate_limit: Record<string, unknown>;
  };
  request_id: string;
};

type VersionResponse = {
  ok: true;
  version: string;
  build_time: string;
  git_commit: string;
  request_id: string;
};

type RandomStatsResponse = {
  ok: true;
  stats: {
    total_requests: number;
    total_ok: number;
    total_error: number;
    in_flight: number;
    window_seconds: number;
    last_window_requests: number;
    last_window_ok: number;
    last_window_error: number;
    last_window_success_rate: number;
  };
  request_id: string;
};

type FailedJobItem = {
  id: string;
  type: string;
  status: string;
  last_error?: string | null;
  updated_at?: string | null;
  attempt?: number;
  max_attempts?: number;
};
type JobsResponse = { ok: true; items: FailedJobItem[]; next_cursor: string; request_id: string };
type CreateHydrationRunResponse = { ok: true; hydration_run_id: string; job_id: string; request_id: string };

type ModularPortsStatusResponse = {
  ok: true;
  catalog: { backend: string };
  tags: { backend: string };
  job_queue: {
    backend: string;
    requested: string;
    implemented?: boolean;
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

type ImageEdgeStatusResponse = {
  ok: true;
  enabled_flag: boolean;
  ready: boolean;
  base_url_count: number;
  has_secret: boolean;
  request_id: string;
};

type RandomEngineStatusResponse = {
  ok: true;
  enabled: boolean;
  traffic_percent?: number;
  healthy: boolean;
  index_empty?: boolean | null;
  ready_for_traffic?: boolean;
  cutover_warning?: string | null;
  /** Process dual-run circuit (closed / half_open / open). */
  circuit?: {
    state?: string;
    consecutive_failures?: number;
    open_remaining_s?: number;
  } | null;
  request_id: string;
};

type CfApiProxyStatusResponse = {
  ok: true;
  enabled_flag: boolean;
  ready: boolean;
  base_url_count: number;
  has_secret: boolean;
  request_id: string;
};

type R2PrewarmStatusResponse = {
  ok: true;
  enabled_flag: boolean;
  ready: boolean;
  url_configured: boolean;
  secret_configured?: boolean;
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

export function DashboardPage() {
  const navigate = useNavigate();

  const createHydration = useMutation({
    mutationFn: () =>
      apiJson<CreateHydrationRunResponse>("/admin/api/hydration-runs", {
        method: "POST",
        body: JSON.stringify({}),
      }),
  });

  const settings = useQuery({
    queryKey: ["admin", "settings"],
    queryFn: () => apiJson<SettingsResponse>("/admin/api/settings"),
  });

  const summary = useQuery({
    queryKey: ["admin", "summary"],
    queryFn: () => apiJson<SummaryResponse>("/admin/api/summary"),
  });

  const failedJobs = useQuery({
    queryKey: ["admin", "jobs", "failed"],
    queryFn: () => apiJson<JobsResponse>("/admin/api/jobs?status=failed&limit=10"),
  });

  const randomStats = useQuery({
    queryKey: ["admin", "stats", "random"],
    queryFn: () => apiJson<RandomStatsResponse>("/admin/api/stats/random"),
    refetchInterval: 5000,
  });

  const modularPorts = useQuery({
    queryKey: ["admin", "maintenance", "modular-ports"],
    queryFn: () => apiJson<ModularPortsStatusResponse>("/admin/api/maintenance/modular-ports"),
    refetchInterval: 30_000,
  });

  const imageEdge = useQuery({
    queryKey: ["admin", "maintenance", "image-edge"],
    queryFn: () => apiJson<ImageEdgeStatusResponse>("/admin/api/maintenance/image-edge"),
    refetchInterval: 30_000,
  });

  const randomEngine = useQuery({
    queryKey: ["admin", "maintenance", "random-engine"],
    queryFn: () => apiJson<RandomEngineStatusResponse>("/admin/api/maintenance/random-engine"),
    refetchInterval: 15_000,
  });

  const cfApiProxy = useQuery({
    queryKey: ["admin", "maintenance", "cf-api-proxy"],
    queryFn: () => apiJson<CfApiProxyStatusResponse>("/admin/api/maintenance/cf-api-proxy"),
    refetchInterval: 30_000,
  });

  const r2Prewarm = useQuery({
    queryKey: ["admin", "maintenance", "r2-prewarm"],
    queryFn: () => apiJson<R2PrewarmStatusResponse>("/admin/api/maintenance/r2-prewarm"),
    refetchInterval: 30_000,
  });

  const apiKeyRl = useQuery({
    queryKey: ["admin", "maintenance", "api-key-rate-limit"],
    queryFn: () => apiJson<ApiKeyRateLimitStatusResponse>("/admin/api/maintenance/api-key-rate-limit"),
    refetchInterval: 30_000,
  });

  const version = useQuery({
    queryKey: ["public", "version"],
    queryFn: () => apiJson<VersionResponse>("/version"),
  });

  const proxyEnabled = settings.data?.settings.proxy.enabled ?? false;
  const defaultPoolId = settings.data?.settings.proxy.default_pool_id ?? "";

  const counts = summary.data?.counts;
  const imageCount = counts?.images.total ?? 0;
  const imageEnabledCount = counts?.images.enabled ?? 0;
  const tokenCount = counts?.tokens.total ?? 0;
  const tokenEnabledCount = counts?.tokens.enabled ?? 0;
  const proxyCount = counts?.proxies.endpoints_total ?? 0;
  const proxyEnabledCount = counts?.proxies.endpoints_enabled ?? 0;
  const proxyPoolCount = counts?.proxy_pools.total ?? 0;
  const proxyPoolEnabledCount = counts?.proxy_pools.enabled ?? 0;
  const bindingCount = counts?.bindings.total ?? 0;

  const jobsCounts = counts?.jobs.counts ?? {};
  const pendingJobs = jobsCounts.pending ?? 0;
  const runningJobs = jobsCounts.running ?? 0;
  const failedJobsTotal = jobsCounts.failed ?? 0;
  const workerLastSeenAt = counts?.worker.last_seen_at ?? null;

  const failedJobItems = failedJobs.data?.items ?? [];
  const failedJobCount = failedJobItems.length;

  const lastWindowRequests = randomStats.data?.stats.last_window_requests ?? 0;
  const lastWindowOk = randomStats.data?.stats.last_window_ok ?? 0;
  const lastWindowError = randomStats.data?.stats.last_window_error ?? 0;
  const lastWindowSuccessRate = randomStats.data?.stats.last_window_success_rate ?? 0;
  const inFlight = randomStats.data?.stats.in_flight ?? 0;
  const totalRequests = randomStats.data?.stats.total_requests ?? 0;
  const totalOk = randomStats.data?.stats.total_ok ?? 0;
  const totalError = randomStats.data?.stats.total_error ?? 0;

  return (
    <>
      <Space wrap style={{ marginBottom: 16 }}>
        <Button type="primary" onClick={() => navigate("/admin/import")}>
          去导入链接
        </Button>
        <Button onClick={() => navigate("/admin/tokens")}>去添加令牌</Button>
        <Button onClick={() => navigate("/admin/proxies")}>去添加代理</Button>
        <Button onClick={() => navigate("/admin/hydration")}>打开补全管理</Button>
        <Button onClick={() => navigate("/admin/random")}>打开随机测试</Button>
        <Button onClick={() => createHydration.mutate()} loading={createHydration.isPending}>
          创建补全任务
        </Button>
      </Space>

      {createHydration.isSuccess ? (
        <Alert
          type="success"
          showIcon
          message="补全任务已创建"
          description={`补全运行ID: ${createHydration.data.hydration_run_id}，任务ID: ${createHydration.data.job_id}，请求ID: ${createHydration.data.request_id}`}
          style={{ marginBottom: 16 }}
        />
      ) : null}
      {createHydration.isError ? (
        <Alert
          type="error"
          showIcon
          message="创建补全任务失败"
          description={requestIdWithMessageDescription(createHydration.error)}
          style={{ marginBottom: 16 }}
        />
      ) : null}

      <Row gutter={[16, 16]}>
        <Col xs={24} md={12} xl={6}>
          <Card title="工作线程 / 队列">
            <QueryState query={summary} errorMessage="加载总览失败">
              <Space direction="vertical">
                <Typography.Text>工作线程心跳: {workerLastSeenAt || "（暂无）"}</Typography.Text>
                <Typography.Text>等待任务: {pendingJobs}</Typography.Text>
                <Typography.Text>运行任务: {runningJobs}</Typography.Text>
                <Typography.Text>失败任务: {failedJobsTotal}</Typography.Text>
              </Space>
            </QueryState>
          </Card>
        </Col>

        <Col xs={24} md={12} xl={6}>
          <Card title="图片">
            <QueryState query={summary} errorMessage="加载总览失败">
              <Space direction="vertical">
                <Typography.Text>总数: {imageCount}</Typography.Text>
                <Typography.Text>启用: {imageEnabledCount}</Typography.Text>
                <Button size="small" onClick={() => navigate("/admin/images")}>
                  打开图片列表
                </Button>
              </Space>
            </QueryState>
          </Card>
        </Col>

        <Col xs={24} md={12} xl={6}>
          <Card title="令牌">
            <QueryState query={summary} errorMessage="加载总览失败">
              <Space direction="vertical">
                <Typography.Text>总数: {tokenCount}</Typography.Text>
                <Typography.Text>启用: {tokenEnabledCount}</Typography.Text>
                <Button size="small" onClick={() => navigate("/admin/tokens")}>
                  打开令牌列表
                </Button>
              </Space>
            </QueryState>
          </Card>
        </Col>

        <Col xs={24} md={12} xl={6}>
          <Card title="代理">
            <QueryState
              queries={[settings, summary]}
              errorMessages={["加载设置失败", "加载总览失败"]}
            >
              <Space direction="vertical">
                <Typography.Text>补全代理总开关: {proxyEnabled ? "开启" : "关闭"}</Typography.Text>
                <Typography.Text>默认补全代理池ID: {defaultPoolId || "（未设置）"}</Typography.Text>
                <Typography.Text>补全代理节点: {proxyEnabledCount}/{proxyCount} 启用</Typography.Text>
                <Typography.Text>补全代理池: {proxyPoolEnabledCount}/{proxyPoolCount} 启用</Typography.Text>
                <Typography.Text>绑定关系: {bindingCount}</Typography.Text>
                <Typography.Text type="secondary">仅服务 Hydrate/OAuth，不用于用户出图</Typography.Text>
                <Button size="small" onClick={() => navigate("/admin/proxies")}>
                  打开补全代理列表
                </Button>
              </Space>
            </QueryState>
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} md={12} xl={8}>
          <Card title="随机接口（/random）统计">
            <QueryState query={randomStats} errorMessage="加载随机统计失败">
              {randomStats.data ? (
                <Space direction="vertical">
                  <Typography.Text>总请求: {totalRequests}</Typography.Text>
                  <Typography.Text>
                    总成功/失败: {totalOk}/{totalError}
                  </Typography.Text>
                  <Typography.Text>
                    近 1 分钟请求: {lastWindowRequests}（成功/失败: {lastWindowOk}/{lastWindowError}）
                  </Typography.Text>
                  <Typography.Text>近 1 分钟成功率: {(lastWindowSuccessRate * 100).toFixed(1)}%</Typography.Text>
                  <Typography.Text>当前并发（in-flight）: {inFlight}</Typography.Text>
                  <Typography.Text type="secondary">请求ID: {randomStats.data.request_id}</Typography.Text>
                </Space>
              ) : null}
            </QueryState>
          </Card>
        </Col>

        <Col xs={24} md={12} xl={8}>
          <Card title="版本信息">
            <QueryState query={version} errorMessage="加载版本信息失败">
              {version.data ? (
                <Space direction="vertical">
                  <Typography.Text>版本: {version.data.version || "（未知）"}</Typography.Text>
                  <Typography.Text>构建时间: {version.data.build_time || "（未设置）"}</Typography.Text>
                  <Typography.Text>提交: {version.data.git_commit || "（未设置）"}</Typography.Text>
                  <Typography.Text type="secondary">请求ID: {version.data.request_id}</Typography.Text>
                </Space>
              ) : null}
            </QueryState>
          </Card>
        </Col>

        <Col xs={24} md={12} xl={8}>
          <Card title="失败任务（最近10条）">
            <QueryState query={failedJobs} errorMessage="加载任务失败">
              <Space direction="vertical" style={{ width: "100%" }}>
                <Typography.Text>数量: {failedJobCount}</Typography.Text>
                {failedJobCount === 0 ? (
                  <Typography.Text type="secondary">最近没有失败任务</Typography.Text>
                ) : (
                  failedJobItems.map((job) => {
                    const err = String(job.last_error || "").trim();
                    const errPreview = err.length > 80 ? `${err.slice(0, 80)}…` : err;
                    return (
                      <div key={job.id} style={{ borderTop: "1px solid rgba(0,0,0,0.06)", paddingTop: 8 }}>
                        <Typography.Text>
                          #{job.id} · {job.type || "unknown"}
                        </Typography.Text>
                        <br />
                        <Typography.Text type="secondary">
                          {job.updated_at || "（时间未知）"}
                          {typeof job.attempt === "number" && typeof job.max_attempts === "number"
                            ? ` · 尝试 ${job.attempt}/${job.max_attempts}`
                            : ""}
                        </Typography.Text>
                        {errPreview ? (
                          <>
                            <br />
                            <Typography.Text type="danger">{errPreview}</Typography.Text>
                          </>
                        ) : null}
                      </div>
                    );
                  })
                )}
                <Button size="small" onClick={() => navigate("/admin/jobs")}>
                  打开任务页
                </Button>
              </Space>
            </QueryState>
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} md={24} xl={24}>
          <Card title="模块端口 / 边缘切流（Phase 4）">
            <QueryState
              partial
              queries={[modularPorts, imageEdge, randomEngine, cfApiProxy, r2Prewarm, apiKeyRl]}
              errorMessages={[
                "加载模块端口失败",
                "加载 Image Edge 失败",
                "加载 Random Engine 失败",
                "加载 CF API Proxy 失败",
                "加载 R2 Prewarm 失败",
                "加载 API Key 限流失败",
              ]}
            >
              <Space direction="vertical" style={{ width: "100%" }}>
                {modularPorts.data ? (
                  <Space wrap size={[8, 8]}>
                    <Tag>catalog={modularPorts.data.catalog.backend}</Tag>
                    <Tag>tags={modularPorts.data.tags.backend}</Tag>
                    <Tag
                      color={
                        modularPorts.data.job_queue.backend === "memory" ? "blue" : undefined
                      }
                    >
                      job_queue={modularPorts.data.job_queue.backend}
                      {modularPorts.data.job_queue.requested !== modularPorts.data.job_queue.backend
                        ? ` (req=${modularPorts.data.job_queue.requested})`
                        : ""}
                    </Tag>
                    {typeof modularPorts.data.job_queue.implemented === "boolean" ? (
                      <Tag color={modularPorts.data.job_queue.implemented ? "green" : "orange"}>
                        {modularPorts.data.job_queue.implemented
                          ? "job_queue implemented"
                          : "job_queue not implemented"}
                      </Tag>
                    ) : null}
                    <Tag
                      color={
                        modularPorts.data.recent_dedup.active_backend === "redis"
                          ? "green"
                          : modularPorts.data.recent_dedup.using_memory_fallback
                            ? "orange"
                            : undefined
                      }
                    >
                      recent_dedup={modularPorts.data.recent_dedup.active_backend}
                      {modularPorts.data.recent_dedup.configured_backend !==
                      modularPorts.data.recent_dedup.active_backend
                        ? ` (req=${modularPorts.data.recent_dedup.configured_backend})`
                        : ""}
                    </Tag>
                    {modularPorts.data.recent_dedup.using_memory_fallback ? (
                      <Tag color="orange">redis→memory fallback</Tag>
                    ) : null}
                    <Tag>random_service={modularPorts.data.random_service?.backend ?? "default"}</Tag>
                    <Tag>random_pick={modularPorts.data.random_pick?.backend ?? "sqlite"}</Tag>
                  </Space>
                ) : null}

                {apiKeyRl.data ? (
                  <Space wrap size={[8, 8]}>
                    <Tag
                      color={
                        apiKeyRl.data.active_backend === "redis"
                          ? "green"
                          : apiKeyRl.data.using_memory_fallback
                            ? "orange"
                            : undefined
                      }
                    >
                      api_key_rl={apiKeyRl.data.active_backend}
                      {apiKeyRl.data.configured_backend !== apiKeyRl.data.active_backend
                        ? ` (req=${apiKeyRl.data.configured_backend})`
                        : ""}
                    </Tag>
                    {apiKeyRl.data.using_memory_fallback ? (
                      <Tag color="orange">redis→memory fallback</Tag>
                    ) : null}
                    {apiKeyRl.data.configured_backend === "redis" && !apiKeyRl.data.redis_url_configured ? (
                      <Tag color="orange">no-redis-url</Tag>
                    ) : null}
                    {apiKeyRl.data.required ? <Tag color="blue">api_key required</Tag> : null}
                    <Tag>
                      rpm={apiKeyRl.data.rpm}/{apiKeyRl.data.burst}
                    </Tag>
                  </Space>
                ) : null}

                <Space wrap size={[8, 8]}>
                  {imageEdge.data ? (
                    <>
                      <Tag color={imageEdge.data.ready ? "green" : imageEdge.data.enabled_flag ? "orange" : undefined}>
                        image_edge={imageEdge.data.ready ? "ready" : imageEdge.data.enabled_flag ? "flag-on-not-ready" : "off"}
                      </Tag>
                      <Tag>
                        bases={imageEdge.data.base_url_count}
                        {imageEdge.data.has_secret ? "" : " · no-secret"}
                      </Tag>
                    </>
                  ) : null}
                  {cfApiProxy.data ? (
                    <>
                      <Tag
                        color={
                          cfApiProxy.data.ready
                            ? "green"
                            : cfApiProxy.data.enabled_flag
                              ? "orange"
                              : undefined
                        }
                      >
                        cf_api_proxy=
                        {cfApiProxy.data.ready
                          ? "ready"
                          : cfApiProxy.data.enabled_flag
                            ? "flag-on-not-ready"
                            : "off"}
                      </Tag>
                      <Tag>
                        bases={cfApiProxy.data.base_url_count}
                        {cfApiProxy.data.has_secret ? "" : " · no-secret"}
                      </Tag>
                    </>
                  ) : null}
                  {r2Prewarm.data ? (
                    <Tag
                      color={
                        r2Prewarm.data.ready ? "green" : r2Prewarm.data.enabled_flag ? "orange" : undefined
                      }
                    >
                      r2_prewarm=
                      {r2Prewarm.data.ready
                        ? "ready"
                        : r2Prewarm.data.enabled_flag
                          ? "flag-on-not-ready"
                          : "off"}
                      {r2Prewarm.data.enabled_flag && !r2Prewarm.data.url_configured
                        ? " · no-url"
                        : ""}
                      {r2Prewarm.data.enabled_flag && !r2Prewarm.data.secret_configured
                        ? " · no-secret"
                        : ""}
                    </Tag>
                  ) : null}
                  {randomEngine.data ? (
                    <>
                      <Tag
                        color={
                          randomEngine.data.ready_for_traffic
                            ? "green"
                            : randomEngine.data.enabled
                              ? "orange"
                              : undefined
                        }
                      >
                        engine=
                        {randomEngine.data.ready_for_traffic
                          ? "ready"
                          : randomEngine.data.enabled
                            ? "enabled-not-ready"
                            : "off"}
                      </Tag>
                      <Tag>traffic={randomEngine.data.traffic_percent ?? 0}%</Tag>
                      {randomEngine.data.enabled && randomEngine.data.index_empty ? (
                        <Tag color="orange">engine index empty</Tag>
                      ) : null}
                      {randomEngine.data.circuit?.state ? (
                        <Tag
                          color={
                            randomEngine.data.circuit.state === "open"
                              ? "red"
                              : randomEngine.data.circuit.state === "half_open"
                                ? "orange"
                                : undefined
                          }
                        >
                          circuit={randomEngine.data.circuit.state}
                          {randomEngine.data.circuit.state === "open" &&
                          typeof randomEngine.data.circuit.open_remaining_s === "number"
                            ? ` ~${Math.ceil(randomEngine.data.circuit.open_remaining_s)}s`
                            : ""}
                        </Tag>
                      ) : null}
                      {randomEngine.data.cutover_warning ? (
                        <Tag color="orange">cutover: {randomEngine.data.cutover_warning}</Tag>
                      ) : null}
                    </>
                  ) : null}
                </Space>

                <Typography.Text type="secondary">
                  请求ID:{" "}
                  {[
                    modularPorts.data?.request_id,
                    imageEdge.data?.request_id,
                    cfApiProxy.data?.request_id,
                    r2Prewarm.data?.request_id,
                    randomEngine.data?.request_id,
                    apiKeyRl.data?.request_id,
                  ]
                    .filter(Boolean)
                    .join(" / ") || "—"}
                </Typography.Text>
                <Button size="small" onClick={() => navigate("/admin/maintenance")}>
                  打开维护页（完整状态 / 推送快照）
                </Button>
              </Space>
            </QueryState>
          </Card>
        </Col>
      </Row>
    </>
  );
}

