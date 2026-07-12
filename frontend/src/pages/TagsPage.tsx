import { Button, Space, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import React from "react";
import { useNavigate } from "react-router-dom";

import { CursorTableCard } from "../admin/CursorTableCard";
import { apiJson } from "../api/client";
import { publicApiKeyHeaders } from "../auth/publicApiKeyStorage";
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

const baseColumns: ColumnsType<TagItem> = [
  { title: "标签", dataIndex: "name", key: "name" },
  { title: "翻译", dataIndex: "translated_name", key: "translated_name" },
  { title: "图片数量", dataIndex: "count_images", key: "count_images" },
];

export function TagsPage() {
  const navigate = useNavigate();

  const { query, items, nextCursor, listRequestId, loadMore } = useCursorList<TagItem, TagsListResponse>({
    queryKey: ["public", "tags", { limit: 50 }],
    getItemId: (item) => item.name,
    fetchPage: (cursor) => {
      const sp = new URLSearchParams({ limit: "50" });
      if (cursor) sp.set("cursor", cursor);
      // Public route: needs X-API-Key when PUBLIC_API_KEY_REQUIRED (same storage as Playground).
      return apiJson<TagsListResponse>(`/tags?${sp.toString()}`, {
        headers: publicApiKeyHeaders(),
      });
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

      <CursorTableCard<TagItem>
        columns={columns}
        items={items}
        rowKey={(row) => row.name}
        query={query}
        loadMore={loadMore}
        nextCursor={nextCursor}
        listRequestId={listRequestId}
        errorMessage="加载标签失败"
        emptyMessage="暂无标签数据"
        emptyDescription="请先导入图片并执行元数据补全。"
      />
    </Space>
  );
}
