import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Form, Input, InputNumber, Modal, Skeleton, Space, Switch, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ActionAlerts } from "../admin/ActionAlerts";
import { dash, yesNo } from "../admin/format";
import { QueryState } from "../admin/QueryState";
import { requestIdDescription } from "../admin/errors";
import { useActionAlerts } from "../admin/useActionAlerts";
import { apiJson } from "../api/client";
import { useCursorList } from "../hooks/useCursorList";

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
  next_cursor?: string | null;
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

export function ProxyPoolsPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const pools = useQuery({
    queryKey: ["admin", "proxy-pools"],
    queryFn: () => apiJson<ProxyPoolsListResponse>("/admin/api/proxy-pools"),
  });

  const ENDPOINT_PAGE_SIZE = 200;
  const endpointsList = useCursorList<ProxyEndpointListItem, ProxyEndpointsResponse>({
    queryKey: ["admin", "proxies", "endpoints", { limit: ENDPOINT_PAGE_SIZE }],
    fetchPage: (cursor) => {
      const qs = new URLSearchParams();
      qs.set("limit", String(ENDPOINT_PAGE_SIZE));
      if (cursor) qs.set("cursor", cursor);
      return apiJson<ProxyEndpointsResponse>(`/admin/api/proxies/endpoints?${qs.toString()}`);
    },
    getItemId: (item) => String(item.id),
  });
  const endpoints = endpointsList.query;

  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm<CreatePoolFormValues>();

  const [editOpen, setEditOpen] = useState(false);
  const [editingPool, setEditingPool] = useState<ProxyPoolItem | null>(null);
  const [editForm] = Form.useForm<EditPoolFormValues>();

  const [configOpen, setConfigOpen] = useState(false);
  const [configPool, setConfigPool] = useState<ProxyPoolItem | null>(null);
  const [selectedEndpointIds, setSelectedEndpointIds] = useState<string[]>([]);
  const [memberConfig, setMemberConfig] = useState<Record<string, MemberConfig>>({});

  const alerts = useActionAlerts();

  React.useEffect(() => {
    if (endpointsList.loadMore.isError) {
      alerts.setError(endpointsList.loadMore.error, "加载更多节点失败");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only re-run on loadMore error state
  }, [endpointsList.loadMore.isError, endpointsList.loadMore.error]);

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
    onMutate: () => alerts.clear(),
    onSuccess: (data) => {
      setCreateOpen(false);
      createForm.resetFields();
      alerts.setSuccess(`代理池创建成功：#${data.pool_id}`, data.request_id);
      qc.invalidateQueries({ queryKey: ["admin", "proxy-pools"] });
    },
    onError: (err) => {
      alerts.setError(err);
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
    onMutate: () => alerts.clear(),
    onSuccess: (data) => {
      setEditOpen(false);
      setEditingPool(null);
      alerts.setSuccess(`代理池已更新：#${data.pool_id}`, data.request_id);
      qc.invalidateQueries({ queryKey: ["admin", "proxy-pools"] });
      qc.invalidateQueries({ queryKey: ["admin", "proxies", "endpoints"] });
    },
    onError: (err) => {
      alerts.setError(err);
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
    onMutate: () => alerts.clear(),
    onSuccess: (data) => {
      alerts.setSuccess(`节点配置已保存：新增=${data.created} 更新=${data.updated} 移除=${data.removed}`, data.request_id);
      qc.invalidateQueries({ queryKey: ["admin", "proxies", "endpoints"] });
      setConfigOpen(false);
      setConfigPool(null);
    },
    onError: (err) => {
      alerts.setError(err);
    },
  });

  const endpointRows = endpointsList.items;

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
    if (!configOpen || !configPool) return;

    const poolId = configPool.id;
    const selected: string[] = [];
    const cfg: Record<string, MemberConfig> = {};
    for (const ep of endpointsList.items) {
      const membership = (ep.pools || []).find((p) => String(p.id) === String(poolId));
      if (!membership) continue;
      selected.push(String(ep.id));
      cfg[String(ep.id)] = { enabled: Boolean(membership.member_enabled), weight: Number(membership.weight) || 1 };
    }

    setSelectedEndpointIds(selected);
    setMemberConfig(cfg);
  }, [configOpen, configPool, endpointsList.items]);

  // Auto-load remaining endpoint pages while config modal is open so membership save is complete.
  useEffect(() => {
    if (!configOpen) return;
    if (!endpointsList.hasMore || endpointsList.isLoadingMore || endpoints.isLoading || endpoints.isFetching) return;
    if (!endpointsList.nextCursor) return;
    endpointsList.loadMore.mutate(endpointsList.nextCursor);
  }, [
    configOpen,
    endpointsList.hasMore,
    endpointsList.isLoadingMore,
    endpointsList.nextCursor,
    endpoints.isLoading,
    endpoints.isFetching,
  ]);

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
    { title: "启用", dataIndex: "enabled", key: "enabled", width: 90, render: (value) => yesNo(value) },
    { title: "描述", dataIndex: "description", key: "description", render: (value) => dash(value) },
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
    { title: "节点启用", dataIndex: "enabled", key: "enabled", width: 90, render: (v) => yesNo(v) },
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
        代理池管理（仅 Hydrate / OAuth）
      </Typography.Title>
      <Alert
        type="info"
        showIcon
        message="池绑定服务补全与令牌出口，不服务公开 /random 出图"
        description="用户侧图片走 Image Edge；此页配置只影响 hydrate_metadata / OAuth 访问 app-api.pixiv.net 时的代理选择。"
      />

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

      <ActionAlerts
        message={alerts.message}
        requestId={alerts.requestId}
        errorMessage={alerts.errorMessage}
        errorRequestId={alerts.errorRequestId}
        requestIdPlacement="description"
      />

      <Card title="代理池列表">
        <QueryState
          query={pools}
          errorMessage="加载代理池失败"
          empty={Boolean(pools.data && pools.data.items.length === 0)}
          emptyMessage="暂无代理池"
          emptyDescription="请先创建一个代理池，然后为它配置代理节点。"
        >
          {pools.data ? (
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
          ) : null}
        </QueryState>
      </Card>

      <Modal
        title="新建代理池"
        open={createOpen}
        onCancel={() => {
          setCreateOpen(false);
          createForm.resetFields();
        }}
        footer={null}
        destroyOnHidden
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
        destroyOnHidden
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
                  // Replace semantics: never save while the membership list is still paging in.
                  if (endpointsList.hasMore || endpointsList.isLoadingMore || endpoints.isLoading || endpoints.isFetching) {
                    return;
                  }
                  setPoolEndpoints.mutate({ poolId: configPool.id, endpointIds: selectedEndpointIds, config: memberConfig });
                }}
                loading={setPoolEndpoints.isPending || endpointsList.isLoadingMore || endpoints.isLoading || endpoints.isFetching}
                disabled={
                  !configPool ||
                  endpointsList.hasMore ||
                  endpointsList.isLoadingMore ||
                  endpoints.isLoading ||
                  endpoints.isFetching
                }
                title={
                  endpointsList.hasMore || endpointsList.isLoadingMore || endpoints.isLoading || endpoints.isFetching
                    ? "节点列表仍在加载，加载完成后再保存以免覆盖未加载成员"
                    : undefined
                }
              >
                保存节点配置
              </Button>
            </Space>
          </Space>
        }
        destroyOnHidden
      >
        {endpoints.isLoading && endpointRows.length === 0 ? (
          <Skeleton active />
        ) : endpoints.isError ? (
          <Alert type="error" showIcon message="加载代理节点失败" description={requestIdDescription(endpoints.error)} />
        ) : endpointRows.length === 0 ? (
          <Alert type="info" showIcon message="暂无代理节点" description="请先在“代理管理”中导入或添加节点，再为代理池配置成员。" />
        ) : (
          <>
            <Typography.Text type="secondary">
              请求ID: {endpointsList.listRequestId || endpoints.data?.request_id || "-"}；已加载 {endpointRows.length} 个节点
              {endpointsList.hasMore ? "（还有更多）" : ""}
            </Typography.Text>
            <Alert
              type="info"
              showIcon
              message="提示"
              description="勾选要加入该代理池的节点；未勾选的节点会从该池移除。成员启用/权重仅对当前代理池生效。列表会自动翻页加载；加载完成前「保存节点配置」会禁用，避免仅保存已加载页导致误删成员。"
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
            {endpointsList.hasMore ? (
              <Button
                style={{ marginTop: 12 }}
                loading={endpointsList.isLoadingMore}
                onClick={() => {
                  if (endpointsList.nextCursor) endpointsList.loadMore.mutate(endpointsList.nextCursor);
                }}
              >
                加载更多节点
              </Button>
            ) : null}
            {endpointsList.loadMore.isError ? (
              <Alert
                type="error"
                showIcon
                message={
                  endpointsList.loadMore.error instanceof Error
                    ? endpointsList.loadMore.error.message
                    : "加载更多节点失败"
                }
                style={{ marginTop: 12 }}
              />
            ) : null}
          </>
        )}
      </Modal>
    </Space>
  );
}

