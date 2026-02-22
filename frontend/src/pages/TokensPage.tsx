import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Form, Input, InputNumber, Modal, Popconfirm, Skeleton, Space, Switch, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import React from "react";

import { ApiError, apiJson } from "../api/client";

type TokenItem = {
  id: string;
  label: string | null;
  enabled: boolean;
  refresh_token_masked: string | null;
  weight: number | null;
  error_count: number | null;
  backoff_until: string | null;
  last_ok_at: string | null;
  last_fail_at: string | null;
};

type TokensListResponse = {
  ok: true;
  items: TokenItem[];
  request_id: string;
};

type CreateTokenFormValues = {
  label: string;
  refresh_token: string;
  enabled: boolean;
  weight: number;
};

type CreateTokenResponse = {
  ok: true;
  token_id: string;
  request_id: string;
};

type UpdateTokenResponse = {
  ok: true;
  token_id: string;
  request_id: string;
};

type DeleteTokenResponse = {
  ok: true;
  token_id: string;
  request_id: string;
};

type TestRefreshResponse = {
  ok: true;
  expires_in: number;
  user_id: string | null;
  proxy: { endpoint_id: string; pool_id: string } | null;
  request_id: string;
};

type ResetFailuresResponse = {
  ok: true;
  token_id: string;
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

const columns = (actions: {
  onEdit: (row: TokenItem) => void;
  onTestRefresh: (id: string) => void;
  onResetFailures: (id: string) => void;
  onToggleEnabled: (row: TokenItem) => void;
  onDelete: (id: string) => void;
  testPendingId: string | null;
  resetPendingId: string | null;
  updatePendingId: string | null;
  deletePendingId: string | null;
}): ColumnsType<TokenItem> => [
  { title: "标签", dataIndex: "label", key: "label" },
  { title: "启用", dataIndex: "enabled", key: "enabled", render: (value) => (value ? "是" : "否") },
  { title: "掩码令牌", dataIndex: "refresh_token_masked", key: "refresh_token_masked" },
  { title: "权重", dataIndex: "weight", key: "weight" },
  { title: "错误次数", dataIndex: "error_count", key: "error_count" },
  { title: "退避截止", dataIndex: "backoff_until", key: "backoff_until" },
  { title: "最近成功", dataIndex: "last_ok_at", key: "last_ok_at" },
  { title: "最近失败", dataIndex: "last_fail_at", key: "last_fail_at" },
  {
    title: "操作",
    key: "actions",
    render: (_, row) => (
      <Space wrap>
        <Button size="small" onClick={() => actions.onEdit(row)}>
          编辑
        </Button>
        <Button size="small" onClick={() => actions.onToggleEnabled(row)} loading={actions.updatePendingId === row.id}>
          {row.enabled ? "禁用" : "启用"}
        </Button>
        <Button size="small" onClick={() => actions.onTestRefresh(row.id)} loading={actions.testPendingId === row.id}>
          测试刷新
        </Button>
        <Button size="small" onClick={() => actions.onResetFailures(row.id)} loading={actions.resetPendingId === row.id}>
          重置失败计数
        </Button>
        <Popconfirm
          title={`确定删除令牌 #${row.id}？`}
          okText="删除"
          cancelText="取消"
          okButtonProps={{ danger: true }}
          onConfirm={() => actions.onDelete(row.id)}
        >
          <Button size="small" danger loading={actions.deletePendingId === row.id}>
            删除
          </Button>
        </Popconfirm>
      </Space>
    ),
  },
];

type EditTokenFormValues = {
  label: string;
  enabled: boolean;
  weight: number;
};

export function TokensPage() {
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = React.useState(false);
  const [createForm] = Form.useForm<CreateTokenFormValues>();
  const [editOpen, setEditOpen] = React.useState(false);
  const [editForm] = Form.useForm<EditTokenFormValues>();
  const [editingToken, setEditingToken] = React.useState<TokenItem | null>(null);

  const [actionMessage, setActionMessage] = React.useState<string | null>(null);
  const [actionRequestId, setActionRequestId] = React.useState<string | null>(null);
  const [actionErrorMessage, setActionErrorMessage] = React.useState<string | null>(null);
  const [actionErrorRequestId, setActionErrorRequestId] = React.useState<string | null>(null);

  const query = useQuery({
    queryKey: ["admin", "tokens"],
    queryFn: () => apiJson<TokensListResponse>("/admin/api/tokens"),
  });

  const createToken = useMutation({
    mutationFn: (values: CreateTokenFormValues) =>
      apiJson<CreateTokenResponse>("/admin/api/tokens", {
        method: "POST",
        body: JSON.stringify({
          label: values.label || null,
          refresh_token: values.refresh_token,
          enabled: Boolean(values.enabled),
          weight: values.weight,
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
      setActionMessage(`令牌创建成功：${data.token_id}`);
      setActionRequestId(data.request_id);
      createForm.resetFields();
      queryClient.invalidateQueries({ queryKey: ["admin", "tokens"] });
    },
    onError: (err) => {
      setActionErrorMessage(messageFromError(err));
      setActionErrorRequestId(requestIdFromError(err));
    },
  });

  const updateToken = useMutation({
    mutationFn: (payload: { tokenId: string; body: Record<string, unknown> }) =>
      apiJson<UpdateTokenResponse>(`/admin/api/tokens/${encodeURIComponent(payload.tokenId)}`, {
        method: "PUT",
        body: JSON.stringify(payload.body),
      }),
    onMutate: () => {
      setActionMessage(null);
      setActionRequestId(null);
      setActionErrorMessage(null);
      setActionErrorRequestId(null);
    },
    onSuccess: (data) => {
      setEditOpen(false);
      setEditingToken(null);
      editForm.resetFields();

      setActionMessage(`令牌已更新：${data.token_id}`);
      setActionRequestId(data.request_id);
      queryClient.invalidateQueries({ queryKey: ["admin", "tokens"] });
    },
    onError: (err) => {
      setActionErrorMessage(messageFromError(err));
      setActionErrorRequestId(requestIdFromError(err));
      queryClient.invalidateQueries({ queryKey: ["admin", "tokens"] });
    },
  });

  const testRefresh = useMutation({
    mutationFn: (tokenId: string) =>
      apiJson<TestRefreshResponse>(`/admin/api/tokens/${encodeURIComponent(tokenId)}/test-refresh`, { method: "POST" }),
    onMutate: () => {
      setActionMessage(null);
      setActionRequestId(null);
      setActionErrorMessage(null);
      setActionErrorRequestId(null);
    },
    onSuccess: (data) => {
      const routeInfo = data.proxy ? `（经代理 #${data.proxy.endpoint_id}，代理池 #${data.proxy.pool_id}）` : "（直连）";
      setActionMessage(`令牌刷新成功，expires_in=${data.expires_in}${routeInfo}`);
      setActionRequestId(data.request_id);
      queryClient.invalidateQueries({ queryKey: ["admin", "tokens"] });
    },
    onError: (err) => {
      setActionErrorMessage(messageFromError(err));
      setActionErrorRequestId(requestIdFromError(err));
      queryClient.invalidateQueries({ queryKey: ["admin", "tokens"] });
    },
  });

  const resetFailures = useMutation({
    mutationFn: (tokenId: string) =>
      apiJson<ResetFailuresResponse>(`/admin/api/tokens/${encodeURIComponent(tokenId)}/reset-failures`, { method: "POST" }),
    onMutate: () => {
      setActionMessage(null);
      setActionRequestId(null);
      setActionErrorMessage(null);
      setActionErrorRequestId(null);
    },
    onSuccess: (data) => {
      setActionMessage(`已重置失败计数：${data.token_id}`);
      setActionRequestId(data.request_id);
      queryClient.invalidateQueries({ queryKey: ["admin", "tokens"] });
    },
    onError: (err) => {
      setActionErrorMessage(messageFromError(err));
      setActionErrorRequestId(requestIdFromError(err));
    },
  });

  const deleteToken = useMutation({
    mutationFn: (tokenId: string) =>
      apiJson<DeleteTokenResponse>(`/admin/api/tokens/${encodeURIComponent(tokenId)}`, { method: "DELETE" }),
    onMutate: () => {
      setActionMessage(null);
      setActionRequestId(null);
      setActionErrorMessage(null);
      setActionErrorRequestId(null);
    },
    onSuccess: (data) => {
      setActionMessage(`令牌已删除：${data.token_id}`);
      setActionRequestId(data.request_id);
      queryClient.invalidateQueries({ queryKey: ["admin", "tokens"] });
      queryClient.invalidateQueries({ queryKey: ["admin", "bindings"] });
    },
    onError: (err) => {
      setActionErrorMessage(messageFromError(err));
      setActionErrorRequestId(requestIdFromError(err));
      queryClient.invalidateQueries({ queryKey: ["admin", "tokens"] });
    },
  });

  const openEdit = (row: TokenItem) => {
    setEditingToken(row);
    setEditOpen(true);
    editForm.setFieldsValue({
      label: row.label || "",
      enabled: Boolean(row.enabled),
      weight: row.weight != null ? row.weight : 1.0,
    });
  };

  const closeEdit = () => {
    setEditOpen(false);
    setEditingToken(null);
    editForm.resetFields();
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        Pixiv 令牌管理
      </Typography.Title>

      {actionMessage ? <Alert type="success" showIcon message={actionMessage} /> : null}
      {actionRequestId ? <Typography.Text type="secondary">请求ID: {actionRequestId}</Typography.Text> : null}
      {actionErrorMessage ? <Alert type="error" showIcon message={actionErrorMessage} /> : null}
      {actionErrorRequestId ? <Typography.Text type="secondary">请求ID: {actionErrorRequestId}</Typography.Text> : null}

      <Space wrap>
        <Button type="primary" onClick={() => setCreateOpen(true)}>
          新增令牌
        </Button>
      </Space>

      <Modal
        title="新增令牌"
        open={createOpen}
        onCancel={() => {
          setCreateOpen(false);
          createForm.resetFields();
        }}
        footer={null}
        destroyOnClose
      >
        <Form<CreateTokenFormValues>
          form={createForm}
          layout="vertical"
          initialValues={{ label: "", refresh_token: "", enabled: true, weight: 1.0 }}
          onFinish={(values) => createToken.mutate(values)}
        >
          <Form.Item label="标签（可选）" name="label">
            <Input placeholder="例如：主账号" />
          </Form.Item>

          <Form.Item
            label="刷新令牌"
            name="refresh_token"
            rules={[{ required: true, message: "请输入刷新令牌" }]}
          >
            <Input.Password placeholder="必填" />
          </Form.Item>

          <Form.Item label="启用" name="enabled" valuePropName="checked">
            <Switch />
          </Form.Item>

          <Form.Item label="权重" name="weight" rules={[{ required: true, message: "请输入权重" }]}>
            <InputNumber min={0} max={100} step={0.1} style={{ width: 180 }} />
          </Form.Item>

          <Alert
            type="info"
            showIcon
            message="安全说明"
            description="刷新令牌只写入不回显，保存后不会再次显示明文。"
          />

          <Space style={{ width: "100%", justifyContent: "flex-end" }}>
            <Button
              onClick={() => {
                setCreateOpen(false);
                createForm.resetFields();
              }}
              disabled={createToken.isPending}
            >
              取消
            </Button>
            <Button type="primary" htmlType="submit" loading={createToken.isPending}>
              创建
            </Button>
          </Space>
        </Form>
      </Modal>

      <Modal
        title={editingToken ? `编辑令牌 #${editingToken.id}` : "编辑令牌"}
        open={editOpen}
        onCancel={closeEdit}
        footer={null}
        destroyOnClose
      >
        <Form<EditTokenFormValues>
          form={editForm}
          layout="vertical"
          initialValues={{ label: "", enabled: true, weight: 1.0 }}
          onFinish={(values) => {
            const tokenId = editingToken?.id;
            if (!tokenId) return;
            updateToken.mutate({
              tokenId,
              body: {
                label: values.label.trim() ? values.label.trim() : null,
                enabled: Boolean(values.enabled),
                weight: values.weight,
              },
            });
          }}
        >
          <Form.Item label="标签（可选）" name="label">
            <Input placeholder="例如：主账号" />
          </Form.Item>

          <Form.Item label="启用" name="enabled" valuePropName="checked">
            <Switch />
          </Form.Item>

          <Form.Item label="权重" name="weight" rules={[{ required: true, message: "请输入权重" }]}>
            <InputNumber min={0} max={100} step={0.1} style={{ width: 180 }} />
          </Form.Item>

          <Alert type="info" showIcon message="提示" description="刷新令牌不支持在此修改；如需更换请新增令牌并禁用旧令牌。" />

          <Space style={{ width: "100%", justifyContent: "flex-end" }}>
            <Button onClick={closeEdit} disabled={updateToken.isPending}>
              取消
            </Button>
            <Button type="primary" htmlType="submit" loading={updateToken.isPending}>
              保存
            </Button>
          </Space>
        </Form>
      </Modal>

      {query.isLoading ? (
        <Skeleton active />
      ) : query.isError ? (
        <Alert
          type="error"
          showIcon
          message="加载令牌列表失败"
          description={requestIdFromError(query.error) ? `请求ID: ${requestIdFromError(query.error)}` : ""}
        />
      ) : !query.data ? (
        <Skeleton active />
      ) : query.data.items.length === 0 ? (
        <Alert type="info" showIcon message="暂无令牌" description="请至少添加一个令牌，才能执行 Pixiv 接口相关任务。" />
      ) : (
        <Card>
          <Typography.Text type="secondary">请求ID: {query.data.request_id}</Typography.Text>
          <Table<TokenItem>
            rowKey={(row) => row.id}
            columns={columns({
              onEdit: (row) => openEdit(row),
              onTestRefresh: (id) => testRefresh.mutate(id),
              onResetFailures: (id) => resetFailures.mutate(id),
              onToggleEnabled: (row) =>
                updateToken.mutate({
                  tokenId: row.id,
                  body: { enabled: !row.enabled },
                }),
              onDelete: (id) => deleteToken.mutate(id),
              testPendingId: testRefresh.isPending ? testRefresh.variables ?? null : null,
              resetPendingId: resetFailures.isPending ? resetFailures.variables ?? null : null,
              updatePendingId: updateToken.isPending ? updateToken.variables?.tokenId ?? null : null,
              deletePendingId: deleteToken.isPending ? deleteToken.variables ?? null : null,
            })}
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
