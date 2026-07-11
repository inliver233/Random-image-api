import { useMutation, useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Skeleton, Space, Table, Typography } from "antd";
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
  const [items, setItems] = React.useState<AuditItem[]>([]);
  const [nextCursor, setNextCursor] = React.useState("");
  const [requestId, setRequestId] = React.useState<string | null>(null);
  const [loadMoreError, setLoadMoreError] = React.useState<string | null>(null);

  const query = useQuery({
    queryKey: ["admin", "audit", { limit: 50 }],
    queryFn: () => apiJson<AuditListResponse>("/admin/api/audit?limit=50"),
  });

  React.useEffect(() => {
    if (!query.data) return;
    setItems(query.data.items);
    setNextCursor(query.data.next_cursor || "");
    setRequestId(query.data.request_id);
    setLoadMoreError(null);
  }, [query.data]);

  const loadMore = useMutation({
    mutationFn: (cursor: string) => {
      const sp = new URLSearchParams({ limit: "50", cursor });
      return apiJson<AuditListResponse>(`/admin/api/audit?${sp.toString()}`);
    },
    onSuccess: (data) => {
      setItems((prev) => {
        const seen = new Set(prev.map((x) => x.id));
        const merged = [...prev];
        for (const item of data.items) {
          if (!seen.has(item.id)) merged.push(item);
        }
        return merged;
      });
      setNextCursor(data.next_cursor || "");
      setRequestId(data.request_id);
      setLoadMoreError(null);
    },
    onError: (err) => {
      setLoadMoreError(err instanceof Error ? err.message : "加载更多失败");
    },
  });

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        审计日志
      </Typography.Title>

      {loadMoreError ? <Alert type="error" showIcon message={loadMoreError} /> : null}

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
      ) : items.length === 0 ? (
        <Alert type="info" showIcon message="暂无审计日志" description="执行后台操作后，这里会显示审计记录。" />
      ) : (
        <Card>
          {requestId ? <Typography.Text type="secondary">请求ID: {requestId}</Typography.Text> : null}
          <Table<AuditItem>
            rowKey={(row) => row.id}
            columns={columns}
            dataSource={items}
            pagination={false}
            size="small"
            style={{ marginTop: 12 }}
          />
          {nextCursor ? (
            <div style={{ marginTop: 12 }}>
              <Button onClick={() => loadMore.mutate(nextCursor)} loading={loadMore.isPending}>
                加载更多
              </Button>
            </div>
          ) : null}
        </Card>
      )}
    </Space>
  );
}
