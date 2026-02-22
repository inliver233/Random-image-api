import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Descriptions, Drawer, Select, Skeleton, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import React, { useState } from "react";

import { ApiError, apiJson } from "../api/client";

type JobItem = {
  id: string;
  type: string;
  status: string;
  priority: number;
  run_after: string | null;
  attempt: number;
  max_attempts: number;
  last_error: string | null;
  locked_by: string | null;
  locked_at: string | null;
  ref_type: string | null;
  ref_id: string | null;
  created_at: string;
  updated_at: string;
};

type JobsListResponse = {
  ok: true;
  items: JobItem[];
  next_cursor: string;
  request_id: string;
};

type JobDetailResponse = {
  ok: true;
  item: JobItem & { payload: unknown; payload_json: string };
  request_id: string;
};

type JobActionResponse = { ok: true; job_id: string; status: string; request_id: string };

function requestIdFromError(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  return err.body?.request_id ? String(err.body.request_id) : null;
}

function statusLabel(status: string): string {
  switch (status) {
    case "pending":
      return "等待中";
    case "running":
      return "运行中";
    case "paused":
      return "已暂停";
    case "canceled":
      return "已取消";
    case "completed":
      return "已完成";
    case "failed":
      return "失败";
    case "dlq":
      return "死信";
    default:
      return status || "未知";
  }
}

function typeLabel(type: string): string {
  switch (type) {
    case "import_images":
      return "导入图片";
    case "hydrate_metadata":
      return "补全元数据";
    case "proxy_probe":
      return "代理探测";
    case "easy_proxies_import":
      return "导入 easy-proxies";
    case "heal_url":
      return "修复 URL";
    default:
      return type || "未知";
  }
}

function statusTag(status: string) {
  const label = statusLabel(status);
  const color =
    status === "running"
      ? "processing"
      : status === "pending"
        ? "blue"
        : status === "completed"
          ? "success"
          : status === "failed"
            ? "error"
            : status === "paused"
              ? "warning"
              : status === "dlq"
                ? "magenta"
                : "default";
  return <Tag color={color}>{label}</Tag>;
}

export function JobsPage() {
  const qc = useQueryClient();
  const [status, setStatus] = useState<string>("failed");
  const [type, setType] = useState<string>("all");
  const [detailJobId, setDetailJobId] = useState<string | null>(null);
  const [actionAlert, setActionAlert] = useState<{ type: "success" | "error"; message: string; requestId: string | null } | null>(null);

  const query = useQuery({
    queryKey: ["admin", "jobs", { status, type, limit: 50 }],
    queryFn: () => {
      const sp = new URLSearchParams({ limit: "50" });
      if (status) sp.set("status", status);
      if (type !== "all") sp.set("type", type);
      return apiJson<JobsListResponse>(`/admin/api/jobs?${sp.toString()}`);
    },
  });

  const jobDetail = useQuery({
    queryKey: ["admin", "jobs", "detail", detailJobId],
    enabled: Boolean(detailJobId),
    queryFn: () => apiJson<JobDetailResponse>(`/admin/api/jobs/${encodeURIComponent(detailJobId || "")}`),
  });

  const action = useMutation({
    mutationFn: (vars: { jobId: string; action: "retry" | "cancel" | "move-to-dlq" }) =>
      apiJson<JobActionResponse>(`/admin/api/jobs/${encodeURIComponent(vars.jobId)}/${vars.action}`, { method: "POST" }),
    onMutate: () => {
      setActionAlert(null);
    },
    onSuccess: (data) => {
      setActionAlert({ type: "success", message: `操作成功：任务 #${data.job_id} 状态已更新为 ${statusLabel(data.status)}`, requestId: data.request_id });
      qc.invalidateQueries({ queryKey: ["admin", "jobs"] });
      qc.invalidateQueries({ queryKey: ["admin", "jobs", "detail", String(data.job_id)] });
    },
    onError: (err) => {
      setActionAlert({ type: "error", message: err instanceof Error ? err.message : "操作失败", requestId: requestIdFromError(err) });
    },
  });

  const columns: ColumnsType<JobItem> = [
    {
      title: "任务ID",
      dataIndex: "id",
      key: "id",
      width: 110,
      render: (value: string) => (
        <Button type="link" size="small" onClick={() => setDetailJobId(String(value))}>
          #{value}
        </Button>
      ),
    },
    { title: "类型", dataIndex: "type", key: "type", width: 140, render: (value) => typeLabel(String(value)) },
    { title: "状态", dataIndex: "status", key: "status", width: 110, render: (value) => statusTag(String(value)) },
    {
      title: "重试次数",
      key: "attempt",
      width: 120,
      render: (_, row) => `${row.attempt} / ${row.max_attempts}`,
    },
    { title: "优先级", dataIndex: "priority", key: "priority", width: 90 },
    { title: "下次执行", dataIndex: "run_after", key: "run_after", width: 180, render: (value) => value || "-" },
    { title: "锁定者", dataIndex: "locked_by", key: "locked_by", width: 140, render: (value) => value || "-" },
    { title: "锁定时间", dataIndex: "locked_at", key: "locked_at", width: 180, render: (value) => value || "-" },
    {
      title: "引用",
      key: "ref",
      width: 160,
      render: (_, row) => (row.ref_type ? `${row.ref_type}:${row.ref_id || ""}` : "-"),
    },
    {
      title: "错误",
      dataIndex: "last_error",
      key: "last_error",
      width: 240,
      render: (value) => (value ? String(value) : "-"),
    },
    { title: "更新时间", dataIndex: "updated_at", key: "updated_at", width: 180 },
    {
      title: "操作",
      key: "actions",
      width: 240,
      render: (_, row) => {
        const retryDisabled = row.status === "running";
        const cancelDisabled = row.status === "completed" || row.status === "canceled";
        const dlqDisabled = row.status === "dlq";

        const isRetryLoading = action.isPending && action.variables?.jobId === row.id && action.variables?.action === "retry";
        const isCancelLoading = action.isPending && action.variables?.jobId === row.id && action.variables?.action === "cancel";
        const isDlqLoading = action.isPending && action.variables?.jobId === row.id && action.variables?.action === "move-to-dlq";

        return (
          <Space wrap>
            <Button size="small" disabled={retryDisabled} loading={isRetryLoading} onClick={() => action.mutate({ jobId: row.id, action: "retry" })}>
              重试
            </Button>
            <Button size="small" danger disabled={cancelDisabled} loading={isCancelLoading} onClick={() => action.mutate({ jobId: row.id, action: "cancel" })}>
              取消
            </Button>
            <Button size="small" disabled={dlqDisabled} loading={isDlqLoading} onClick={() => action.mutate({ jobId: row.id, action: "move-to-dlq" })}>
              死信
            </Button>
          </Space>
        );
      },
    },
  ];

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        任务队列
      </Typography.Title>

      <Space wrap>
        <Typography.Text>状态:</Typography.Text>
        <Select
          value={status}
          onChange={(value) => setStatus(value)}
          options={[
            { value: "failed", label: "失败" },
            { value: "pending", label: "等待中" },
            { value: "running", label: "运行中" },
            { value: "paused", label: "已暂停" },
            { value: "completed", label: "已完成" },
            { value: "dlq", label: "死信" },
          ]}
          style={{ width: 180 }}
        />
        <Typography.Text>类型:</Typography.Text>
        <Select
          value={type}
          onChange={(value) => setType(value)}
          options={[
            { value: "all", label: "全部" },
            { value: "import_images", label: "导入图片" },
            { value: "hydrate_metadata", label: "补全元数据" },
            { value: "proxy_probe", label: "代理探测" },
            { value: "easy_proxies_import", label: "导入 easy-proxies" },
            { value: "heal_url", label: "修复 URL" },
          ]}
          style={{ width: 200 }}
        />
        <Button onClick={() => query.refetch()} loading={query.isFetching}>
          刷新列表
        </Button>
      </Space>

      {actionAlert ? <Alert type={actionAlert.type} showIcon message={actionAlert.message} description={actionAlert.requestId ? `请求ID: ${actionAlert.requestId}` : ""} /> : null}

      <Card>
        {query.isLoading ? (
          <Skeleton active />
        ) : query.isError ? (
          <Alert
            type="error"
            showIcon
            message="加载任务列表失败"
            description={requestIdFromError(query.error) ? `请求ID: ${requestIdFromError(query.error)}` : ""}
          />
        ) : !query.data ? (
          <Skeleton active />
        ) : query.data.items.length === 0 ? (
          <Alert type="info" showIcon message="暂无任务" description="可先触发导入/补全/代理探测任务。" />
        ) : (
          <>
            <Typography.Text type="secondary">请求ID: {query.data.request_id}</Typography.Text>
            <Table<JobItem>
              rowKey={(row) => row.id}
              columns={columns}
              dataSource={query.data.items}
              pagination={false}
              size="small"
              scroll={{ x: 1600 }}
              style={{ marginTop: 12 }}
            />
          </>
        )}
      </Card>

      <Drawer
        open={Boolean(detailJobId)}
        onClose={() => setDetailJobId(null)}
        title={detailJobId ? `任务详情 #${detailJobId}` : "任务详情"}
        width={720}
      >
        {jobDetail.isLoading ? (
          <Skeleton active />
        ) : jobDetail.isError ? (
          <Alert
            type="error"
            showIcon
            message="加载任务详情失败"
            description={requestIdFromError(jobDetail.error) ? `请求ID: ${requestIdFromError(jobDetail.error)}` : ""}
          />
        ) : !jobDetail.data ? (
          <Skeleton active />
        ) : (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Typography.Text type="secondary">请求ID: {jobDetail.data.request_id}</Typography.Text>
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="任务ID">#{jobDetail.data.item.id}</Descriptions.Item>
              <Descriptions.Item label="类型">{typeLabel(jobDetail.data.item.type)}</Descriptions.Item>
              <Descriptions.Item label="状态">{statusLabel(jobDetail.data.item.status)}</Descriptions.Item>
              <Descriptions.Item label="优先级">{jobDetail.data.item.priority}</Descriptions.Item>
              <Descriptions.Item label="重试次数">{`${jobDetail.data.item.attempt} / ${jobDetail.data.item.max_attempts}`}</Descriptions.Item>
              <Descriptions.Item label="下次执行">{jobDetail.data.item.run_after || "-"}</Descriptions.Item>
              <Descriptions.Item label="锁定者">{jobDetail.data.item.locked_by || "-"}</Descriptions.Item>
              <Descriptions.Item label="锁定时间">{jobDetail.data.item.locked_at || "-"}</Descriptions.Item>
              <Descriptions.Item label="引用类型">{jobDetail.data.item.ref_type || "-"}</Descriptions.Item>
              <Descriptions.Item label="引用ID">{jobDetail.data.item.ref_id || "-"}</Descriptions.Item>
              <Descriptions.Item label="创建时间">{jobDetail.data.item.created_at}</Descriptions.Item>
              <Descriptions.Item label="更新时间">{jobDetail.data.item.updated_at}</Descriptions.Item>
              <Descriptions.Item label="错误信息" span={2}>
                {jobDetail.data.item.last_error || "-"}
              </Descriptions.Item>
            </Descriptions>

            <Card size="small" title="任务参数（结构化）">
              <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                {JSON.stringify(jobDetail.data.item.payload, null, 2)}
              </pre>
            </Card>
          </Space>
        )}
      </Drawer>
    </Space>
  );
}
