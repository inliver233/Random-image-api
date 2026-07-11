import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Form, Input, Modal, Skeleton, Space, Switch, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import React from "react";

import { ApiError, apiJson } from "../api/client";
import { useCursorList } from "../hooks/useCursorList";

type ApiKeyItem = {
  id: string;
  name: string;
  description: string | null;
  enabled: boolean;
  hint: string;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
};

type ApiKeysListResponse = {
  ok: true;
  items: ApiKeyItem[];
  next_cursor: string;
  request_id: string;
};

type CreateApiKeyFormValues = {
  name: string;
  api_key: string;
  description?: string;
  enabled: boolean;
};

type CreateApiKeyResponse = {
  ok: true;
  api_key_id: string;
  hint: string;
  request_id: string;
};

type UpdateApiKeyResponse = {
  ok: true;
  api_key_id: string;
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

export function ApiKeysPage() {
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = React.useState(false);
  const [createForm] = Form.useForm<CreateApiKeyFormValues>();
  const [actionMessage, setActionMessage] = React.useState<string | null>(null);
  const [actionRequestId, setActionRequestId] = React.useState<string | null>(null);
  const [actionErrorMessage, setActionErrorMessage] = React.useState<string | null>(null);
  const [actionErrorRequestId, setActionErrorRequestId] = React.useState<string | null>(null);

  const {
    query: listQuery,
    items,
    nextCursor,
    listRequestId: requestId,
    loadMore,
    setItems,
  } = useCursorList<ApiKeyItem, ApiKeysListResponse>({
    queryKey: ["admin", "api-keys", { limit: 50 }],
    getItemId: (item) => item.id,
    fetchPage: (cursor) => {
      const sp = new URLSearchParams({ limit: "50" });
      if (cursor) sp.set("cursor", cursor);
      return apiJson<ApiKeysListResponse>(`/admin/api/api-keys?${sp.toString()}`);
    },
  });

  React.useEffect(() => {
    if (loadMore.isError) {
      const err = loadMore.error;
      setActionErrorMessage(messageFromError(err));
      setActionErrorRequestId(requestIdFromError(err));
    }
  }, [loadMore.isError, loadMore.error]);

  const createKey = useMutation({
    mutationFn: (values: CreateApiKeyFormValues) =>
      apiJson<CreateApiKeyResponse>("/admin/api/api-keys", {
        method: "POST",
        body: JSON.stringify({
          name: values.name,
          api_key: values.api_key,
          description: values.description || null,
          enabled: Boolean(values.enabled),
        }),
      }),
    onMutate: () => {
      setActionMessage(null);
      setActionRequestId(null);
      setActionErrorMessage(null);
      setActionErrorRequestId(null);
    },
    onSuccess: (data) => {
      setCreateOpen(false);
      createForm.resetFields();
      setActionMessage(`API Key 已创建：#${data.api_key_id}（hint=${data.hint}）`);
      setActionRequestId(data.request_id);
      queryClient.invalidateQueries({ queryKey: ["admin", "api-keys"] });
    },
    onError: (err) => {
      setActionErrorMessage(messageFromError(err));
      setActionErrorRequestId(requestIdFromError(err));
    },
  });

  const updateKey = useMutation({
    mutationFn: (payload: { id: string; enabled: boolean }) =>
      apiJson<UpdateApiKeyResponse>(`/admin/api/api-keys/${encodeURIComponent(payload.id)}`, {
        method: "PUT",
        body: JSON.stringify({ enabled: payload.enabled }),
      }),
    onMutate: () => {
      setActionMessage(null);
      setActionRequestId(null);
      setActionErrorMessage(null);
      setActionErrorRequestId(null);
    },
    onSuccess: (data, vars) => {
      setActionMessage(`API Key #${data.api_key_id} 已${vars.enabled ? "启用" : "禁用"}`);
      setActionRequestId(data.request_id);
      setItems((prev) => prev.map((it) => (it.id === vars.id ? { ...it, enabled: vars.enabled } : it)));
      queryClient.invalidateQueries({ queryKey: ["admin", "api-keys"] });
    },
    onError: (err) => {
      setActionErrorMessage(messageFromError(err));
      setActionErrorRequestId(requestIdFromError(err));
    },
  });

  const columns: ColumnsType<ApiKeyItem> = [
    { title: "ID", dataIndex: "id", key: "id", width: 90 },
    { title: "名称", dataIndex: "name", key: "name", width: 160 },
    { title: "描述", dataIndex: "description", key: "description", render: (v) => v || "-" },
    { title: "Hint", dataIndex: "hint", key: "hint", width: 140 },
    {
      title: "启用",
      dataIndex: "enabled",
      key: "enabled",
      width: 90,
      render: (value) => (value ? "是" : "否"),
    },
    { title: "最近使用", dataIndex: "last_used_at", key: "last_used_at", width: 180, render: (v) => v || "-" },
    { title: "创建时间", dataIndex: "created_at", key: "created_at", width: 180 },
    {
      title: "操作",
      key: "actions",
      width: 140,
      render: (_, row) => (
        <Button
          size="small"
          loading={updateKey.isPending && updateKey.variables?.id === row.id}
          onClick={() => updateKey.mutate({ id: row.id, enabled: !row.enabled })}
        >
          {row.enabled ? "禁用" : "启用"}
        </Button>
      ),
    },
  ];

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        API Keys
      </Typography.Title>

      <Space wrap>
        <Button type="primary" onClick={() => setCreateOpen(true)}>
          创建 API Key
        </Button>
        <Button onClick={() => listQuery.refetch()} loading={listQuery.isFetching}>
          刷新列表
        </Button>
      </Space>

      {actionMessage ? (
        <Alert type="success" showIcon message={actionMessage} description={actionRequestId ? `请求ID: ${actionRequestId}` : ""} />
      ) : null}
      {actionErrorMessage ? (
        <Alert type="error" showIcon message={actionErrorMessage} description={actionErrorRequestId ? `请求ID: ${actionErrorRequestId}` : ""} />
      ) : null}

      {listQuery.isLoading ? (
        <Skeleton active />
      ) : listQuery.isError ? (
        <Alert
          type="error"
          showIcon
          message="加载 API Keys 失败"
          description={requestIdFromError(listQuery.error) ? `请求ID: ${requestIdFromError(listQuery.error)}` : ""}
        />
      ) : items.length === 0 ? (
        <Alert type="info" showIcon message="暂无 API Key" description="创建后可用于公网接口鉴权（X-API-Key）。" />
      ) : (
        <Card>
          {requestId ? <Typography.Text type="secondary">请求ID: {requestId}</Typography.Text> : null}
          <Table<ApiKeyItem>
            rowKey={(row) => row.id}
            columns={columns}
            dataSource={items}
            pagination={false}
            size="small"
            scroll={{ x: 1100 }}
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

      <Modal
        title="创建 API Key"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => createForm.submit()}
        okText="创建"
        cancelText="取消"
        confirmLoading={createKey.isPending}
        destroyOnClose
      >
        <Form
          form={createForm}
          layout="vertical"
          initialValues={{ enabled: true }}
          onFinish={(values) => createKey.mutate(values)}
        >
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }, { max: 100 }]}>
            <Input placeholder="例如：public-bot" />
          </Form.Item>
          <Form.Item
            name="api_key"
            label="密钥原文"
            rules={[
              { required: true, message: "请输入密钥" },
              { min: 20, message: "至少 20 字符" },
              { max: 500, message: "最多 500 字符" },
            ]}
            extra="服务端只保存哈希，创建后无法再次查看原文。"
          >
            <Input.Password placeholder="至少 20 字符的随机密钥" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} maxLength={1000} placeholder="可选" />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
