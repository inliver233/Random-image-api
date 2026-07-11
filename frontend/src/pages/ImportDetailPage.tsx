import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Descriptions, Popconfirm, Progress, Skeleton, Space, Typography } from "antd";
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

type ImportRollbackResponse = {
  ok: true;
  mode: "disable" | "delete";
  updated: number;
  request_id: string;
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
  const qc = useQueryClient();
  const idRaw = String(params.id || "").trim();
  const id = idRaw && /^\d+$/.test(idRaw) ? idRaw : "";
  const [actionAlert, setActionAlert] = React.useState<{ type: "success" | "error"; message: string; requestId: string | null } | null>(
    null,
  );

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

  const rollback = useMutation({
    mutationFn: (mode: "disable" | "delete") =>
      apiJson<ImportRollbackResponse>(`/admin/api/imports/${id}/rollback`, {
        method: "POST",
        body: JSON.stringify({ mode }),
      }),
    onMutate: () => setActionAlert(null),
    onSuccess: (data) => {
      const modeLabel = data.mode === "disable" ? "禁用" : "标记删除";
      setActionAlert({
        type: "success",
        message: `回滚完成（${modeLabel}），影响 ${data.updated} 张图片`,
        requestId: data.request_id,
      });
      qc.invalidateQueries({ queryKey: ["admin", "imports", id] });
      qc.invalidateQueries({ queryKey: ["admin", "images"] });
      qc.invalidateQueries({ queryKey: ["admin", "summary"] });
    },
    onError: (err) => {
      setActionAlert({ type: "error", message: messageFromError(err), requestId: requestIdFromError(err) });
    },
  });

  if (!id) {
    return <Alert type="error" showIcon message="导入ID不合法" />;
  }

  const jobStatus = String(query.data?.item.job?.status || "");
  const rollbackDisabled = jobStatus === "pending" || jobStatus === "running" || rollback.isPending;

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        导入任务 #{id}
      </Typography.Title>

      {actionAlert ? (
        <Alert type={actionAlert.type} showIcon message={actionAlert.message} description={actionAlert.requestId ? `请求ID: ${actionAlert.requestId}` : ""} />
      ) : null}

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

          <Card
            title="导入概览"
            extra={
              <Space wrap>
                <Popconfirm
                  title="确定禁用本批次导入的图片？"
                  description="会将 created_import_id 匹配的图片 status 设为禁用（可再启用）。"
                  okText="禁用"
                  cancelText="取消"
                  disabled={rollbackDisabled}
                  onConfirm={() => rollback.mutate("disable")}
                >
                  <Button disabled={rollbackDisabled} loading={rollback.isPending && rollback.variables === "disable"}>
                    回滚禁用
                  </Button>
                </Popconfirm>
                <Popconfirm
                  title="确定标记删除本批次导入的图片？"
                  description="会将 created_import_id 匹配的图片 status 设为删除标记（软删除）。"
                  okText="标记删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  disabled={rollbackDisabled}
                  onConfirm={() => rollback.mutate("delete")}
                >
                  <Button danger disabled={rollbackDisabled} loading={rollback.isPending && rollback.variables === "delete"}>
                    回滚删除
                  </Button>
                </Popconfirm>
              </Space>
            }
          >
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
