import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, Card, Popconfirm, Select, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import React from "react";
import { useMemo, useState } from "react";

import { ActionAlerts } from "../admin/ActionAlerts";
import { dash } from "../admin/format";
import { QueryState } from "../admin/QueryState";
import { missingLabel } from "../admin/missingFields";
import { useActionAlerts } from "../admin/useActionAlerts";
import { apiJson, resolvePublicApiUrl } from "../api/client";
import { withPublicApiKeyQuery } from "../auth/publicApiKeyStorage";
import { useCursorList } from "../hooks/useCursorList";

type ImageItem = {
  id: string;
  illust_id: string;
  page_index: number;
  ext: string;
  status: number;
  width: number | null;
  height: number | null;
  orientation: number | null;
  x_restrict: number | null;
  ai_type: number | null;
  illust_type: number | null;
  bookmark_count: number | null;
  view_count: number | null;
  comment_count: number | null;
  user: { id: string | null; name: string | null };
  title: string | null;
  created_at_pixiv: string | null;
  original_url: string;
  proxy_path: string;
  tag_count: number;
  missing: string[];
};

type ImagesListResponse = {
  ok: true;
  items: ImageItem[];
  next_cursor: string;
  request_id: string;
};

type ManualHydrateResponse = {
  ok: true;
  created: boolean;
  job_id: string;
  illust_id: string;
  request_id: string;
};

type DeleteImageResponse = {
  ok: true;
  image_id: string;
  request_id: string;
};

type BulkDeleteImagesResponse = {
  ok: true;
  requested: number;
  deleted: number;
  missing: number;
  request_id: string;
};

type ClearImagesResponse = {
  ok: true;
  deleted_image_tags: number;
  deleted_images: number;
  deleted_tags: number;
  request_id: string;
};

export function ImagesPage() {
  const qc = useQueryClient();
  const [missing, setMissing] = useState<string[]>([]);
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const alerts = useActionAlerts();

  const {
    query,
    items,
    nextCursor,
    listRequestId,
    loadMore,
  } = useCursorList<ImageItem, ImagesListResponse>({
    queryKey: ["admin", "images", { limit: 50, missing }],
    getItemId: (item) => item.id,
    fetchPage: (cursor) => {
      const sp = new URLSearchParams({ limit: "50" });
      for (const key of missing) sp.append("missing", key);
      if (cursor) sp.set("cursor", cursor);
      return apiJson<ImagesListResponse>(`/admin/api/images?${sp.toString()}`);
    },
  });

  React.useEffect(() => {
    // Clear selection when the first page reloads (filter change or invalidate).
    setSelectedRowKeys([]);
  }, [query.data]);

  React.useEffect(() => {
    if (loadMore.isError) {
      alerts.setError(loadMore.error);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only re-run on loadMore error state
  }, [loadMore.isError, loadMore.error]);

  const deleteImage = useMutation({
    mutationFn: (imageId: string) =>
      apiJson<DeleteImageResponse>(`/admin/api/images/${encodeURIComponent(imageId)}`, { method: "DELETE" }),
    onMutate: () => alerts.clear(),
    onSuccess: (data) => {
      alerts.setSuccess(`图片已删除：#${data.image_id}`, data.request_id);
      setSelectedRowKeys((prev) => prev.filter((k) => String(k) !== String(data.image_id)));
      qc.invalidateQueries({ queryKey: ["admin", "images"] });
      qc.invalidateQueries({ queryKey: ["admin", "summary"] });
      qc.invalidateQueries({ queryKey: ["public", "tags"] });
    },
    onError: (err) => {
      alerts.setError(err);
    },
  });

  const bulkDelete = useMutation({
    mutationFn: (imageIds: string[]) =>
      apiJson<BulkDeleteImagesResponse>("/admin/api/images/bulk-delete", {
        method: "POST",
        body: JSON.stringify({ image_ids: imageIds.map((v) => Number(v)) }),
      }),
    onMutate: () => alerts.clear(),
    onSuccess: (data) => {
      alerts.setSuccess(`批量删除完成：请求 ${data.requested}，成功删除 ${data.deleted}，未找到 ${data.missing}`, data.request_id,);
      setSelectedRowKeys([]);
      qc.invalidateQueries({ queryKey: ["admin", "images"] });
      qc.invalidateQueries({ queryKey: ["admin", "summary"] });
      qc.invalidateQueries({ queryKey: ["public", "tags"] });
    },
    onError: (err) => {
      alerts.setError(err);
    },
  });

  const clearImages = useMutation({
    mutationFn: () =>
      apiJson<ClearImagesResponse>("/admin/api/images/clear", {
        method: "POST",
        body: JSON.stringify({ confirm: true, delete_tags: true }),
      }),
    onMutate: () => alerts.clear(),
    onSuccess: (data) => {
      alerts.setSuccess(`图片数据已清空：图片 ${data.deleted_images}，关联 ${data.deleted_image_tags}，标签 ${data.deleted_tags}`, data.request_id,);
      setSelectedRowKeys([]);
      qc.invalidateQueries({ queryKey: ["admin", "images"] });
      qc.invalidateQueries({ queryKey: ["admin", "summary"] });
      qc.invalidateQueries({ queryKey: ["public", "tags"] });
    },
    onError: (err) => {
      alerts.setError(err);
    },
  });

  const manualHydrate = useMutation({
    mutationFn: (imageId: string) =>
      apiJson<ManualHydrateResponse>("/admin/api/hydration-runs/manual", {
        method: "POST",
        body: JSON.stringify({ image_id: Number(imageId) }),
      }),
    onMutate: () => alerts.clear(),
    onSuccess: (data) => {
      alerts.setSuccess(data.created ? `单图补全任务已创建：任务 #${data.job_id}（作品 ${data.illust_id}）` : `任务已存在：任务 #${data.job_id}（作品 ${data.illust_id}）`, data.request_id,);
      qc.invalidateQueries({ queryKey: ["admin", "jobs"] });
      qc.invalidateQueries({ queryKey: ["admin", "hydration-runs"] });
    },
    onError: (err) => {
      alerts.setError(err, "创建补全任务失败");
    },
  });

  const columns: ColumnsType<ImageItem> = useMemo(
    () => [
      {
        title: "图片ID",
        dataIndex: "id",
        key: "id",
        width: 100,
        render: (value: string, row) => (
          <Button
            type="link"
            size="small"
            onClick={() =>
              window.open(
                // Public /i needs X-API-Key when required; browser open uses ?api_key=.
                withPublicApiKeyQuery(
                  resolvePublicApiUrl(row.proxy_path || `/i/${row.id}.${row.ext}`),
                ),
                "_blank",
                "noopener,noreferrer",
              )
            }
          >
            #{value}
          </Button>
        ),
      },
      { title: "作品ID", dataIndex: "illust_id", key: "illust_id", width: 110 },
      { title: "页码", dataIndex: "page_index", key: "page_index", width: 70 },
      { title: "格式", dataIndex: "ext", key: "ext", width: 70 },
      {
        title: "尺寸",
        key: "geometry",
        width: 120,
        render: (_, row) => (row.width && row.height ? `${row.width}×${row.height}` : "-"),
      },
      { title: "R18", dataIndex: "x_restrict", key: "x_restrict", width: 70, render: (v) => (v == null ? "-" : Number(v) > 0 ? "Y" : "N") },
      { title: "AI", dataIndex: "ai_type", key: "ai_type", width: 60, render: (v) => (v == null ? "-" : Number(v) > 0 ? "Y" : "N") },
      {
        title: "类型",
        dataIndex: "illust_type",
        key: "illust_type",
        width: 90,
        render: (v) => (v == null ? "-" : Number(v) === 0 ? "插画" : Number(v) === 1 ? "漫画" : Number(v) === 2 ? "动图" : String(v)),
      },
      { title: "收藏", dataIndex: "bookmark_count", key: "bookmark_count", width: 90, render: (v) => (v == null ? "-" : String(v)) },
      { title: "浏览", dataIndex: "view_count", key: "view_count", width: 90, render: (v) => (v == null ? "-" : String(v)) },
      { title: "评论", dataIndex: "comment_count", key: "comment_count", width: 90, render: (v) => (v == null ? "-" : String(v)) },
      { title: "标签数", dataIndex: "tag_count", key: "tag_count", width: 80 },
      {
        title: "缺失",
        dataIndex: "missing",
        key: "missing",
        width: 220,
        render: (values: string[]) =>
          (values || []).length ? (
            <Space wrap>
              {values.map((v) => (
                <Tag key={v}>{missingLabel(v, "short")}</Tag>
              ))}
            </Space>
          ) : (
            "-"
          ),
      },
      { title: "作者", key: "user", width: 140, render: (_, row) => (row.user?.name ? row.user.name : "-") },
      { title: "标题", dataIndex: "title", key: "title", width: 220, render: (v) => dash(v) },
      { title: "Pixiv 发布时间", dataIndex: "created_at_pixiv", key: "created_at_pixiv", width: 180, render: (v) => dash(v) },
      {
        title: "操作",
        key: "actions",
        width: 160,
        render: (_, row) => (
          <Space wrap>
            <Button
              size="small"
              onClick={() => manualHydrate.mutate(row.id)}
              loading={manualHydrate.isPending && manualHydrate.variables === row.id}
            >
              手动补全
            </Button>
            <Popconfirm
              title={`确定删除图片 #${row.id}？`}
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              onConfirm={() => deleteImage.mutate(row.id)}
            >
              <Button size="small" danger loading={deleteImage.isPending && deleteImage.variables === row.id}>
                删除
              </Button>
            </Popconfirm>
          </Space>
        ),
      },
    ],
    [deleteImage, manualHydrate],
  );

  const selectedImageIds = useMemo(() => selectedRowKeys.map((k) => String(k)), [selectedRowKeys]);

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        图片管理
      </Typography.Title>

      <Space wrap>
        <Typography.Text>缺失筛选:</Typography.Text>
        <Select
          mode="multiple"
          allowClear
          value={missing}
          onChange={(values) => {
            setMissing(values);
            setSelectedRowKeys([]);
          }}
          placeholder="不过滤（显示全部）"
          options={[
            { value: "tags", label: "缺标签" },
            { value: "geometry", label: "缺尺寸" },
            { value: "r18", label: "缺 R18 信息" },
            { value: "ai", label: "缺 AI 信息" },
            { value: "illust_type", label: "缺作品类型" },
            { value: "user", label: "缺作者信息" },
            { value: "title", label: "缺标题" },
            { value: "created_at", label: "缺发布时间" },
            { value: "popularity", label: "缺热度信息" },
          ]}
          style={{ minWidth: 420 }}
        />
        <Button onClick={() => query.refetch()} loading={query.isFetching}>
          刷新列表
        </Button>
        <Popconfirm
          title={`确定删除所选图片（${selectedImageIds.length} 张）？`}
          okText="删除"
          cancelText="取消"
          okButtonProps={{ danger: true }}
          onConfirm={() => bulkDelete.mutate(selectedImageIds)}
          disabled={selectedImageIds.length === 0}
        >
          <Button danger disabled={selectedImageIds.length === 0} loading={bulkDelete.isPending}>
            删除所选（{selectedImageIds.length}）
          </Button>
        </Popconfirm>
        <Popconfirm
          title="确定清空所有图片数据（包含标签与关联）？此操作不可恢复。"
          okText="清空"
          cancelText="取消"
          okButtonProps={{ danger: true }}
          onConfirm={() => clearImages.mutate()}
        >
          <Button danger loading={clearImages.isPending}>
            清空图片库
          </Button>
        </Popconfirm>
      </Space>

      <ActionAlerts
        message={alerts.message}
        requestId={alerts.requestId}
        errorMessage={alerts.errorMessage}
        errorRequestId={alerts.errorRequestId}
        requestIdPlacement="description"
      />

      <QueryState
        query={query}
        errorMessage="加载图片列表失败"
        empty={Boolean(query.data && items.length === 0)}
        emptyMessage="暂无图片"
        emptyDescription="请先导入图片链接，或取消筛选条件。"
      >
        {query.data ? (
          <Card>
            {listRequestId ? <Typography.Text type="secondary">请求ID: {listRequestId}</Typography.Text> : null}
            <Table<ImageItem>
              rowKey={(row) => row.id}
              columns={columns}
              dataSource={items}
              rowSelection={{
                selectedRowKeys,
                onChange: (keys) => setSelectedRowKeys(keys),
              }}
              pagination={false}
              size="small"
              scroll={{ x: 2000 }}
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
        ) : null}
      </QueryState>
    </Space>
  );
}
