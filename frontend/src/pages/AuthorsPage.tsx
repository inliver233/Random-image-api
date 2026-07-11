import { Alert, Button, Card, Skeleton, Space, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import React from "react";

import { requestIdDescription } from "../admin/errors";
import { apiJson } from "../api/client";
import { useCursorList } from "../hooks/useCursorList";

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

const columns: ColumnsType<AuthorItem> = [
  { title: "作者ID", dataIndex: "user_id", key: "user_id" },
  { title: "作者名", dataIndex: "user_name", key: "user_name" },
  { title: "图片数量", dataIndex: "count_images", key: "count_images" },
];

export function AuthorsPage() {
  const {
    query,
    items,
    nextCursor,
    listRequestId: requestId,
    loadMore,
  } = useCursorList<AuthorItem, AuthorsListResponse>({
    queryKey: ["public", "authors", { limit: 50 }],
    getItemId: (item) => item.user_id,
    fetchPage: (cursor) => {
      const sp = new URLSearchParams({ limit: "50" });
      if (cursor) sp.set("cursor", cursor);
      return apiJson<AuthorsListResponse>(`/authors?${sp.toString()}`);
    },
  });

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        作者列表
      </Typography.Title>

      {loadMore.isError ? (
        <Alert type="error" showIcon message={loadMore.error instanceof Error ? loadMore.error.message : "加载更多失败"} />
      ) : null}

      {query.isLoading ? (
        <Skeleton active />
      ) : query.isError ? (
        <Alert
          type="error"
          showIcon
          message="加载作者列表失败"
          description={requestIdDescription(query.error)}
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
