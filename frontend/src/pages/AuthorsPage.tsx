import { Button, Space, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import React from "react";
import { useNavigate } from "react-router-dom";

import { CursorTableCard } from "../admin/CursorTableCard";
import { apiJson } from "../api/client";
import { publicApiKeyHeaders } from "../auth/publicApiKeyStorage";
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

const baseColumns: ColumnsType<AuthorItem> = [
  { title: "作者ID", dataIndex: "user_id", key: "user_id" },
  { title: "作者名", dataIndex: "user_name", key: "user_name" },
  { title: "图片数量", dataIndex: "count_images", key: "count_images" },
];

export function AuthorsPage() {
  const navigate = useNavigate();

  const { query, items, nextCursor, listRequestId, loadMore } = useCursorList<AuthorItem, AuthorsListResponse>({
    queryKey: ["public", "authors", { limit: 50 }],
    getItemId: (item) => item.user_id,
    fetchPage: (cursor) => {
      const sp = new URLSearchParams({ limit: "50" });
      if (cursor) sp.set("cursor", cursor);
      // Public route: needs X-API-Key when PUBLIC_API_KEY_REQUIRED (same storage as Playground).
      return apiJson<AuthorsListResponse>(`/authors?${sp.toString()}`, {
        headers: publicApiKeyHeaders(),
      });
    },
  });

  const columns: ColumnsType<AuthorItem> = [
    ...baseColumns,
    {
      title: "操作",
      key: "actions",
      render: (_, row) => (
        <Button
          size="small"
          onClick={() =>
            navigate(`/admin/random?format=image&user_id=${encodeURIComponent(String(row.user_id))}`)
          }
        >
          按此作者随机一张
        </Button>
      ),
    },
  ];

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        作者列表
      </Typography.Title>

      <CursorTableCard<AuthorItem>
        columns={columns}
        items={items}
        rowKey={(row) => row.user_id}
        query={query}
        loadMore={loadMore}
        nextCursor={nextCursor}
        listRequestId={listRequestId}
        errorMessage="加载作者列表失败"
        emptyMessage="暂无作者数据"
        emptyDescription="请先导入图片并执行补全。"
      />
    </Space>
  );
}
