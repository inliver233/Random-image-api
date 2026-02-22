import { useQuery } from "@tanstack/react-query";
import { Alert, Card, Descriptions, Progress, Skeleton, Space, Typography } from "antd";
import React from "react";
import { useParams } from "react-router-dom";

import { ApiError, apiJson } from "../api/client";

type ImportDetailResponse = {
  ok: true;
  item: {
    import: {
      id: string;
      created_at: string;
      created_by: string;
      source: string;
      total: number;
      accepted: number;
      success: number;
      failed: number;
    };
    job:
      | {
          id: string;
          type: string;
          status: string;
          attempt: number;
          max_attempts: number;
          last_error: string | null;
        }
      | null;
    detail: Record<string, unknown>;
  };
  request_id: string;
};

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

export function ImportDetailPage() {
  const params = useParams();
  const idRaw = String(params.id || "").trim();
  const id = idRaw && /^\d+$/.test(idRaw) ? idRaw : "";

  const query = useQuery({
    queryKey: ["admin", "imports", id],
    enabled: Boolean(id),
    queryFn: () => apiJson<ImportDetailResponse>(`/admin/api/imports/${id}`),
    refetchInterval: (state) => {
      const data = state.state.data as ImportDetailResponse | undefined;
      const status = String(data?.item.job?.status || "");
      return status === "pending" || status === "running" ? 1000 : false;
    },
  });

  if (!id) {
    return <Alert type="error" showIcon message="导入ID不合法" />;
  }

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        导入任务 #{id}
      </Typography.Title>

      {query.isLoading ? (
        <Skeleton active />
      ) : query.isError ? (
        <Alert
          type="error"
          showIcon
          message="加载导入详情失败"
          description={requestIdFromError(query.error) ? `请求ID: ${requestIdFromError(query.error)}` : ""}
        />
      ) : (
        <>
          <Typography.Text type="secondary">请求ID: {query.data?.request_id}</Typography.Text>

          {query.data?.item.job && (query.data.item.job.status === "pending" || query.data.item.job.status === "running") ? (
            <Alert type="info" showIcon message="导入进行中（每1秒自动刷新）" />
          ) : null}

          {query.data?.item.job && query.data.item.job.status === "pending" ? (
            <Alert
              type="warning"
              showIcon
              message="任务仍在等待执行"
              description="如果长时间不动，请确认工作线程服务已经启动。"
            />
          ) : null}

          {(() => {
            const accepted = Number(query.data?.item.import.accepted || 0);
            const success = Number(query.data?.item.import.success || 0);
            const failed = Number(query.data?.item.import.failed || 0);
            const status = String(query.data?.item.job?.status || "");
            const waiting = status === "pending" || status === "running";
            if (accepted > 0 && success === 0 && failed === 0 && waiting) {
              return (
                <Alert
                  type="warning"
                  showIcon
                  message="尚未开始处理导入内容"
                  description={`已接收 ${accepted} 条，但 success=0 / failed=0。通常是 worker 未启动或任务仍在等待。请确认 docker compose 已启动 worker 服务。`}
                />
              );
            }
            return null;
          })()}

          <Card title="导入概览">
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="创建时间">{query.data?.item.import.created_at}</Descriptions.Item>
              <Descriptions.Item label="创建人">{query.data?.item.import.created_by}</Descriptions.Item>
              <Descriptions.Item label="来源">{query.data?.item.import.source}</Descriptions.Item>
              <Descriptions.Item label="总数">{query.data?.item.import.total}</Descriptions.Item>
              <Descriptions.Item label="接收">{query.data?.item.import.accepted}</Descriptions.Item>
              <Descriptions.Item label="成功">{query.data?.item.import.success}</Descriptions.Item>
              <Descriptions.Item label="失败">{query.data?.item.import.failed}</Descriptions.Item>
            </Descriptions>

            {query.data?.item.job ? (
              <div style={{ marginTop: 12 }}>
                <Progress
                  percent={(() => {
                    const accepted = Number(query.data?.item.import.accepted || 0);
                    const total = Number(query.data?.item.import.total || 0);
                    const base = accepted > 0 ? accepted : total > 0 ? total : 0;
                    const success = Number(query.data?.item.import.success || 0);
                    if (base <= 0) return 0;
                    const percent = Math.round((success / base) * 100);
                    return Math.max(0, Math.min(100, percent));
                  })()}
                  status={
                    query.data.item.job.status === "completed"
                      ? "success"
                      : query.data.item.job.status === "failed" || query.data.item.job.status === "dlq"
                        ? "exception"
                        : "active"
                  }
                />
              </div>
            ) : null}
          </Card>

          <Card title="关联任务">
            {query.data?.item.job ? (
              <Descriptions size="small" column={2}>
                <Descriptions.Item label="任务ID">{query.data.item.job.id}</Descriptions.Item>
                <Descriptions.Item label="任务类型">{query.data.item.job.type}</Descriptions.Item>
                <Descriptions.Item label="状态">{statusLabel(query.data.item.job.status)}</Descriptions.Item>
                <Descriptions.Item label="重试次数">{query.data.item.job.attempt}</Descriptions.Item>
                <Descriptions.Item label="最大重试">{query.data.item.job.max_attempts}</Descriptions.Item>
                <Descriptions.Item label="最后错误">{query.data.item.job.last_error || ""}</Descriptions.Item>
              </Descriptions>
            ) : (
              <Alert type="info" showIcon message="暂无关联任务" />
            )}
          </Card>

          <Card title="详情数据（结构化）">
            <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>
              {JSON.stringify(query.data?.item.detail || {}, null, 2)}
            </pre>
          </Card>
        </>
      )}
    </Space>
  );
}

