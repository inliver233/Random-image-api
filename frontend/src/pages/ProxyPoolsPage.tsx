import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Form, Input, InputNumber, Modal, Skeleton, Space, Switch, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, apiJson } from "../api/client";

type ProxyPoolItem = {
  id: string;
  name: string;
  description: string | null;
  enabled: boolean;
};

type ProxyPoolsListResponse = {
  ok: true;
  items: ProxyPoolItem[];
  request_id: string;
};

type CreateProxyPoolResponse = {
  ok: true;
  pool_id: string;
  request_id: string;
};

type UpdateProxyPoolResponse = {
  ok: true;
  pool_id: string;
  request_id: string;
};

type ProxyEndpointListItem = {
  id: string;
  uri_masked: string;
  enabled: boolean;
  pools: Array<{
    id: string;
    name: string;
    pool_enabled: boolean;
    member_enabled: boolean;
    weight: number;
  }>;
};

type ProxyEndpointsResponse = {
  ok: true;
  items: ProxyEndpointListItem[];
  request_id: string;
};

type SetPoolEndpointsResponse = {
  ok: true;
  pool_id: string;
  created: number;
  updated: number;
  removed: number;
  request_id: string;
};

type CreatePoolFormValues = {
  name: string;
  description: string;
  enabled: boolean;
};

type EditPoolFormValues = {
  name: string;
  description: string;
  enabled: boolean;
};

type MemberConfig = { enabled: boolean; weight: number };

function requestIdFromError(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  return err.body?.request_id ? String(err.body.request_id) : null;
}

function messageFromError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "未知错误";
}

export function ProxyPoolsPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const pools = useQuery({
    queryKey: ["admin", "proxy-pools"],
    queryFn: () => apiJson<ProxyPoolsListResponse>("/admin/api/proxy-pools"),
  });

  const endpoints = useQuery({
    queryKey: ["admin", "proxies", "endpoints"],
    queryFn: () => apiJson<ProxyEndpointsResponse>("/admin/api/proxies/endpoints"),
  });

  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm<CreatePoolFormValues>();

  const [editOpen, setEditOpen] = useState(false);
  const [editingPool, setEditingPool] = useState<ProxyPoolItem | null>(null);
  const [editForm] = Form.useForm<EditPoolFormValues>();

  const [configOpen, setConfigOpen] = useState(false);
  const [configPool, setConfigPool] = useState<ProxyPoolItem | null>(null);
  const [selectedEndpointIds, setSelectedEndpointIds] = useState<string[]>([]);
  const [memberConfig, setMemberConfig] = useState<Record<string, MemberConfig>>({});

  const [actionAlert, setActionAlert] = useState<{ type: "success" | "error"; message: string; requestId: string | null } | null>(null);

  const createPool = useMutation({
    mutationFn: (values: CreatePoolFormValues) =>
      apiJson<CreateProxyPoolResponse>("/admin/api/proxy-pools", {
        method: "POST",
        body: JSON.stringify({
          name: values.name,
          description: values.description.trim() ? values.description.trim() : null,
          enabled: Boolean(values.enabled),
        }),
      }),
    onMutate: () => setActionAlert(null),
    onSuccess: (data) => {
      setCreateOpen(false);
      createForm.resetFields();
      setActionAlert({ type: "success", message: `代理池创建成功：#${data.pool_id}`, requestId: data.request_id });
      qc.invalidateQueries({ queryKey: ["admin", "proxy-pools"] });
    },
    onError: (err) => {
      setActionAlert({ type: "error", message: messageFromError(err), requestId: requestIdFromError(err) });
    },
  });

  const updatePool = useMutation({
    mutationFn: (vars: { poolId: string; values: EditPoolFormValues }) =>
      apiJson<UpdateProxyPoolResponse>(`/admin/api/proxy-pools/${encodeURIComponent(vars.poolId)}`, {
        method: "PUT",
        body: JSON.stringify({
          name: vars.values.name,
          description: vars.values.description.trim() ? vars.values.description.trim() : null,
          enabled: Boolean(vars.values.enabled),
        }),
      }),
    onMutate: () => setActionAlert(null),
    onSuccess: (data) => {
      setEditOpen(false);
      setEditingPool(null);
      setActionAlert({ type: "success", message: `代理池已更新：#${data.pool_id}`, requestId: data.request_id });
      qc.invalidateQueries({ queryKey: ["admin", "proxy-pools"] });
      qc.invalidateQueries({ queryKey: ["admin", "proxies", "endpoints"] });
    },
    onError: (err) => {
      setActionAlert({ type: "error", message: messageFromError(err), requestId: requestIdFromError(err) });
    },
  });

  const setPoolEndpoints = useMutation({
    mutationFn: (vars: { poolId: string; endpointIds: string[]; config: Record<string, MemberConfig> }) => {
      const items = vars.endpointIds
        .map((id) => {
          const cfg = vars.config[id] || { enabled: true, weight: 1 };
          return { endpoint_id: Number(id), enabled: Boolean(cfg.enabled), weight: Number(cfg.weight) || 1 };
        })
        .filter((v) => Number.isFinite(v.endpoint_id) && v.endpoint_id > 0);

      return apiJson<SetPoolEndpointsResponse>(`/admin/api/proxy-pools/${encodeURIComponent(vars.poolId)}/endpoints`, {
        method: "POST",
        body: JSON.stringify({ items }),
      });
    },
    onMutate: () => setActionAlert(null),
    onSuccess: (data) => {
      setActionAlert({
        type: "success",
        message: `节点配置已保存：新增=${data.created} 更新=${data.updated} 移除=${data.removed}`,
        requestId: data.request_id,
      });
      qc.invalidateQueries({ queryKey: ["admin", "proxies", "endpoints"] });
      setConfigOpen(false);
      setConfigPool(null);
    },
    onError: (err) => {
      setActionAlert({ type: "error", message: messageFromError(err), requestId: requestIdFromError(err) });
    },
  });

  const endpointRows = endpoints.data?.items || [];

  const endpointSelection = useMemo(() => {
    return {
      selectedRowKeys: selectedEndpointIds,
      onChange: (keys: React.Key[]) => {
        const ids = keys.map((k) => String(k));
        setSelectedEndpointIds(ids);
        setMemberConfig((prev) => {
          const next = { ...prev };
          for (const id of ids) {
            if (!next[id]) next[id] = { enabled: true, weight: 1 };
          }
          return next;
        });
      },
    };
  }, [selectedEndpointIds]);

  useEffect(() => {
    if (!configOpen || !configPool || !endpoints.data) return;

    const poolId = configPool.id;
    const selected: string[] = [];
    const cfg: Record<string, MemberConfig> = {};
    for (const ep of endpoints.data.items) {
      const membership = (ep.pools || []).find((p) => String(p.id) === String(poolId));
      if (!membership) continue;
      selected.push(String(ep.id));
      cfg[String(ep.id)] = { enabled: Boolean(membership.member_enabled), weight: Number(membership.weight) || 1 };
    }

    setSelectedEndpointIds(selected);
    setMemberConfig(cfg);
  }, [configOpen, configPool, endpoints.data]);

  const openEdit = (pool: ProxyPoolItem) => {
    setEditingPool(pool);
    editForm.setFieldsValue({
      name: pool.name,
      description: pool.description || "",
      enabled: Boolean(pool.enabled),
    });
    setEditOpen(true);
  };

  const openConfig = (pool: ProxyPoolItem) => {
    setConfigPool(pool);
    setConfigOpen(true);
  };

  const poolColumns: ColumnsType<ProxyPoolItem> = [
    { title: "ID", dataIndex: "id", key: "id", width: 90, render: (value) => `#${value}` },
    { title: "名称", dataIndex: "name", key: "name", width: 220 },
    { title: "启用", dataIndex: "enabled", key: "enabled", width: 90, render: (value) => (value ? "是" : "否") },
    { title: "描述", dataIndex: "description", key: "description", render: (value) => value || "-" },
    {
      title: "操作",
      key: "actions",
      width: 280,
      render: (_, row) => (
        <Space wrap>
          <Button size="small" onClick={() => openEdit(row)}>
            编辑
          </Button>
          <Button size="small" data-testid={`pool-config-${row.id}`} onClick={() => openConfig(row)}>
            配置节点
          </Button>
          <Button size="small" onClick={() => navigate(`/admin/bindings?pool_id=${encodeURIComponent(row.id)}`)}>
            查看绑定
          </Button>
        </Space>
      ),
    },
  ];

  const endpointColumns: ColumnsType<ProxyEndpointListItem> = [
    { title: "节点ID", dataIndex: "id", key: "id", width: 90, render: (value) => `#${value}` },
    { title: "代理地址（掩码）", dataIndex: "uri_masked", key: "uri_masked", width: 320 },
    { title: "节点启用", dataIndex: "enabled", key: "enabled", width: 90, render: (v) => (v ? "是" : "否") },
    {
      title: "成员启用",
      key: "member_enabled",
      width: 110,
      render: (_, row) => {
        const id = String(row.id);
        const selected = selectedEndpointIds.includes(id);
        const checked = memberConfig[id]?.enabled ?? true;
        return (
          <Switch
            size="small"
            checked={checked}
            disabled={!selected}
            onChange={(value) => setMemberConfig((prev) => ({ ...prev, [id]: { ...(prev[id] || { enabled: true, weight: 1 }), enabled: value } }))}
          />
        );
      },
    },
    {
      title: "权重",
      key: "weight",
      width: 100,
      render: (_, row) => {
        const id = String(row.id);
        const selected = selectedEndpointIds.includes(id);
        const weight = memberConfig[id]?.weight ?? 1;
        return (
          <InputNumber
            min={0}
            max={1000}
            value={weight}
            disabled={!selected}
            onChange={(value) => setMemberConfig((prev) => ({ ...prev, [id]: { ...(prev[id] || { enabled: true, weight: 1 }), weight: Number(value || 1) } }))}
          />
        );
      },
    },
  ];

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        代理池管理
      </Typography.Title>

      <Space wrap>
        <Button type="primary" onClick={() => setCreateOpen(true)}>
          新建代理池
        </Button>
        <Button onClick={() => navigate("/admin/proxies")}>打开代理列表</Button>
        <Button onClick={() => navigate("/admin/settings")}>打开系统设置</Button>
        <Button onClick={() => pools.refetch()} loading={pools.isFetching}>
          刷新代理池
        </Button>
      </Space>

      {actionAlert ? (
        <Alert type={actionAlert.type} showIcon message={actionAlert.message} description={actionAlert.requestId ? `请求ID: ${actionAlert.requestId}` : ""} />
      ) : null}

      <Card title="代理池列表">
        {pools.isLoading ? (
          <Skeleton active />
        ) : pools.isError ? (
          <Alert type="error" showIcon message="加载代理池失败" description={requestIdFromError(pools.error) ? `请求ID: ${requestIdFromError(pools.error)}` : ""} />
        ) : !pools.data || pools.data.items.length === 0 ? (
          <Alert type="info" showIcon message="暂无代理池" description="请先创建一个代理池，然后为它配置代理节点。" />
        ) : (
          <>
            <Typography.Text type="secondary">请求ID: {pools.data.request_id}</Typography.Text>
            <Table<ProxyPoolItem>
              rowKey={(row) => row.id}
              columns={poolColumns}
              dataSource={pools.data.items}
              pagination={false}
              size="small"
              style={{ marginTop: 12 }}
            />
          </>
        )}
      </Card>

      <Modal
        title="新建代理池"
        open={createOpen}
        onCancel={() => {
          setCreateOpen(false);
          createForm.resetFields();
        }}
        footer={null}
        destroyOnClose
      >
        <Form<CreatePoolFormValues>
          form={createForm}
          layout="vertical"
          initialValues={{ name: "", description: "", enabled: true }}
          onFinish={(values) => createPool.mutate(values)}
        >
          <Form.Item label="名称" name="name" rules={[{ required: true, message: "请输入名称" }]}>
            <Input placeholder="例如：默认代理池" />
          </Form.Item>
          <Form.Item label="描述（可选）" name="description">
            <Input.TextArea rows={3} placeholder="用于备注用途/地区/线路等" />
          </Form.Item>
          <Form.Item label="启用" name="enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Space style={{ width: "100%", justifyContent: "flex-end" }}>
            <Button onClick={() => setCreateOpen(false)} disabled={createPool.isPending}>
              取消
            </Button>
            <Button type="primary" htmlType="submit" loading={createPool.isPending}>
              创建
            </Button>
          </Space>
        </Form>
      </Modal>

      <Modal
        title={editingPool ? `编辑代理池 #${editingPool.id}` : "编辑代理池"}
        open={editOpen}
        onCancel={() => {
          setEditOpen(false);
          setEditingPool(null);
        }}
        footer={null}
        destroyOnClose
      >
        <Form<EditPoolFormValues>
          form={editForm}
          layout="vertical"
          initialValues={{ name: "", description: "", enabled: true }}
          onFinish={(values) => {
            if (!editingPool) return;
            updatePool.mutate({ poolId: editingPool.id, values });
          }}
        >
          <Form.Item label="名称" name="name" rules={[{ required: true, message: "请输入名称" }]}>
            <Input />
          </Form.Item>
          <Form.Item label="描述（可选）" name="description">
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item label="启用" name="enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Space style={{ width: "100%", justifyContent: "flex-end" }}>
            <Button onClick={() => setEditOpen(false)} disabled={updatePool.isPending}>
              取消
            </Button>
            <Button type="primary" htmlType="submit" loading={updatePool.isPending}>
              保存
            </Button>
          </Space>
        </Form>
      </Modal>

      <Modal
        title={configPool ? `配置代理池节点 #${configPool.id}` : "配置代理池节点"}
        open={configOpen}
        onCancel={() => {
          setConfigOpen(false);
          setConfigPool(null);
        }}
        width={900}
        footer={
          <Space style={{ width: "100%", justifyContent: "space-between" }}>
            <Space>
              {configPool ? (
                <Typography.Text type="secondary">
                  当前代理池ID: #{configPool.id}（可在系统设置中填写默认代理池ID）
                </Typography.Text>
              ) : null}
            </Space>
            <Space>
              <Button onClick={() => setConfigOpen(false)} disabled={setPoolEndpoints.isPending}>
                关闭
              </Button>
              <Button
                type="primary"
                data-testid="pool-endpoints-save"
                onClick={() => {
                  if (!configPool) return;
                  setPoolEndpoints.mutate({ poolId: configPool.id, endpointIds: selectedEndpointIds, config: memberConfig });
                }}
                loading={setPoolEndpoints.isPending}
                disabled={!configPool}
              >
                保存节点配置
              </Button>
            </Space>
          </Space>
        }
        destroyOnClose
      >
        {endpoints.isLoading ? (
          <Skeleton active />
        ) : endpoints.isError ? (
          <Alert type="error" showIcon message="加载代理节点失败" description={requestIdFromError(endpoints.error) ? `请求ID: ${requestIdFromError(endpoints.error)}` : ""} />
        ) : (
          <>
            <Typography.Text type="secondary">请求ID: {endpoints.data?.request_id}</Typography.Text>
            <Alert
              type="info"
              showIcon
              message="提示"
              description="勾选要加入该代理池的节点；未勾选的节点会从该池移除。成员启用/权重仅对当前代理池生效。"
              style={{ marginTop: 12 }}
            />
            <Table<ProxyEndpointListItem>
              rowKey={(row) => row.id}
              columns={endpointColumns}
              dataSource={endpointRows}
              pagination={false}
              size="small"
              style={{ marginTop: 12 }}
              scroll={{ x: 880 }}
              rowSelection={endpointSelection}
            />
          </>
        )}
      </Modal>
    </Space>
  );
}

