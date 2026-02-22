import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Form, Input, InputNumber, Select, Skeleton, Space, Switch, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import React, { useEffect, useState } from "react";

import { ApiError, apiJson } from "../api/client";

type ProxyEndpointItem = {
  id: string;
  uri_masked: string;
  source: string;
  source_ref: string | null;
  enabled: boolean;
  scheme?: string;
  host?: string;
  port?: number;
  invalid_host?: boolean;
  latency_ms: number | null;
  status: string | null;
  blacklisted_until: string | null;
  last_error: string | null;
  success_count: number;
  failure_count: number;
  last_ok_at: string | null;
  last_fail_at: string | null;
  pools: Array<{
    id: string;
    name: string;
    pool_enabled: boolean;
    member_enabled: boolean;
    weight: number;
  }>;
  bindings: { primary_count: number; override_count: number };
};

type ProxiesEndpointsResponse = {
  ok: true;
  items: ProxyEndpointItem[];
  request_id: string;
};

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

type ManualImportFormValues = {
  text: string;
  source: "manual";
  conflict_policy: "overwrite" | "skip";
};

type ManualImportResponse = {
  ok: true;
  created: number;
  updated: number;
  skipped: number;
  errors: Array<{ line: number; code: string; message: string }>;
  request_id: string;
};

type EasyProxiesImportFormValues = {
  base_url: string;
  password: string;
  conflict_policy: "overwrite" | "skip_non_easy_proxies";
  bootstrap: boolean;
  host_override?: string;
  attach_pool_id?: number;
  attach_weight?: number;
  recompute_bindings?: boolean;
  max_tokens_per_proxy?: number;
  strict?: boolean;
};

type EasyProxiesImportResponse = {
  ok: true;
  created: number;
  updated: number;
  skipped: number;
  errors: Array<{ code: string; message: string }>;
  warnings?: string[];
  attach?: { pool_id: string; endpoints_total: number; created: number; updated: number };
  bindings?: { pool_id: string; recomputed: number } & Record<string, unknown>;
  request_id: string;
};

type ProxiesProbeResponse = {
  ok: true;
  job_id: string;
  request_id: string;
};

type UpdateProxyEndpointResponse = {
  ok: true;
  endpoint_id: string;
  enabled: boolean;
  request_id: string;
};

type ResetProxyEndpointFailuresResponse = {
  ok: true;
  endpoint_id: string;
  request_id: string;
};

type CleanupInvalidHostsResponse = {
  ok: true;
  dry_run: boolean;
  invalid_hosts: string[];
  matched: number;
  disabled?: number;
  memberships_removed?: number;
  overrides_cleared?: number;
  deleted?: number;
  warnings?: string[];
  request_id: string;
};

function requestIdFromError(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  return err.body?.request_id ? String(err.body.request_id) : null;
}

const columns = (actions: {
  onToggleEnabled: (row: ProxyEndpointItem) => void;
  onResetFailures: (row: ProxyEndpointItem) => void;
  updatePendingId: string | null;
  resetPendingId: string | null;
}): ColumnsType<ProxyEndpointItem> => [
  { title: "节点ID", dataIndex: "id", key: "id" },
  { title: "代理地址（掩码）", dataIndex: "uri_masked", key: "uri_masked" },
  {
    title: "主机",
    key: "host",
    render: (_, row) => {
      const host = String(row.host || "").trim();
      const port = typeof row.port === "number" && Number.isFinite(row.port) ? row.port : null;
      const invalid = Boolean(row.invalid_host);
      const text = host ? (port ? `${host}:${port}` : host) : "-";
      return invalid ? <Typography.Text type="danger">{text}（占位）</Typography.Text> : text;
    },
  },
  {
    title: "来源",
    dataIndex: "source",
    key: "source",
    render: (value: string) => {
      const v = String(value || "").trim();
      if (v === "manual") return "手动";
      if (v === "easy_proxies") return "easy-proxies";
      return v || "-";
    },
  },
  {
    title: "来源引用",
    dataIndex: "source_ref",
    key: "source_ref",
    render: (value: string | null) =>
      value ? (
        <Typography.Text code copyable={{ text: String(value || "") }}>
          {String(value || "")}
        </Typography.Text>
      ) : (
        "-"
      ),
  },
  { title: "启用", dataIndex: "enabled", key: "enabled", render: (value) => (value ? "是" : "否") },
  {
    title: "状态",
    dataIndex: "status",
    key: "status",
    render: (value: string | null) => {
      const normalized = String(value || "").trim().toLowerCase();
      if (!normalized) return "-";
      if (normalized === "ok") return "正常";
      if (normalized === "fail" || normalized === "failed") return "异常";
      if (normalized === "timeout") return "超时";
      return value;
    },
  },
  {
    title: "所属代理池",
    key: "pools",
    render: (_, row) =>
      (row.pools || []).length
        ? (row.pools || [])
            .map((pool) => `${pool.name}(#${pool.id}) 权重=${pool.weight} ${pool.pool_enabled && pool.member_enabled ? "启用" : "停用"}`)
            .join("，")
        : "-",
  },
  {
    title: "绑定统计",
    key: "bindings",
    render: (_, row) =>
      row.bindings ? `主绑定=${row.bindings.primary_count}，覆盖绑定=${row.bindings.override_count}` : "-",
  },
  { title: "成功/失败", key: "ok_fail", render: (_, row) => `${row.success_count}/${row.failure_count}` },
  { title: "最近成功", dataIndex: "last_ok_at", key: "last_ok_at", render: (value) => value || "-" },
  { title: "最近失败", dataIndex: "last_fail_at", key: "last_fail_at", render: (value) => value || "-" },
  { title: "延迟(ms)", dataIndex: "latency_ms", key: "latency_ms" },
  { title: "黑名单至", dataIndex: "blacklisted_until", key: "blacklisted_until" },
  { title: "最后错误", dataIndex: "last_error", key: "last_error" },
  {
    title: "操作",
    key: "actions",
    render: (_, row) => (
      <Space size="small">
        <Button size="small" onClick={() => actions.onToggleEnabled(row)} loading={actions.updatePendingId === row.id}>
          {row.enabled ? "禁用" : "启用"}
        </Button>
        <Button
          size="small"
          onClick={() => actions.onResetFailures(row)}
          loading={actions.resetPendingId === row.id}
          disabled={!row.blacklisted_until && row.failure_count <= 0}
        >
          解除拉黑
        </Button>
      </Space>
    ),
  },
];

export function ProxiesPage() {
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ["admin", "proxies", "endpoints"],
    queryFn: () => apiJson<ProxiesEndpointsResponse>("/admin/api/proxies/endpoints"),
  });

  const poolsQuery = useQuery({
    queryKey: ["admin", "proxy-pools"],
    queryFn: () => apiJson<ProxyPoolsListResponse>("/admin/api/proxy-pools"),
  });

  const [manualErrorMessage, setManualErrorMessage] = useState<string | null>(null);
  const [manualRequestId, setManualRequestId] = useState<string | null>(null);
  const [manualResult, setManualResult] = useState<ManualImportResponse | null>(null);
  const [manualForm] = Form.useForm<ManualImportFormValues>();

  const [probeErrorMessage, setProbeErrorMessage] = useState<string | null>(null);
  const [probeRequestId, setProbeRequestId] = useState<string | null>(null);
  const [probeJobId, setProbeJobId] = useState<string | null>(null);
  const [probeUrl, setProbeUrl] = useState<string>("https://www.pixiv.net/robots.txt");
  const [probeTimeoutMs, setProbeTimeoutMs] = useState<number>(8000);
  const [probeConcurrency, setProbeConcurrency] = useState<number>(10);

  const [easyErrorMessage, setEasyErrorMessage] = useState<string | null>(null);
  const [easyRequestId, setEasyRequestId] = useState<string | null>(null);
  const [easyResult, setEasyResult] = useState<EasyProxiesImportResponse | null>(null);
  const [easyForm] = Form.useForm<EasyProxiesImportFormValues>();

  useEffect(() => {
    const enabledPools = (poolsQuery.data?.items || []).filter((p) => Boolean(p.enabled));
    if (enabledPools.length <= 0) return;
    const current = easyForm.getFieldValue("attach_pool_id");
    if (typeof current === "number" && Number.isFinite(current) && current > 0) return;
    const firstId = Number(enabledPools[0].id);
    if (!Number.isFinite(firstId) || firstId <= 0) return;
    easyForm.setFieldValue("attach_pool_id", firstId);
  }, [poolsQuery.data, easyForm]);

  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionRequestId, setActionRequestId] = useState<string | null>(null);
  const [actionWarningMessage, setActionWarningMessage] = useState<string | null>(null);
  const [actionErrorMessage, setActionErrorMessage] = useState<string | null>(null);
  const [actionErrorRequestId, setActionErrorRequestId] = useState<string | null>(null);

  const [cleanupRecomputeBindings, setCleanupRecomputeBindings] = useState<boolean>(true);
  const [cleanupDeleteOrphans, setCleanupDeleteOrphans] = useState<boolean>(true);
  const [cleanupMaxTokensPerProxy, setCleanupMaxTokensPerProxy] = useState<number>(2);
  const [cleanupStrict, setCleanupStrict] = useState<boolean>(false);

  const updateEndpoint = useMutation({
    mutationFn: (payload: { endpointId: string; enabled: boolean }) =>
      apiJson<UpdateProxyEndpointResponse>(`/admin/api/proxies/endpoints/${encodeURIComponent(payload.endpointId)}`, {
        method: "PUT",
        body: JSON.stringify({ enabled: payload.enabled }),
      }),
    onMutate: () => {
      setActionMessage(null);
      setActionRequestId(null);
      setActionWarningMessage(null);
      setActionErrorMessage(null);
      setActionErrorRequestId(null);
    },
    onSuccess: (data) => {
      setActionMessage(`代理节点已${data.enabled ? "启用" : "禁用"}：${data.endpoint_id}`);
      setActionRequestId(data.request_id);
      queryClient.invalidateQueries({ queryKey: ["admin", "proxies", "endpoints"] });
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        setActionErrorMessage(err.message);
        setActionErrorRequestId(requestIdFromError(err));
        return;
      }
      if (err instanceof Error) {
        setActionErrorMessage(err.message);
        return;
      }
      setActionErrorMessage("代理节点更新失败");
    },
  });

  const resetEndpointFailures = useMutation({
    mutationFn: (payload: { endpointId: string }) =>
      apiJson<ResetProxyEndpointFailuresResponse>(
        `/admin/api/proxies/endpoints/${encodeURIComponent(payload.endpointId)}/reset-failures`,
        {
          method: "POST",
          body: JSON.stringify({}),
        },
      ),
    onMutate: () => {
      setActionMessage(null);
      setActionRequestId(null);
      setActionWarningMessage(null);
      setActionErrorMessage(null);
      setActionErrorRequestId(null);
    },
    onSuccess: (data) => {
      setActionMessage(`代理节点已重置失败并解除拉黑：${data.endpoint_id}`);
      setActionRequestId(data.request_id);
      queryClient.invalidateQueries({ queryKey: ["admin", "proxies", "endpoints"] });
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        setActionErrorMessage(err.message);
        setActionErrorRequestId(requestIdFromError(err));
        return;
      }
      if (err instanceof Error) {
        setActionErrorMessage(err.message);
        return;
      }
      setActionErrorMessage("重置失败/解除拉黑失败");
    },
  });

  const cleanupInvalidHosts = useMutation({
    mutationFn: () =>
      apiJson<CleanupInvalidHostsResponse>("/admin/api/proxies/endpoints/cleanup-invalid-hosts", {
        method: "POST",
        body: JSON.stringify({
          recompute_bindings: cleanupRecomputeBindings,
          delete_orphans: cleanupDeleteOrphans,
          max_tokens_per_proxy: Math.max(1, Math.trunc(cleanupMaxTokensPerProxy || 2)),
          strict: cleanupStrict,
        }),
      }),
    onMutate: () => {
      setActionMessage(null);
      setActionRequestId(null);
      setActionWarningMessage(null);
      setActionErrorMessage(null);
      setActionErrorRequestId(null);
    },
    onSuccess: (data) => {
      const parts = [
        `匹配: ${data.matched}`,
        typeof data.disabled === "number" ? `禁用: ${data.disabled}` : null,
        typeof data.memberships_removed === "number" ? `移除池成员: ${data.memberships_removed}` : null,
        typeof data.overrides_cleared === "number" ? `清理覆盖绑定: ${data.overrides_cleared}` : null,
        typeof data.deleted === "number" ? `删除: ${data.deleted}` : null,
      ].filter(Boolean);
      setActionMessage(`清理完成（${parts.join("，")}）`);
      setActionRequestId(data.request_id);
      if (Array.isArray(data.warnings) && data.warnings.length) {
        setActionWarningMessage(data.warnings.slice(0, 10).join("；"));
      }
      queryClient.invalidateQueries({ queryKey: ["admin", "proxies", "endpoints"] });
      queryClient.invalidateQueries({ queryKey: ["admin", "bindings"] });
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        setActionErrorMessage(err.message);
        setActionErrorRequestId(requestIdFromError(err));
        return;
      }
      if (err instanceof Error) {
        setActionErrorMessage(err.message);
        return;
      }
      setActionErrorMessage("清理失败");
    },
  });

  const manualImport = useMutation({
    mutationFn: (values: ManualImportFormValues) =>
      apiJson<ManualImportResponse>("/admin/api/proxies/endpoints/import", {
        method: "POST",
        body: JSON.stringify(values),
      }),
    onMutate: () => {
      setManualErrorMessage(null);
      setManualRequestId(null);
      setManualResult(null);
    },
    onSuccess: (data) => {
      setManualResult(data);
      setManualRequestId(data.request_id);
      manualForm.setFieldValue("text", "");
      queryClient.invalidateQueries({ queryKey: ["admin", "proxies", "endpoints"] });
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        setManualErrorMessage(err.message);
        setManualRequestId(requestIdFromError(err));
        return;
      }
      if (err instanceof Error) {
        setManualErrorMessage(err.message);
        return;
      }
      setManualErrorMessage("手动导入失败");
    },
  });

  const probe = useMutation({
    mutationFn: () =>
      apiJson<ProxiesProbeResponse>("/admin/api/proxies/probe", {
        method: "POST",
        body: JSON.stringify({
          probe_url: String(probeUrl || "").trim() || undefined,
          timeout_ms:
            Number.isFinite(Number(probeTimeoutMs)) && Number(probeTimeoutMs) > 0 ? Number(probeTimeoutMs) : undefined,
          concurrency:
            Number.isFinite(Number(probeConcurrency)) && Number(probeConcurrency) > 0 ? Number(probeConcurrency) : undefined,
        }),
      }),
    onMutate: () => {
      setProbeErrorMessage(null);
      setProbeRequestId(null);
      setProbeJobId(null);
    },
    onSuccess: (data) => {
      setProbeRequestId(data.request_id);
      setProbeJobId(data.job_id);
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        setProbeErrorMessage(err.message);
        setProbeRequestId(requestIdFromError(err));
        return;
      }
      if (err instanceof Error) {
        setProbeErrorMessage(err.message);
        return;
      }
      setProbeErrorMessage("探测任务入队失败");
    },
  });

  const easyImport = useMutation({
    mutationFn: (values: EasyProxiesImportFormValues) => {
      const payload: Record<string, unknown> = {
        base_url: String(values.base_url || "").trim(),
        password: String(values.password || ""),
        conflict_policy: values.conflict_policy,
      };
      if (String(values.host_override || "").trim()) payload.host_override = String(values.host_override || "").trim();
      if (values.bootstrap) {
        if (typeof values.attach_pool_id === "number") payload.attach_pool_id = values.attach_pool_id;
        if (typeof values.attach_weight === "number") payload.attach_weight = values.attach_weight;
        payload.recompute_bindings = values.recompute_bindings !== false;
        if (payload.recompute_bindings) {
          if (typeof values.max_tokens_per_proxy === "number") payload.max_tokens_per_proxy = values.max_tokens_per_proxy;
          payload.strict = values.strict ?? false;
        }
      }
      return apiJson<EasyProxiesImportResponse>("/admin/api/proxies/easy-proxies/import", {
        method: "POST",
        body: JSON.stringify(payload),
      });
    },
    onMutate: () => {
      setEasyErrorMessage(null);
      setEasyRequestId(null);
      setEasyResult(null);
    },
    onSuccess: (data) => {
      setEasyResult(data);
      setEasyRequestId(data.request_id);
      queryClient.invalidateQueries({ queryKey: ["admin", "proxies", "endpoints"] });
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        setEasyErrorMessage(err.message);
        setEasyRequestId(requestIdFromError(err));
        return;
      }
      if (err instanceof Error) {
        setEasyErrorMessage(err.message);
        return;
      }
      setEasyErrorMessage("从 easy-proxies 导入失败");
    },
  });

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        代理管理
      </Typography.Title>

      {actionMessage ? <Alert type="success" showIcon message={actionMessage} /> : null}
      {actionRequestId ? <Typography.Text type="secondary">请求ID: {actionRequestId}</Typography.Text> : null}
      {actionWarningMessage ? <Alert type="warning" showIcon message={actionWarningMessage} /> : null}
      {actionErrorMessage ? <Alert type="error" showIcon message={actionErrorMessage} /> : null}
      {actionErrorRequestId ? <Typography.Text type="secondary">请求ID: {actionErrorRequestId}</Typography.Text> : null}

      <Card title="维护操作">
        <Space wrap>
          <Space>
            <Typography.Text>重算 token 绑定</Typography.Text>
            <Switch checked={cleanupRecomputeBindings} onChange={(v) => setCleanupRecomputeBindings(Boolean(v))} />
          </Space>
          <Space>
            <Typography.Text>删除无引用节点</Typography.Text>
            <Switch checked={cleanupDeleteOrphans} onChange={(v) => setCleanupDeleteOrphans(Boolean(v))} />
          </Space>
          <Space>
            <Typography.Text>单代理最多绑定令牌数</Typography.Text>
            <InputNumber
              min={1}
              max={1000}
              value={cleanupMaxTokensPerProxy}
              onChange={(v) => setCleanupMaxTokensPerProxy(Number(v) || 2)}
            />
          </Space>
          <Space>
            <Typography.Text>严格容量</Typography.Text>
            <Switch checked={cleanupStrict} onChange={(v) => setCleanupStrict(Boolean(v))} />
          </Space>
          <Button type="primary" danger onClick={() => cleanupInvalidHosts.mutate()} loading={cleanupInvalidHosts.isPending}>
            清理 0.0.0.0/localhost
          </Button>
        </Space>
        <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
          作用：禁用并从代理池移除无效占位主机（如 0.0.0.0/localhost），可选重算绑定与删除无引用记录。
        </Typography.Paragraph>
      </Card>

      <Card title="手动导入代理节点">
        {manualErrorMessage ? <Alert type="error" showIcon message={manualErrorMessage} /> : null}
        {manualRequestId ? <Typography.Text type="secondary">请求ID: {manualRequestId}</Typography.Text> : null}

        <Form<ManualImportFormValues>
          form={manualForm}
          layout="vertical"
          initialValues={{ text: "", source: "manual", conflict_policy: "overwrite" }}
          onFinish={(values) => manualImport.mutate(values)}
        >
          <Form.Item label="代理地址（每行一个）" name="text" rules={[{ required: true, message: "请输入代理地址" }]}>
            <Input.TextArea rows={6} placeholder="http://user:pass@1.2.3.4:8080" />
          </Form.Item>
          <Form.Item label="冲突策略" name="conflict_policy">
            <Select
              options={[
                { value: "overwrite", label: "覆盖同地址节点" },
                { value: "skip", label: "跳过同地址节点" },
              ]}
              style={{ minWidth: 220 }}
            />
          </Form.Item>

          <Button type="primary" htmlType="submit" loading={manualImport.isPending}>
            导入
          </Button>
        </Form>

        {manualImport.isPending ? <Alert type="info" showIcon message="正在导入代理节点..." style={{ marginTop: 12 }} /> : null}
        {manualResult ? (
          <Alert
            type="success"
            showIcon
            message="手动导入完成"
            description={`新增: ${manualResult.created}，更新: ${manualResult.updated}，跳过: ${manualResult.skipped}，错误: ${manualResult.errors.length}`}
            style={{ marginTop: 12 }}
          />
        ) : null}

        <Alert
          type="info"
          showIcon
          message="安全说明"
          description="代理密码仅写入不回显，列表中只显示脱敏后的地址。"
          style={{ marginTop: 12 }}
        />
      </Card>

      <Card title="从 easy-proxies 导入（推荐）">
        {easyErrorMessage ? <Alert type="error" showIcon message={easyErrorMessage} /> : null}
        {easyRequestId ? <Typography.Text type="secondary">请求ID: {easyRequestId}</Typography.Text> : null}

        <Alert
          type="info"
          showIcon
          message="easy-proxies 使用说明"
          description={
            <div>
              <div>1) “面板地址”填 easy-proxies 的 Web/API 地址（不是导出的代理地址）。</div>
              <div>2) “访问密码”是面板登录密码；代理账号/密码来自导出的代理 URI。</div>
              <div>3) 若代理密码包含“@”，导出的 URI 可能形如 `http://user:pass@123@host:2323`（多个 @ 属正常）；也支持 `%40` 编码写法。</div>
              <div>4) 单入口 pool：你通常只会看到 1 个端口（例如 2323），该端口在 easy-proxies 内部轮换节点，本项目无法直接展示 pool 内部实际命中的端口。</div>
               <div>5) multi-port：你会看到很多不同端口（例如 24004/24005/...），每个端口就是一个独立节点；可在本项目侧探测并禁用不稳定节点。</div>
               <div>6) hybrid：可能同时存在 pool 入口与 multi-port 节点，建议优先导入 multi-port 节点以便精细管理。</div>
               <div>7) 使用单入口 pool 时，可在“代理池”页面把该入口的成员权重设置为节点数，以贴近真实容量与绑定容量。</div>
               <div>8) 若导出结果里 host 是 0.0.0.0/127.0.0.1/localhost 等占位符，本项目会自动替换为“面板地址”的 host，避免导入后不可连接。</div>
             </div>
           }
           style={{ marginBottom: 12 }}
         />

        <Form<EasyProxiesImportFormValues>
          form={easyForm}
          layout="vertical"
          initialValues={{
            base_url: "",
            password: "",
            conflict_policy: "skip_non_easy_proxies",
            bootstrap: true,
            attach_pool_id: 1,
            attach_weight: 1,
            recompute_bindings: true,
            max_tokens_per_proxy: 2,
            strict: false,
          }}
          onFinish={(values) => {
            let attachPoolId = values.attach_pool_id;
            if (values.bootstrap && typeof attachPoolId !== "number") {
              const enabledPools = (poolsQuery.data?.items || []).filter((p) => Boolean(p.enabled));
              if (enabledPools.length === 1) {
                const autoId = Number(enabledPools[0].id);
                if (Number.isFinite(autoId) && autoId > 0) attachPoolId = autoId;
              }
            }
            if (values.bootstrap && typeof attachPoolId !== "number") {
              setEasyErrorMessage("请选择要加入的代理池");
              return;
            }
            easyImport.mutate({ ...values, attach_pool_id: attachPoolId });
          }}
        >
          <Form.Item
            label="面板地址"
            name="base_url"
            rules={[{ required: true, message: "请输入 easy-proxies 面板地址" }]}
            extra="示例：http://你的IP:15666（不要填导出的代理地址）。"
          >
            <Input placeholder="http://easy-proxies:15666" />
          </Form.Item>
          <Form.Item label="访问密码（可选）" name="password">
            <Input.Password placeholder="可选" />
          </Form.Item>
          <Form.Item label="冲突策略" name="conflict_policy">
            <Select
              options={[
                { value: "overwrite", label: "覆盖同地址节点" },
                { value: "skip_non_easy_proxies", label: "仅覆盖 easy-proxies 导入的节点" },
              ]}
            />
          </Form.Item>

          <Form.Item
            label="一键接入（推荐）"
            name="bootstrap"
            valuePropName="checked"
            extra="开启后：导入完成会自动加入代理池，并可选重算 token 绑定；适合 Docker Compose 启动后一键可用。"
          >
            <Switch />
          </Form.Item>

          <Form.Item noStyle shouldUpdate={(prev, cur) => prev.bootstrap !== cur.bootstrap}>
            {({ getFieldValue }) =>
              getFieldValue("bootstrap") ? (
                <>
                  <Form.Item
                    label="加入代理池"
                    name="attach_pool_id"
                    rules={[{ required: true, message: "请选择代理池" }]}
                    extra={poolsQuery.isError ? "代理池列表加载失败（可稍后刷新或先到“代理池”页面创建）。" : undefined}
                  >
                    <Select
                      placeholder="选择一个代理池"
                      loading={poolsQuery.isLoading}
                      options={(poolsQuery.data?.items || [])
                        .filter((p) => Boolean(p.enabled))
                        .map((p) => ({ value: Number(p.id), label: `${p.name}(#${p.id})` }))}
                      style={{ minWidth: 260 }}
                    />
                  </Form.Item>
                  <Form.Item label="成员权重" name="attach_weight" extra="权重越大，越可能被选中；也会影响 token 绑定容量。">
                    <InputNumber min={0} max={1000} style={{ width: 180 }} />
                  </Form.Item>
                  <Form.Item label="重算 token 绑定" name="recompute_bindings" valuePropName="checked">
                    <Switch />
                  </Form.Item>
                  <Form.Item noStyle shouldUpdate={(p, c) => p.recompute_bindings !== c.recompute_bindings}>
                    {({ getFieldValue: get2 }) =>
                      get2("recompute_bindings") ? (
                        <>
                          <Form.Item label="单代理最多绑定令牌数" name="max_tokens_per_proxy">
                            <InputNumber min={1} max={1000} style={{ width: 180 }} />
                          </Form.Item>
                          <Form.Item
                            label="严格容量校验"
                            name="strict"
                            valuePropName="checked"
                            extra="关闭后即使代理容量不足也会尽量分配（更稳健，但可能多个 token 共享同一节点）。"
                          >
                            <Switch />
                          </Form.Item>
                        </>
                      ) : null
                    }
                  </Form.Item>
                </>
              ) : null
            }
          </Form.Item>

          <Button type="primary" htmlType="submit" loading={easyImport.isPending}>
            开始导入
          </Button>
        </Form>

        {easyImport.isPending ? <Alert type="info" showIcon message="正在从外部代理服务导入..." style={{ marginTop: 12 }} /> : null}
        {easyResult ? (
          <>
            <Alert
              type="success"
              showIcon
              message="外部代理服务导入完成"
              description={
                [
                  `新增: ${easyResult.created}，更新: ${easyResult.updated}，跳过: ${easyResult.skipped}，错误: ${easyResult.errors.length}`,
                  easyResult.attach
                    ? `加入代理池 #${easyResult.attach.pool_id}: 节点总数=${easyResult.attach.endpoints_total}（新增=${easyResult.attach.created}，更新=${easyResult.attach.updated}）`
                    : null,
                  easyResult.bindings ? `重算绑定: ${String(easyResult.bindings.recomputed ?? "-")}` : null,
                ]
                  .filter(Boolean)
                  .join("；")
              }
              style={{ marginTop: 12 }}
            />
            {Array.isArray(easyResult.warnings) && easyResult.warnings.length > 0 ? (
              <Alert type="warning" showIcon message="导入提示" description={easyResult.warnings.join(" ")} style={{ marginTop: 12 }} />
            ) : null}
          </>
        ) : null}
      </Card>

      <Card title="代理节点列表">
        <Alert
          type="info"
          showIcon
          message="提示"
          description="列表中每一行代表一个实际可用的代理入口（host:port）。若只看到 1 个端口，通常表示你导入的是单入口 pool；multi-port/hybrid 会出现多个不同端口节点。"
          style={{ marginBottom: 12 }}
        />
        <Space wrap style={{ marginBottom: 12 }}>
          <Button type="primary" onClick={() => probe.mutate()} loading={probe.isPending}>
            启动健康探测任务
          </Button>
          <Button onClick={() => query.refetch()} loading={query.isFetching}>
            刷新列表
          </Button>
        </Space>
        <Space wrap style={{ marginBottom: 12 }}>
          <Typography.Text>探测URL:</Typography.Text>
          <Input
            value={probeUrl}
            onChange={(e) => setProbeUrl(e.target.value)}
            placeholder="留空使用默认"
            style={{ width: 360 }}
          />
          <Typography.Text>超时(ms):</Typography.Text>
          <InputNumber min={100} max={600000} value={probeTimeoutMs} onChange={(v) => setProbeTimeoutMs(typeof v === "number" ? v : 8000)} />
          <Typography.Text>并发:</Typography.Text>
          <InputNumber min={1} max={200} value={probeConcurrency} onChange={(v) => setProbeConcurrency(typeof v === "number" ? v : 10)} />
        </Space>

        {probe.isPending ? <Alert type="info" showIcon message="探测任务入队中..." style={{ marginBottom: 12 }} /> : null}
        {probeErrorMessage ? <Alert type="error" showIcon message={probeErrorMessage} style={{ marginBottom: 12 }} /> : null}
        {probeJobId ? (
          <Alert
            type="success"
            showIcon
            message="探测任务已入队"
            description={`任务ID: ${probeJobId}`}
            style={{ marginBottom: 12 }}
          />
        ) : null}
        {probeRequestId ? <Typography.Text type="secondary">请求ID: {probeRequestId}</Typography.Text> : null}

        {query.isLoading ? (
          <Skeleton active />
        ) : query.isError ? (
          <Alert
            type="error"
            showIcon
            message="加载代理节点失败"
            description={requestIdFromError(query.error) ? `请求ID: ${requestIdFromError(query.error)}` : ""}
          />
        ) : !query.data ? (
          <Skeleton active />
        ) : query.data.items.length === 0 ? (
          <Alert type="info" showIcon message="暂无代理节点" description="请先导入代理节点以启用代理路由。" />
        ) : (
          <>
            <Typography.Text type="secondary">请求ID: {query.data.request_id}</Typography.Text>
            <Table<ProxyEndpointItem>
              rowKey={(row) => row.id}
              columns={columns({
                onToggleEnabled: (row) => updateEndpoint.mutate({ endpointId: row.id, enabled: !row.enabled }),
                updatePendingId: updateEndpoint.isPending ? updateEndpoint.variables?.endpointId ?? null : null,
                onResetFailures: (row) => resetEndpointFailures.mutate({ endpointId: row.id }),
                resetPendingId: resetEndpointFailures.isPending ? resetEndpointFailures.variables?.endpointId ?? null : null,
              })}
              dataSource={query.data.items}
              pagination={false}
              size="small"
              scroll={{ x: 1500 }}
              style={{ marginTop: 12 }}
            />
          </>
        )}
      </Card>
    </Space>
  );
}
