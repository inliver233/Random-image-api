import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Form, Input, InputNumber, Modal, Skeleton, Space, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import React, { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { ApiError, apiJson } from "../api/client";

type ProxyRef = {
  id: string;
  scheme: string;
  host: string;
  port: number;
  username: string;
};

type BindingItem = {
  id: string;
  created_at: string;
  updated_at: string;
  token: { id: string; label: string | null };
  pool: { id: string; name: string };
  primary_proxy: ProxyRef;
  override_proxy: ProxyRef | null;
  override_expires_at: string | null;
  effective_proxy_id: string;
  effective_mode: "primary" | "override";
};

type BindingsListResponse = {
  ok: true;
  items: BindingItem[];
  summary?: { pool_id: string; pool_endpoints_total: number; pool_endpoints_enabled: number };
  request_id: string;
};

type RecomputeResponse = {
  ok: true;
  pool_id: string;
  recomputed: number;
  strict?: boolean;
  over_capacity_assigned?: number;
  capacity?: number;
  token_count?: number;
  proxy_count?: number;
  max_tokens_per_proxy?: number;
  request_id: string;
};

type OverrideResponse = {
  ok: true;
  binding_id: string;
  override_proxy_id: string;
  override_expires_at: string;
  request_id: string;
};

type ClearOverrideResponse = {
  ok: true;
  binding_id: string;
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

function formatProxy(proxy: ProxyRef | null): string {
  if (!proxy) return "";
  const host = String(proxy.host || "").includes(":") && !String(proxy.host || "").startsWith("[") ? `[${proxy.host}]` : proxy.host;
  const user = String(proxy.username || "").trim();
  const auth = user ? `${user}@` : "";
  return `节点#${proxy.id} ${proxy.scheme}://${auth}${host}:${proxy.port}`;
}

function modeLabel(mode: "primary" | "override"): string {
  return mode === "override" ? "覆盖代理" : "主代理";
}

function stabilityLabel(row: BindingItem): string {
  if (row.effective_mode === "override") return "固定（覆盖到期/清除会变）";
  return "固定（重算会变）";
}

const columns = (actions: {
  onOpenOverride: (row: BindingItem) => void;
  onClearOverride: (row: BindingItem) => void;
  overridePendingId: string | null;
  clearPendingId: string | null;
}): ColumnsType<BindingItem> => [
  {
    title: "令牌",
    key: "token",
    render: (_, row) => (row.token.label ? `${row.token.label}（#${row.token.id}）` : `#${row.token.id}`),
  },
  {
    title: "代理池",
    key: "pool",
    render: (_, row) => `${row.pool.name}（#${row.pool.id}）`,
  },
  {
    title: "当前生效代理",
    key: "effective",
    render: (_, row) => (row.effective_mode === "override" ? formatProxy(row.override_proxy) : formatProxy(row.primary_proxy)),
  },
  { title: "生效模式", dataIndex: "effective_mode", key: "effective_mode", render: (value) => modeLabel(value) },
  { title: "固定性", key: "stability", render: (_, row) => stabilityLabel(row) },
  { title: "绑定更新时间", dataIndex: "updated_at", key: "updated_at", render: (value) => value || "-" },
  { title: "主代理", key: "primary_proxy", render: (_, row) => formatProxy(row.primary_proxy) },
  { title: "覆盖代理", key: "override_proxy", render: (_, row) => formatProxy(row.override_proxy) || "-" },
  { title: "覆盖过期时间", dataIndex: "override_expires_at", key: "override_expires_at", render: (value) => value || "-" },
  {
    title: "操作",
    key: "actions",
    render: (_, row) => (
      <Space wrap>
        <Button size="small" onClick={() => actions.onOpenOverride(row)} loading={actions.overridePendingId === row.id}>
          设置覆盖
        </Button>
        <Button
          size="small"
          onClick={() => actions.onClearOverride(row)}
          loading={actions.clearPendingId === row.id}
          disabled={!row.override_proxy}
        >
          清除覆盖
        </Button>
      </Space>
    ),
  },
];

type OverrideFormValues = {
  override_proxy_id: number;
  ttl_minutes: number;
  reason: string;
};

export function BindingsPage() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();

  const initialPoolId = useMemo(() => {
    const raw = searchParams.get("pool_id");
    const value = raw ? Number.parseInt(raw, 10) : 1;
    return Number.isFinite(value) && value > 0 ? value : 1;
  }, [searchParams]);

  const [poolId, setPoolId] = useState<number>(initialPoolId);
  const [maxTokensPerProxy, setMaxTokensPerProxy] = useState<number>(2);

  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionRequestId, setActionRequestId] = useState<string | null>(null);
  const [actionErrorMessage, setActionErrorMessage] = useState<string | null>(null);
  const [actionErrorRequestId, setActionErrorRequestId] = useState<string | null>(null);

  const [overrideOpen, setOverrideOpen] = useState(false);
  const [overrideBinding, setOverrideBinding] = useState<BindingItem | null>(null);
  const [overrideForm] = Form.useForm<OverrideFormValues>();

  const query = useQuery({
    queryKey: ["admin", "bindings", { poolId }],
    queryFn: () => apiJson<BindingsListResponse>(`/admin/api/bindings?pool_id=${poolId}`),
  });

  const recompute = useMutation({
    mutationFn: (vars: { strict?: boolean } | undefined) =>
      apiJson<RecomputeResponse>("/admin/api/bindings/recompute", {
        method: "POST",
        body: JSON.stringify({
          pool_id: poolId,
          max_tokens_per_proxy: maxTokensPerProxy,
          strict: vars?.strict !== undefined ? Boolean(vars.strict) : true,
        }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "bindings", { poolId }] });
    },
  });

  const setOverride = useMutation({
    mutationFn: (payload: { bindingId: string; override_proxy_id: number; ttl_ms: number; reason: string }) =>
      apiJson<OverrideResponse>(`/admin/api/bindings/${encodeURIComponent(payload.bindingId)}/override`, {
        method: "POST",
        body: JSON.stringify({
          override_proxy_id: payload.override_proxy_id,
          ttl_ms: payload.ttl_ms,
          reason: payload.reason,
        }),
      }),
    onMutate: () => {
      setActionMessage(null);
      setActionRequestId(null);
      setActionErrorMessage(null);
      setActionErrorRequestId(null);
    },
    onSuccess: (data) => {
      setActionMessage(`已设置覆盖代理：绑定 #${data.binding_id} → 节点 #${data.override_proxy_id}`);
      setActionRequestId(data.request_id);
      setOverrideOpen(false);
      setOverrideBinding(null);
      overrideForm.resetFields();
      queryClient.invalidateQueries({ queryKey: ["admin", "bindings", { poolId }] });
    },
    onError: (err) => {
      setActionErrorMessage(messageFromError(err));
      setActionErrorRequestId(requestIdFromError(err));
    },
  });

  const clearOverride = useMutation({
    mutationFn: (bindingId: string) =>
      apiJson<ClearOverrideResponse>(`/admin/api/bindings/${encodeURIComponent(bindingId)}/clear-override`, { method: "POST" }),
    onMutate: () => {
      setActionMessage(null);
      setActionRequestId(null);
      setActionErrorMessage(null);
      setActionErrorRequestId(null);
    },
    onSuccess: (data) => {
      setActionMessage(`已清除覆盖代理：绑定 #${data.binding_id}`);
      setActionRequestId(data.request_id);
      queryClient.invalidateQueries({ queryKey: ["admin", "bindings", { poolId }] });
    },
    onError: (err) => {
      setActionErrorMessage(messageFromError(err));
      setActionErrorRequestId(requestIdFromError(err));
    },
  });

  const setPoolIdAndSyncUrl = (next: number) => {
    const value = Number.isFinite(next) && next > 0 ? next : 1;
    setPoolId(value);
    const params = new URLSearchParams(searchParams);
    params.set("pool_id", String(value));
    setSearchParams(params, { replace: true });
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        令牌与代理绑定
      </Typography.Title>

      <Alert
        type="info"
        showIcon
        message="说明"
        description={
          (() => {
            const summary = query.data?.summary;
            const parts: string[] = [
              "绑定默认是固定的：仅会在你点击“重新计算绑定”、设置覆盖、清除覆盖或覆盖到期后发生变化。",
              "这里显示的是“本项目侧的代理入口（host:port）”。如果你导入的是 easy-proxies 单入口 pool，你只会看到 pool 入口端口（例如 2323）；pool 内部实际命中的节点端口需要在 easy-proxies 面板查看。",
            ];
            if (summary && typeof summary.pool_endpoints_total === "number" && typeof summary.pool_endpoints_enabled === "number") {
              parts.push(`当前代理池入口：可用 ${summary.pool_endpoints_enabled}/${summary.pool_endpoints_total}。`);
              if (summary.pool_endpoints_enabled === 1) {
                parts.push("可用入口=1 时，所有令牌绑定到同一入口属于正常现象。");
              }
            }
            return parts.join(" ");
          })()
        }
      />

      {actionMessage ? <Alert type="success" showIcon message={actionMessage} /> : null}
      {actionRequestId ? <Typography.Text type="secondary">请求ID: {actionRequestId}</Typography.Text> : null}
      {actionErrorMessage ? <Alert type="error" showIcon message={actionErrorMessage} /> : null}
      {actionErrorRequestId ? <Typography.Text type="secondary">请求ID: {actionErrorRequestId}</Typography.Text> : null}

      <Card>
        <Space wrap>
          <Typography.Text>代理池ID:</Typography.Text>
          <InputNumber min={1} value={poolId} onChange={(value) => setPoolIdAndSyncUrl(Number(value || 1))} />
          <Typography.Text>单代理最多绑定令牌数:</Typography.Text>
          <InputNumber
            min={1}
            max={1000}
            value={maxTokensPerProxy}
            onChange={(value) => setMaxTokensPerProxy(Number(value || 2))}
          />
          <Button type="primary" onClick={() => recompute.mutate({ strict: true })} loading={recompute.isPending}>
            重新计算绑定
          </Button>
        </Space>

        {recompute.isError ? (
          <Alert
            type="error"
            showIcon
            message="重新计算绑定失败"
            description={
              (() => {
                const rid = requestIdFromError(recompute.error);
                const msg = messageFromError(recompute.error);
                const details =
                  recompute.error instanceof ApiError && recompute.error.body && typeof recompute.error.body.details === "object"
                    ? recompute.error.body.details
                    : null;
                const tokenCount = details && typeof details.token_count === "number" ? details.token_count : null;
                const proxyCount = details && typeof details.proxy_count === "number" ? details.proxy_count : null;
                const maxPer = details && typeof details.max_tokens_per_proxy === "number" ? details.max_tokens_per_proxy : null;
                const weightSum = details && typeof details.weight_sum === "number" ? details.weight_sum : null;
                const capacity = details && typeof details.capacity === "number" ? details.capacity : null;
                const parts = [msg];
                if (tokenCount !== null && proxyCount !== null && maxPer !== null) {
                  const extra = [`令牌数=${tokenCount}，代理数=${proxyCount}，单代理上限=${maxPer}`];
                  if (weightSum !== null) extra.push(`权重和=${weightSum}`);
                  if (capacity !== null) extra.push(`总容量=${capacity}`);
                  parts.push(extra.join("，"));
                }
                if (rid) parts.push(`请求ID: ${rid}`);
                return parts.filter((p) => String(p || "").trim()).join("；");
              })()
            }
            style={{ marginTop: 12 }}
          />
        ) : null}

        {recompute.isError &&
        recompute.error instanceof ApiError &&
        recompute.error.body &&
        recompute.error.body.code === "BAD_REQUEST" &&
        typeof recompute.error.body.details?.token_count === "number" &&
        typeof recompute.error.body.details?.proxy_count === "number" &&
        typeof recompute.error.body.details?.max_tokens_per_proxy === "number" ? (
          <Button style={{ marginTop: 12 }} onClick={() => recompute.mutate({ strict: false })} loading={recompute.isPending}>
            继续计算（允许超出容量）
          </Button>
        ) : null}

        {recompute.isSuccess ? (
          <Alert
            type="success"
            showIcon
            message="重新计算绑定完成"
            description={`重算数量: ${recompute.data.recomputed}，请求ID: ${recompute.data.request_id}`}
            style={{ marginTop: 12 }}
          />
        ) : null}
      </Card>

      <Modal
        title={overrideBinding ? `设置覆盖代理（绑定 #${overrideBinding.id}）` : "设置覆盖代理"}
        open={overrideOpen}
        onCancel={() => {
          setOverrideOpen(false);
          setOverrideBinding(null);
          overrideForm.resetFields();
        }}
        footer={null}
        destroyOnClose
      >
        <Form<OverrideFormValues>
          form={overrideForm}
          layout="vertical"
          initialValues={{ override_proxy_id: 0, ttl_minutes: 60, reason: "" }}
          onFinish={(values) => {
            const bindingId = overrideBinding?.id;
            if (!bindingId) return;
            const ttlMinutes = Number(values.ttl_minutes || 0);
            const ttlMs = Math.trunc(ttlMinutes * 60 * 1000);
            setOverride.mutate({
              bindingId,
              override_proxy_id: Number(values.override_proxy_id),
              ttl_ms: ttlMs,
              reason: String(values.reason || "").trim(),
            });
          }}
        >
          <Form.Item
            label="覆盖代理节点ID"
            name="override_proxy_id"
            rules={[{ required: true, message: "请输入代理节点ID" }]}
            extra="必须在当前代理池内且处于启用状态。可在“代理管理”页面查看节点ID。"
          >
            <InputNumber min={1} placeholder="例如：10" style={{ width: "100%" }} />
          </Form.Item>

          <Form.Item
            label="有效期（分钟）"
            name="ttl_minutes"
            rules={[{ required: true, message: "请输入有效期" }]}
            extra="例如：60=1小时。最大 43200 分钟（30天）。"
          >
            <InputNumber min={1} max={43200} placeholder="例如：60" style={{ width: "100%" }} />
          </Form.Item>

          <Form.Item label="原因（可选）" name="reason">
            <Input placeholder="例如：临时切换节点排查问题" />
          </Form.Item>

          <Space style={{ width: "100%", justifyContent: "flex-end" }}>
            <Button
              onClick={() => {
                setOverrideOpen(false);
                setOverrideBinding(null);
                overrideForm.resetFields();
              }}
              disabled={setOverride.isPending}
            >
              取消
            </Button>
            <Button type="primary" htmlType="submit" loading={setOverride.isPending}>
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
          message="加载绑定列表失败"
          description={requestIdFromError(query.error) ? `请求ID: ${requestIdFromError(query.error)}` : ""}
        />
      ) : !query.data ? (
        <Skeleton active />
      ) : query.data.items.length === 0 ? (
        <Alert
          type="info"
          showIcon
          message="暂无绑定数据"
          description="请先创建代理池并加入代理节点，然后点击“重新计算绑定”。"
        />
      ) : (
        <Card>
          <Typography.Text type="secondary">请求ID: {query.data.request_id}</Typography.Text>
          <Table<BindingItem>
            rowKey={(row) => row.id}
            columns={columns({
              onOpenOverride: (row) => {
                setOverrideBinding(row);
                setOverrideOpen(true);
                const currentId = row.override_proxy?.id || row.primary_proxy.id;
                const parsed = Number.parseInt(String(currentId || "0"), 10);
                overrideForm.setFieldsValue({
                  override_proxy_id: Number.isFinite(parsed) && parsed > 0 ? parsed : 1,
                  ttl_minutes: 60,
                  reason: "",
                });
              },
              onClearOverride: (row) => clearOverride.mutate(row.id),
              overridePendingId: setOverride.isPending ? setOverride.variables?.bindingId ?? null : null,
              clearPendingId: clearOverride.isPending ? clearOverride.variables ?? null : null,
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
