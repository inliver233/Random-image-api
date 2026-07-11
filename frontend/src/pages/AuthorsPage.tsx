import { useMutation, useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Skeleton, Space, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import React from "react";

import { ApiError, apiJson } from "../api/client";

type AuthorItem = {
  user_id: string;
  user_name: string | null;
  count_images: number | null;
};

type AuthorsListResponse = {
  ok: true;
  items: AuthorItem[];
  next_cursor: string;
  request_id: string;
};

function requestIdFromError(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  return err.body?.request_id ? String(err.body.request_id) : null;
}

const columns: ColumnsType<AuthorItem> = [
  { title: "作者ID", dataIndex: "user_id", key: "user_id" },
  { title: "作者名", dataIndex: "user_name", key: "user_name" },
  { title: "图片数量", dataIndex: "count_images", key: "count_images" },
];

export function AuthorsPage() {
  const [items, setItems] = React.useState<AuthorItem[]>([]);
  const [nextCursor, setNextCursor] = React.useState("");
  const [requestId, setRequestId] = React.useState<string | null>(null);
  const [loadMoreError, setLoadMoreError] = React.useState<string | null>(null);

  const query = useQuery({
    queryKey: ["public", "authors", { limit: 50 }],
    queryFn: () => apiJson<AuthorsListResponse>("/authors?limit=50"),
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
      return apiJson<AuthorsListResponse>(`/authors?${sp.toString()}`);
    },
    onSuccess: (data) => {
      setItems((prev) => {
        const seen = new Set(prev.map((x) => x.user_id));
        const merged = [...prev];
        for (const item of data.items) {
          if (!seen.has(item.user_id)) merged.push(item);
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
        作者列表
      </Typography.Title>

      {loadMoreError ? <Alert type="error" showIcon message={loadMoreError} /> : null}

      {query.isLoading ? (
        <Skeleton active />
      ) : query.isError ? (
        <Alert
          type="error"
          showIcon
          message="加载作者列表失败"
          description={requestIdFromError(query.error) ? `请求ID: ${requestIdFromError(query.error)}` : ""}
        />
      ) : !query.data ? (
        <Skeleton active />
      ) : items.length === 0 ? (
        <Alert type="info" showIcon message="暂无作者数据" description="请先导入图片并执行补全。" />
      ) : (
        <Card>
          {requestId ? <Typography.Text type="secondary">请求ID: {requestId}</Typography.Text> : null}
          <Table<AuthorItem>
            rowKey={(row) => row.user_id}
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
