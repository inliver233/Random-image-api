import { useMutation, useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Row, Skeleton, Space, Typography } from "antd";
import React from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, apiJson } from "../api/client";

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

type JobsResponse = { ok: true; items: unknown[]; next_cursor: string; request_id: string };
type CreateHydrationRunResponse = { ok: true; hydration_run_id: string; job_id: string; request_id: string };

function asApiError(err: unknown): ApiError | null {
  return err instanceof ApiError ? err : null;
}

function requestIdFromError(err: unknown): string | null {
  const apiErr = asApiError(err);
  if (!apiErr?.body?.request_id) return null;
  return String(apiErr.body.request_id);
}

function messageFromError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "未知错误";
}

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

  const failedJobCount = failedJobs.data?.items.length ?? 0;

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
          description={
            requestIdFromError(createHydration.error)
              ? `请求ID: ${requestIdFromError(createHydration.error)}（${messageFromError(createHydration.error)}）`
              : messageFromError(createHydration.error)
          }
          style={{ marginBottom: 16 }}
        />
      ) : null}

      <Row gutter={[16, 16]}>
        <Col xs={24} md={12} xl={6}>
          <Card title="工作线程 / 队列">
            {summary.isLoading ? (
              <Skeleton active />
            ) : summary.isError ? (
              <Alert
                type="error"
                showIcon
                message="加载总览失败"
                description={requestIdFromError(summary.error) ? `请求ID: ${requestIdFromError(summary.error)}` : ""}
              />
            ) : (
              <Space direction="vertical">
                <Typography.Text>工作线程心跳: {workerLastSeenAt || "（暂无）"}</Typography.Text>
                <Typography.Text>等待任务: {pendingJobs}</Typography.Text>
                <Typography.Text>运行任务: {runningJobs}</Typography.Text>
                <Typography.Text>失败任务: {failedJobsTotal}</Typography.Text>
              </Space>
            )}
          </Card>
        </Col>

        <Col xs={24} md={12} xl={6}>
          <Card title="图片">
            {summary.isLoading ? (
              <Skeleton active />
            ) : summary.isError ? (
              <Alert
                type="error"
                showIcon
                message="加载总览失败"
                description={requestIdFromError(summary.error) ? `请求ID: ${requestIdFromError(summary.error)}` : ""}
              />
            ) : (
              <Space direction="vertical">
                <Typography.Text>总数: {imageCount}</Typography.Text>
                <Typography.Text>启用: {imageEnabledCount}</Typography.Text>
                <Button size="small" onClick={() => navigate("/admin/images")}>
                  打开图片列表
                </Button>
              </Space>
            )}
          </Card>
        </Col>

        <Col xs={24} md={12} xl={6}>
          <Card title="令牌">
            {summary.isLoading ? (
              <Skeleton active />
            ) : summary.isError ? (
              <Alert
                type="error"
                showIcon
                message="加载总览失败"
                description={requestIdFromError(summary.error) ? `请求ID: ${requestIdFromError(summary.error)}` : ""}
              />
            ) : (
              <Space direction="vertical">
                <Typography.Text>总数: {tokenCount}</Typography.Text>
                <Typography.Text>启用: {tokenEnabledCount}</Typography.Text>
                <Button size="small" onClick={() => navigate("/admin/tokens")}>
                  打开令牌列表
                </Button>
              </Space>
            )}
          </Card>
        </Col>

        <Col xs={24} md={12} xl={6}>
          <Card title="代理">
            {settings.isLoading || summary.isLoading ? (
              <Skeleton active />
            ) : settings.isError ? (
              <Alert
                type="error"
                showIcon
                message="加载设置失败"
                description={requestIdFromError(settings.error) ? `请求ID: ${requestIdFromError(settings.error)}` : ""}
              />
            ) : summary.isError ? (
              <Alert
                type="error"
                showIcon
                message="加载总览失败"
                description={requestIdFromError(summary.error) ? `请求ID: ${requestIdFromError(summary.error)}` : ""}
              />
            ) : (
              <Space direction="vertical">
                <Typography.Text>代理总开关: {proxyEnabled ? "开启" : "关闭"}</Typography.Text>
                <Typography.Text>默认代理池ID: {defaultPoolId || "（未设置）"}</Typography.Text>
                <Typography.Text>代理节点: {proxyEnabledCount}/{proxyCount} 启用</Typography.Text>
                <Typography.Text>代理池: {proxyPoolEnabledCount}/{proxyPoolCount} 启用</Typography.Text>
                <Typography.Text>绑定关系: {bindingCount}</Typography.Text>
                <Button size="small" onClick={() => navigate("/admin/proxies")}>
                  打开代理列表
                </Button>
              </Space>
            )}
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} md={12} xl={8}>
          <Card title="随机接口（/random）统计">
            {randomStats.isLoading ? (
              <Skeleton active />
            ) : randomStats.isError ? (
              <Alert
                type="error"
                showIcon
                message="加载随机统计失败"
                description={requestIdFromError(randomStats.error) ? `请求ID: ${requestIdFromError(randomStats.error)}` : ""}
              />
            ) : !randomStats.data ? (
              <Skeleton active />
            ) : (
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
            )}
          </Card>
        </Col>

        <Col xs={24} md={12} xl={8}>
          <Card title="版本信息">
            {version.isLoading ? (
              <Skeleton active />
            ) : version.isError ? (
              <Alert
                type="error"
                showIcon
                message="加载版本信息失败"
                description={requestIdFromError(version.error) ? `请求ID: ${requestIdFromError(version.error)}` : ""}
              />
            ) : !version.data ? (
              <Skeleton active />
            ) : (
              <Space direction="vertical">
                <Typography.Text>版本: {version.data.version || "（未知）"}</Typography.Text>
                <Typography.Text>构建时间: {version.data.build_time || "（未设置）"}</Typography.Text>
                <Typography.Text>提交: {version.data.git_commit || "（未设置）"}</Typography.Text>
                <Typography.Text type="secondary">请求ID: {version.data.request_id}</Typography.Text>
              </Space>
            )}
          </Card>
        </Col>

        <Col xs={24} md={12} xl={8}>
          <Card title="失败任务（最近10条）">
            {failedJobs.isLoading ? (
              <Skeleton active />
            ) : failedJobs.isError ? (
              <Alert
                type="error"
                showIcon
                message="加载任务失败"
                description={requestIdFromError(failedJobs.error) ? `请求ID: ${requestIdFromError(failedJobs.error)}` : ""}
              />
            ) : (
              <Space direction="vertical">
                <Typography.Text>数量: {failedJobCount}</Typography.Text>
                <Button size="small" onClick={() => navigate("/admin/jobs")}>
                  打开任务页
                </Button>
              </Space>
            )}
          </Card>
        </Col>
      </Row>
    </>
  );
}


