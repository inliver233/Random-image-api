import { useQuery } from "@tanstack/react-query";
import { Alert, Card, Skeleton, Space, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import React from "react";

import { ApiError, apiJson } from "../api/client";

type AuditItem = {
  id: string;
  created_at: string;
  actor: string | null;
  action: string;
  resource: string;
  record_id: string | null;
  request_id: string | null;
  detail_json: Record<string, unknown> | null;
};

type AuditListResponse = {
  ok: true;
  items: AuditItem[];
  next_cursor: string;
  request_id: string;
};

function requestIdFromError(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  return err.body?.request_id ? String(err.body.request_id) : null;
}

const columns: ColumnsType<AuditItem> = [
  { title: "时间", dataIndex: "created_at", key: "created_at" },
  { title: "操作者", dataIndex: "actor", key: "actor" },
  { title: "动作", dataIndex: "action", key: "action" },
  { title: "资源", dataIndex: "resource", key: "resource" },
  { title: "记录ID", dataIndex: "record_id", key: "record_id" },
  { title: "请求ID", dataIndex: "request_id", key: "request_id" },
  {
    title: "详情",
    key: "detail_json",
    render: (_, row) => (
      <pre style={{ margin: 0, maxWidth: 360, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
        {row.detail_json ? JSON.stringify(row.detail_json) : ""}
      </pre>
    ),
  },
];

export function AuditPage() {
  const query = useQuery({
    queryKey: ["admin", "audit", { limit: 50 }],
    queryFn: () => apiJson<AuditListResponse>("/admin/api/audit?limit=50"),
  });

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        审计日志
      </Typography.Title>

      {query.isLoading ? (
        <Skeleton active />
      ) : query.isError ? (
        <Alert
          type="error"
          showIcon
          message="加载审计日志失败"
          description={requestIdFromError(query.error) ? `请求ID: ${requestIdFromError(query.error)}` : ""}
        />
      ) : !query.data ? (
        <Skeleton active />
      ) : query.data.items.length === 0 ? (
        <Alert type="info" showIcon message="暂无审计日志" description="执行后台操作后，这里会显示审计记录。" />
      ) : (
        <Card>
          <Typography.Text type="secondary">请求ID: {query.data.request_id}</Typography.Text>
          <Table<AuditItem>
            rowKey={(row) => row.id}
            columns={columns}
            dataSource={query.data.items}
            pagination={false}
            size="small"
            style={{ marginTop: 12 }}
          />
        </Card>
      )}
    </Space>
  );
}

