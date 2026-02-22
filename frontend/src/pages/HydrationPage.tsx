import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Form,
  InputNumber,
  Row,
  Select,
  Skeleton,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import React, { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, apiJson } from "../api/client";

type SummaryResponse = {
  ok: true;
  counts: {
    hydration?: {
      enabled_images_total: number;
      missing: Record<string, number>;
    };
    jobs: { counts: Record<string, number> };
    worker: { last_seen_at: string | null };
  };
  request_id: string;
};

type HydrationRunJob = {
  id: string;
  status: string;
  attempt: number;
  max_attempts: number;
  run_after: string | null;
  last_error: string | null;
  locked_by: string | null;
  locked_at: string | null;
  updated_at: string | null;
};

type HydrationRunItem = {
  id: string;
  type: string;
  status: string;
  criteria: Record<string, unknown>;
  cursor: Record<string, unknown>;
  total: number | null;
  processed: number;
  success: number;
  failed: number;
  started_at: string | null;
  finished_at: string | null;
  last_error: string | null;
  created_at: string | null;
  updated_at: string | null;
  latest_job: HydrationRunJob | null;
};

type HydrationRunsResponse = {
  ok: true;
  items: HydrationRunItem[];
  next_cursor: string;
  request_id: string;
};

type CreateHydrationRunResponse = {
  ok: true;
  hydration_run_id: string;
  job_id: string;
  request_id: string;
};

type ManualHydrateResponse = {
  ok: true;
  created: boolean;
  job_id: string;
  illust_id: string;
  request_id: string;
};

type RunActionResponse = {
  ok: true;
  hydration_run_id: string;
  status: string;
  job_status: string;
  request_id: string;
};

type RunAction = "pause" | "resume" | "cancel";

type BackfillFormValues = {
  missing: string[];
};

type ManualFormValues = {
  target_type: "illust_id" | "image_id";
  target_id: number;
};

const MISSING_OPTIONS = [
  { label: "标签", value: "tags" },
  { label: "尺寸与方向", value: "geometry" },
  { label: "R18 信息", value: "r18" },
  { label: "AI 信息", value: "ai" },
  { label: "作品类型（插画/漫画/动图）", value: "illust_type" },
  { label: "作者信息", value: "user" },
  { label: "标题", value: "title" },
  { label: "发布时间", value: "created_at" },
  { label: "热度（收藏/浏览/评论）", value: "popularity" },
];

const MISSING_LABELS: Record<string, string> = {
  tags: "标签",
  geometry: "尺寸与方向",
  r18: "R18 信息",
  ai: "AI 信息",
  illust_type: "作品类型",
  user: "作者信息",
  title: "标题",
  created_at: "发布时间",
  popularity: "热度（收藏/浏览/评论）",
};

function requestIdFromError(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  return err.body?.request_id ? String(err.body.request_id) : null;
}

function messageFromError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "未知错误";
}

function statusColor(status: string): string {
  switch (status) {
    case "running":
      return "processing";
    case "pending":
      return "blue";
    case "completed":
      return "success";
    case "failed":
      return "error";
    case "paused":
      return "warning";
    case "canceled":
      return "default";
    default:
      return "default";
  }
}

function statusLabel(status: string): string {
  switch (status) {
    case "pending":
      return "等待中";
    case "running":
      return "运行中";
    case "paused":
      return "已暂停";
    case "completed":
      return "已完成";
    case "failed":
      return "失败";
    case "canceled":
      return "已取消";
    default:
      return status || "未知";
  }
}

function typeLabel(type: string): string {
  if (type === "backfill") return "全量补全";
  if (type === "manual") return "手动补全";
  return type || "未知";
}

function missingSummary(criteria: Record<string, unknown>): string {
  const missing = criteria.missing;
  if (Array.isArray(missing)) {
    const values = missing.map((v) => String(v || "").trim()).filter((v) => v.length > 0);
    if (values.length > 0) {
      return values.map((v) => MISSING_LABELS[v] || v).join("、");
    }
  }
  return "默认（全部缺失字段）";
}

export function HydrationPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [actionState, setActionState] = useState<{ runId: string; action: RunAction } | null>(null);

  const [backfillForm] = Form.useForm<BackfillFormValues>();
  const [manualForm] = Form.useForm<ManualFormValues>();

  const summary = useQuery({
    queryKey: ["admin", "summary"],
    queryFn: () => apiJson<SummaryResponse>("/admin/api/summary"),
  });

  const hydrationStats = summary.data?.counts.hydration;
  const enabledImagesTotal = hydrationStats?.enabled_images_total ?? 0;
  const missingCounts = useMemo(() => hydrationStats?.missing ?? {}, [hydrationStats?.missing]);

  const missingOptionsWithCounts = useMemo(() => {
    return MISSING_OPTIONS.map((opt) => {
      const missing = Number(missingCounts[opt.value] ?? 0);
      const label = enabledImagesTotal > 0 ? `${opt.label}（缺 ${missing}）` : opt.label;
      return { label, value: opt.value };
    });
  }, [enabledImagesTotal, missingCounts]);

  const runs = useQuery({
    queryKey: ["admin", "hydration-runs", { statusFilter }],
    queryFn: () => {
      const query = new URLSearchParams({ limit: "30" });
      if (statusFilter !== "all") {
        query.set("status", statusFilter);
      }
      return apiJson<HydrationRunsResponse>(`/admin/api/hydration-runs?${query.toString()}`);
    },
  });

  const createBackfill = useMutation({
    mutationFn: (values: BackfillFormValues) => {
      const body =
        Array.isArray(values.missing) && values.missing.length > 0
          ? { type: "backfill", criteria: { missing: values.missing } }
          : { type: "backfill", criteria: {} };
      return apiJson<CreateHydrationRunResponse>("/admin/api/hydration-runs", {
        method: "POST",
        body: JSON.stringify(body),
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "hydration-runs"] });
      qc.invalidateQueries({ queryKey: ["admin", "summary"] });
    },
  });

  const createManual = useMutation({
    mutationFn: (values: ManualFormValues) => {
      const body = values.target_type === "image_id" ? { image_id: values.target_id } : { illust_id: values.target_id };
      return apiJson<ManualHydrateResponse>("/admin/api/hydration-runs/manual", {
        method: "POST",
        body: JSON.stringify(body),
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "hydration-runs"] });
      qc.invalidateQueries({ queryKey: ["admin", "summary"] });
    },
  });

  const runAction = useMutation({
    mutationFn: ({ runId, action }: { runId: string; action: RunAction }) =>
      apiJson<RunActionResponse>(`/admin/api/hydration-runs/${encodeURIComponent(runId)}/${action}`, {
        method: "POST",
      }),
    onMutate: (variables) => {
      setActionState({ runId: variables.runId, action: variables.action });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "hydration-runs"] });
      qc.invalidateQueries({ queryKey: ["admin", "summary"] });
    },
    onSettled: () => {
      setActionState(null);
    },
  });

  const jobsCounts = summary.data?.counts.jobs.counts ?? {};
  const pendingJobs = jobsCounts.pending ?? 0;
  const runningJobs = jobsCounts.running ?? 0;
  const failedJobs = jobsCounts.failed ?? 0;
  const workerLastSeen = summary.data?.counts.worker.last_seen_at ?? null;

  const columns: ColumnsType<HydrationRunItem> = useMemo(
    () => [
      { title: "运行ID", dataIndex: "id", key: "id", width: 100 },
      {
        title: "类型",
        dataIndex: "type",
        key: "type",
        width: 120,
        render: (value: string) => typeLabel(value),
      },
      {
        title: "状态",
        key: "status",
        width: 120,
        render: (_, row) => <Tag color={statusColor(String(row.status || ""))}>{statusLabel(row.status)}</Tag>,
      },
      {
        title: "进度",
        key: "progress",
        width: 220,
        render: (_, row) => {
          const total = row.total;
          const done = row.processed;
          const overall = total !== null && total > 0 ? `${done}/${total}` : `${done}`;
          return `${overall}（成功:${row.success}，失败:${row.failed}）`;
        },
      },
      {
        title: "补全范围",
        key: "criteria",
        width: 260,
        render: (_, row) => missingSummary(row.criteria || {}),
      },
      {
        title: "关联任务",
        key: "latest_job",
        width: 220,
        render: (_, row) => {
          if (!row.latest_job) return "-";
          return `#${row.latest_job.id} ${statusLabel(row.latest_job.status)} ${row.latest_job.attempt}/${row.latest_job.max_attempts}`;
        },
      },
      {
        title: "最近更新",
        dataIndex: "updated_at",
        key: "updated_at",
        width: 180,
        render: (value: string | null) => value || "-",
      },
      {
        title: "错误信息",
        key: "last_error",
        width: 280,
        render: (_, row) => row.last_error || row.latest_job?.last_error || "-",
      },
      {
        title: "操作",
        key: "actions",
        width: 260,
        render: (_, row) => {
          const status = String(row.status || "");
          const pauseDisabled = status !== "pending" && status !== "running";
          const resumeDisabled = status !== "paused";
          const cancelDisabled = status !== "pending" && status !== "running" && status !== "paused";

          const isPauseLoading = runAction.isPending && actionState?.runId === row.id && actionState?.action === "pause";
          const isResumeLoading = runAction.isPending && actionState?.runId === row.id && actionState?.action === "resume";
          const isCancelLoading = runAction.isPending && actionState?.runId === row.id && actionState?.action === "cancel";

          return (
            <Space wrap>
              <Button
                size="small"
                disabled={pauseDisabled}
                loading={isPauseLoading}
                onClick={() => runAction.mutate({ runId: row.id, action: "pause" })}
              >
                暂停
              </Button>
              <Button
                size="small"
                disabled={resumeDisabled}
                loading={isResumeLoading}
                onClick={() => runAction.mutate({ runId: row.id, action: "resume" })}
              >
                恢复
              </Button>
              <Button
                size="small"
                danger
                disabled={cancelDisabled}
                loading={isCancelLoading}
                onClick={() => runAction.mutate({ runId: row.id, action: "cancel" })}
              >
                取消
              </Button>
            </Space>
          );
        },
      },
    ],
    [actionState, runAction],
  );

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        元数据补全管理
      </Typography.Title>

      <Row gutter={[16, 16]}>
        <Col xs={24} md={12} xl={8}>
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
                <Typography.Text>工作线程心跳: {workerLastSeen || "（暂无）"}</Typography.Text>
                <Typography.Text>等待任务: {pendingJobs}</Typography.Text>
                <Typography.Text>运行任务: {runningJobs}</Typography.Text>
                <Typography.Text>失败任务: {failedJobs}</Typography.Text>
                <Space wrap>
                  <Button size="small" onClick={() => navigate("/admin/jobs")}>打开任务页</Button>
                  <Button size="small" onClick={() => runs.refetch()} loading={runs.isFetching}>刷新运行列表</Button>
                </Space>
              </Space>
            )}
          </Card>
        </Col>

        <Col xs={24} md={12} xl={8}>
          <Card title="全量补全任务">
            <Form<BackfillFormValues>
              form={backfillForm}
              layout="vertical"
              initialValues={{ missing: MISSING_OPTIONS.map((item) => item.value) }}
              onFinish={(values) => createBackfill.mutate(values)}
            >
              <Form.Item label="补全字段" name="missing">
                <Checkbox.Group options={missingOptionsWithCounts} />
              </Form.Item>
              <Space wrap>
                <Button type="primary" htmlType="submit" loading={createBackfill.isPending}>
                  创建全量补全任务
                </Button>
                <Button onClick={() => backfillForm.resetFields()}>重置</Button>
              </Space>
            </Form>

            {createBackfill.isSuccess ? (
              <Alert
                type="success"
                showIcon
                style={{ marginTop: 12 }}
                message="全量补全任务已创建"
                description={`补全运行ID: ${createBackfill.data.hydration_run_id}，任务ID: ${createBackfill.data.job_id}，请求ID: ${createBackfill.data.request_id}`}
              />
            ) : null}
            {createBackfill.isError ? (
              <Alert
                type="error"
                showIcon
                style={{ marginTop: 12 }}
                message="创建全量补全任务失败"
                description={
                  requestIdFromError(createBackfill.error)
                    ? `请求ID: ${requestIdFromError(createBackfill.error)}（${messageFromError(createBackfill.error)}）`
                    : messageFromError(createBackfill.error)
                }
              />
            ) : null}
          </Card>
        </Col>

        <Col xs={24} md={24} xl={8}>
          <Card title="单图补全任务">
            <Form<ManualFormValues>
              form={manualForm}
              layout="vertical"
              initialValues={{ target_type: "illust_id", target_id: 0 }}
              onFinish={(values) => createManual.mutate(values)}
            >
              <Form.Item label="目标类型" name="target_type">
                <Select
                  options={[
                    { value: "illust_id", label: "作品ID（illust_id）" },
                    { value: "image_id", label: "图片ID（image_id）" },
                  ]}
                />
              </Form.Item>
              <Form.Item
                label="目标ID"
                name="target_id"
                rules={[
                  {
                    validator: async (_, value: number | undefined) => {
                      if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
                        throw new Error("目标ID必须大于0");
                      }
                    },
                  },
                ]}
              >
                <InputNumber min={1} style={{ width: "100%" }} placeholder="例如：12345678" />
              </Form.Item>
              <Button type="primary" htmlType="submit" loading={createManual.isPending}>
                创建单图补全任务
              </Button>
            </Form>

            {createManual.isSuccess ? (
              <Alert
                type="success"
                showIcon
                style={{ marginTop: 12 }}
                message={createManual.data.created ? "单图补全任务已创建" : "已有任务在处理中，已复用"}
                description={`作品ID: ${createManual.data.illust_id}，任务ID: ${createManual.data.job_id}，请求ID: ${createManual.data.request_id}`}
              />
            ) : null}
            {createManual.isError ? (
              <Alert
                type="error"
                showIcon
                style={{ marginTop: 12 }}
                message="创建单图补全任务失败"
                description={
                  requestIdFromError(createManual.error)
                    ? `请求ID: ${requestIdFromError(createManual.error)}（${messageFromError(createManual.error)}）`
                    : messageFromError(createManual.error)
                }
              />
            ) : null}
          </Card>
        </Col>
      </Row>

      <Card title="元数据覆盖率统计">
        {summary.isLoading ? (
          <Skeleton active />
        ) : summary.isError ? (
          <Alert
            type="error"
            showIcon
            message="加载覆盖率统计失败"
            description={requestIdFromError(summary.error) ? `请求ID: ${requestIdFromError(summary.error)}` : ""}
          />
        ) : enabledImagesTotal <= 0 ? (
          <Alert type="info" showIcon message="暂无可用图片" description="请先导入图片链接后再查看覆盖率统计。" />
        ) : (
          <Table
            size="small"
            pagination={false}
            rowKey={(row) => row.key}
            columns={[
              { title: "字段", dataIndex: "label", key: "label" },
              { title: "缺失", dataIndex: "missing", key: "missing", width: 120 },
              { title: "已具备", dataIndex: "present", key: "present", width: 120 },
              { title: "覆盖率", dataIndex: "coverage", key: "coverage", width: 120 },
            ]}
            dataSource={MISSING_OPTIONS.map((opt) => {
              const missing = Number(missingCounts[opt.value] ?? 0);
              const present = Math.max(0, enabledImagesTotal - missing);
              const coverage = enabledImagesTotal > 0 ? `${Math.round((present / enabledImagesTotal) * 100)}%` : "0%";
              return { key: opt.value, label: opt.label, missing, present, coverage };
            })}
          />
        )}
      </Card>

      {runAction.isError ? (
        <Alert
          type="error"
          showIcon
          message="补全任务操作失败"
          description={
            requestIdFromError(runAction.error)
              ? `请求ID: ${requestIdFromError(runAction.error)}（${messageFromError(runAction.error)}）`
              : messageFromError(runAction.error)
          }
        />
      ) : null}

      <Card title="补全运行列表">
        <Space wrap style={{ marginBottom: 12 }}>
          <Typography.Text>状态筛选:</Typography.Text>
          <Select
            value={statusFilter}
            onChange={(value) => setStatusFilter(value)}
            style={{ width: 200 }}
            options={[
              { value: "all", label: "全部" },
              { value: "pending", label: "等待中" },
              { value: "running", label: "运行中" },
              { value: "paused", label: "已暂停" },
              { value: "completed", label: "已完成" },
              { value: "failed", label: "失败" },
              { value: "canceled", label: "已取消" },
            ]}
          />
          <Button onClick={() => runs.refetch()} loading={runs.isFetching}>刷新</Button>
        </Space>

        {runs.isLoading ? (
          <Skeleton active />
        ) : runs.isError ? (
          <Alert
            type="error"
            showIcon
            message="加载补全运行列表失败"
            description={requestIdFromError(runs.error) ? `请求ID: ${requestIdFromError(runs.error)}` : ""}
          />
        ) : !runs.data ? (
          <Skeleton active />
        ) : runs.data.items.length === 0 ? (
          <Alert type="info" showIcon message="暂无补全任务" description="请先创建一个全量补全任务。" />
        ) : (
          <>
            <Typography.Text type="secondary">请求ID: {runs.data.request_id}</Typography.Text>
            <Table<HydrationRunItem>
              rowKey={(row) => row.id}
              columns={columns}
              dataSource={runs.data.items}
              pagination={false}
              size="small"
              scroll={{ x: 1900 }}
              style={{ marginTop: 12 }}
            />
          </>
        )}
      </Card>
    </Space>
  );
}
