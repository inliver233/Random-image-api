import { Alert, Button, Card, Skeleton, Space, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import React from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, apiJson } from "../api/client";
import { useCursorList } from "../hooks/useCursorList";

type TagItem = {
  name: string;
  translated_name: string | null;
  count_images: number | null;
};

type TagsListResponse = {
  ok: true;
  items: TagItem[];
  next_cursor: string;
  request_id: string;
};

function requestIdFromError(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  return err.body?.request_id ? String(err.body.request_id) : null;
}

const baseColumns: ColumnsType<TagItem> = [
  { title: "标签", dataIndex: "name", key: "name" },
  { title: "翻译", dataIndex: "translated_name", key: "translated_name" },
  { title: "图片数量", dataIndex: "count_images", key: "count_images" },
];

export function TagsPage() {
  const navigate = useNavigate();

  const {
    query,
    items,
    nextCursor,
    listRequestId: requestId,
    loadMore,
  } = useCursorList<TagItem, TagsListResponse>({
    queryKey: ["public", "tags", { limit: 50 }],
    getItemId: (item) => item.name,
    fetchPage: (cursor) => {
      const sp = new URLSearchParams({ limit: "50" });
      if (cursor) sp.set("cursor", cursor);
      return apiJson<TagsListResponse>(`/tags?${sp.toString()}`);
    },
  });

  const columns: ColumnsType<TagItem> = [
    ...baseColumns,
    {
      title: "操作",
      key: "actions",
      render: (_, row) => (
        <Button
          size="small"
          onClick={() => navigate(`/admin/random?format=image&included_tags=${encodeURIComponent(row.name)}`)}
        >
          按此标签随机一张
        </Button>
      ),
    },
  ];

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        标签列表
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
          message="加载标签失败"
          description={requestIdFromError(query.error) ? `请求ID: ${requestIdFromError(query.error)}` : ""}
        />
      ) : !query.data ? (
        <Skeleton active />
      ) : items.length === 0 ? (
        <Alert
          type="info"
          showIcon
          message="暂无标签数据"
          description="请先导入图片并执行元数据补全。"
        />
      ) : (
        <Card>
          {requestId ? <Typography.Text type="secondary">请求ID: {requestId}</Typography.Text> : null}
          <Table<TagItem>
            rowKey={(row) => row.name}
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
