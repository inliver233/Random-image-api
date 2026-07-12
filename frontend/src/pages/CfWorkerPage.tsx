import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Form,
  Input,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
} from "antd";
import React from "react";

import { ActionAlerts } from "../admin/ActionAlerts";
import { messageFromError, requestIdFromError } from "../admin/errors";
import { QueryState } from "../admin/QueryState";
import { useActionAlerts } from "../admin/useActionAlerts";
import { apiJson } from "../api/client";

type BaseCooldownRow = {
  base_url?: string;
  cooling?: boolean;
  fail_streak?: number;
  cool_remaining_s?: number;
  cooldown_base_s?: number;
  cooldown_max_s?: number;
};

type PoolSide = {
  env_base_urls?: string[];
  runtime_base_urls?: string[];
  merged_base_urls?: string[];
  members?: Array<{ kind?: string; base_url?: string; source?: string }>;
  runtime_enabled?: boolean;
  env_enabled?: boolean;
  base_cooldown?: BaseCooldownRow[];
};

type CfWorkersPoolResponse = {
  ok: true;
  api?: PoolSide;
  image?: PoolSide;
  egress_policy?: {
    residential_egress_emergency_only?: boolean;
    force_residential_emergency?: boolean;
    cf_api_proxy_ready?: boolean;
    image_edge_ready?: boolean;
  };
  note?: string;
  request_id: string;
};

type DeployFormValues = {
  kind: "api" | "image";
  api_token: string;
  account_id: string;
  worker_name: string;
  proxy_secret?: string;
  image_edge_secret?: string;
  enable_business: boolean;
  register: boolean;
};

type DeployResponse = {
  ok: true;
  deployed?: boolean;
  kind?: string;
  worker_name?: string;
  worker_host?: string;
  base_url?: string;
  secrets_set?: string[];
  registered?: boolean;
  business_enabled?: boolean;
  ready?: boolean;
  message?: string;
  runtime_base_urls?: string[];
  cutover_hint?: string;
  secret_generated?: boolean;
  generated_secret?: string;
  generated_secret_note?: string;
  request_id: string;
};

type ProbeResponse = {
  ok: true;
  api?: { summary?: { total?: number; ok?: number; fail?: number } };
  image?: { summary?: { total?: number; ok?: number; fail?: number } };
  request_id: string;
};

const USAGE_STEPS = [
  "创建具有 Workers 编辑权限的 CF API 令牌",
  "填写账户 ID 与 Worker 名称",
  "选择类型：API 出口 或 出图边缘，点击部署",
  "成功后自动加入本系统对应出口池，无需手写 BASE_URLS",
  "补全/Token 走 api-worker；用户出图走 img-worker 反代 i.pximg.net，而非住宅",
  "多 Worker 名 = 多出口节点；注意免费额",
];

function formatBaseCooldown(rows: BaseCooldownRow[] | undefined): string {
  const list = Array.isArray(rows) ? rows : [];
  const cooling = list.filter((r) => Boolean(r.cooling));
  if (cooling.length === 0) {
    return list.length > 0 ? "全部健康（无冷却）" : "（无成员）";
  }
  return cooling
    .map((r) => {
      const host = String(r.base_url || "").replace(/^https?:\/\//, "");
      const rem = typeof r.cool_remaining_s === "number" ? r.cool_remaining_s.toFixed(0) : "?";
      const streak = r.fail_streak ?? 0;
      return `${host}: 冷却 ${rem}s / streak ${streak}`;
    })
    .join(" · ");
}

export function CfWorkerPage() {
  const queryClient = useQueryClient();
  const alerts = useActionAlerts();
  const [form] = Form.useForm<DeployFormValues>();
  const kind = Form.useWatch("kind", form) as "api" | "image" | undefined;

  const poolQuery = useQuery({
    queryKey: ["admin", "cf-workers", "pool"],
    queryFn: () => apiJson<CfWorkersPoolResponse>("/admin/api/cf-workers/pool"),
    staleTime: 10_000,
    refetchOnWindowFocus: false,
  });

  const deploy = useMutation({
    mutationFn: (values: DeployFormValues) =>
      apiJson<DeployResponse>("/admin/api/cf-workers/deploy", {
        method: "POST",
        body: JSON.stringify({
          kind: values.kind,
          api_token: values.api_token,
          account_id: values.account_id,
          worker_name: values.worker_name,
          register: values.register !== false,
          enable_business: values.enable_business !== false,
          proxy_secret: values.kind === "api" ? values.proxy_secret || undefined : undefined,
          image_edge_secret:
            values.kind === "image" ? values.image_edge_secret || undefined : undefined,
        }),
      }),
    onSuccess: (body) => {
      let msg =
        body.message ||
        `已部署 ${body.worker_host || body.base_url || ""}` +
          (body.business_enabled ? "（业务已启用）" : "（未启用业务）");
      if (body.secret_generated && body.generated_secret) {
        msg += ` · 已生成密钥（请立即保存，仅回显一次）：${body.generated_secret}`;
      }
      alerts.setSuccess(msg, body.request_id);
      // Clear token after success — never leave CF API token in form state longer than needed.
      form.setFieldsValue({ api_token: "" });
      void queryClient.invalidateQueries({ queryKey: ["admin", "cf-workers"] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "maintenance"] });
    },
    onError: (err) => {
      alerts.setErrorMessage(messageFromError(err) || "部署失败", requestIdFromError(err));
    },
  });

  const probe = useMutation({
    mutationFn: () =>
      apiJson<ProbeResponse>("/admin/api/cf-workers/probe", {
        method: "POST",
        body: JSON.stringify({ kind: "all" }),
      }),
    onSuccess: (body) => {
      const apiOk = body.api?.summary?.ok ?? 0;
      const apiTotal = body.api?.summary?.total ?? 0;
      const imgOk = body.image?.summary?.ok ?? 0;
      const imgTotal = body.image?.summary?.total ?? 0;
      alerts.setSuccess(`探针完成：API ${apiOk}/${apiTotal} · Image ${imgOk}/${imgTotal}`, body.request_id);
      void queryClient.invalidateQueries({ queryKey: ["admin", "cf-workers", "pool"] });
    },
    onError: (err) => {
      alerts.setErrorMessage(messageFromError(err) || "探针失败", requestIdFromError(err));
    },
  });

  const setForceResidential = useMutation({
    mutationFn: (enabled: boolean) =>
      apiJson<{ ok: true; force_residential_emergency?: boolean; request_id: string }>(
        "/admin/api/cf-workers/egress-policy",
        {
          method: "POST",
          body: JSON.stringify({ force_residential_emergency: enabled }),
        },
      ),
    onMutate: () => {
      alerts.clear();
    },
    onSuccess: (data, enabled) => {
      alerts.setSuccess(
        enabled
          ? "已开启进程内强制住宅应急（不持久；非公开出图主路径）"
          : "已关闭进程内强制住宅应急",
        data.request_id,
      );
      void queryClient.invalidateQueries({ queryKey: ["admin", "cf-workers", "pool"] });
    },
    onError: (err) => {
      alerts.setErrorMessage(messageFromError(err) || "更新出口策略失败", requestIdFromError(err));
    },
  });

  const pool = poolQuery.data;
  const policy = pool?.egress_policy;

  return (
    <div style={{ maxWidth: 880 }}>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        CF Worker
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        一页部署本仓硬化 Cloudflare Worker：API 出口（hydrate/Token）与出图边缘（img-worker →{" "}
        <Typography.Text code>i.pximg.net</Typography.Text>
        ）。成功后默认自动入池并启用业务语义；CF API Token 不落库。
      </Typography.Paragraph>

      <ActionAlerts
        message={alerts.message}
        requestId={alerts.requestId}
        errorMessage={alerts.errorMessage}
        errorRequestId={alerts.errorRequestId}
      />

      <Card title="使用说明" style={{ marginBottom: 16 }}>
        <ol style={{ margin: 0, paddingLeft: 20 }}>
          {USAGE_STEPS.map((step) => (
            <li key={step} style={{ marginBottom: 6 }}>
              {step}
            </li>
          ))}
        </ol>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0, marginTop: 12 }}>
          高级不在本页：R2 / Engine / Ports 见维护工具折叠；本地应急镜像见系统设置（非公开出图主路径）。
        </Typography.Paragraph>
      </Card>

      <Card title="部署 Worker" style={{ marginBottom: 16 }}>
        <Form<DeployFormValues>
          form={form}
          layout="vertical"
          initialValues={{
            kind: "image",
            enable_business: true,
            register: true,
            api_token: "",
            account_id: "",
            worker_name: "",
          }}
          onFinish={(values) => deploy.mutate(values)}
        >
          <Form.Item name="kind" label="类型" rules={[{ required: true, message: "选择类型" }]}>
            <Select
              options={[
                { value: "image", label: "出图边缘 (img-worker → i.pximg.net)" },
                { value: "api", label: "API 出口 (hydrate / Token)" },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="api_token"
            label="Cloudflare API 令牌"
            rules={[{ required: true, message: "填写 CF API Token" }]}
            extra="password 输入，请求后清空；永不写入数据库"
          >
            <Input.Password autoComplete="off" placeholder="CF API Token（Workers 编辑权限）" />
          </Form.Item>
          <Form.Item
            name="account_id"
            label="账户 ID"
            rules={[{ required: true, message: "填写 Account ID" }]}
          >
            <Input placeholder="Cloudflare Account ID（hex）" allowClear />
          </Form.Item>
          <Form.Item
            name="worker_name"
            label="Worker 名称"
            rules={[{ required: true, message: "填写 Worker 名" }]}
            extra="小写字母/数字/连字符；多名称 = 多出口节点"
          >
            <Input placeholder="ria-img-a" allowClear />
          </Form.Item>
          {kind === "api" ? (
            <Form.Item
              name="proxy_secret"
              label="PROXY_SECRET（可选）"
              extra="留空：复用 env/runtime → 仍无则自动生成（响应回显一次）"
            >
              <Input.Password autoComplete="off" placeholder="与 BFF 共享的 X-Proxy-Secret" />
            </Form.Item>
          ) : (
            <Form.Item
              name="image_edge_secret"
              label="IMAGE_EDGE_SECRET（可选）"
              extra="留空：复用 env/runtime → 仍无则自动生成（响应回显一次）"
            >
              <Input.Password autoComplete="off" placeholder="与 BFF 共享的 HMAC 密钥" />
            </Form.Item>
          )}
          <Form.Item name="enable_business" label="部署后启用业务" valuePropName="checked">
            <Switch checkedChildren="启用" unCheckedChildren="仅部署" />
          </Form.Item>
          <Form.Item name="register" label="注册进出口池" valuePropName="checked" hidden>
            <Switch />
          </Form.Item>
          <Space wrap>
            <Button type="primary" htmlType="submit" loading={deploy.isPending}>
              部署 Worker
            </Button>
            <Button onClick={() => probe.mutate()} loading={probe.isPending}>
              检查状态（探针 healthz）
            </Button>
            <Button onClick={() => void poolQuery.refetch()} loading={poolQuery.isFetching}>
              刷新池
            </Button>
          </Space>
        </Form>
      </Card>

      <Card title="已部署 / 池状态" style={{ marginBottom: 16 }}>
        <QueryState query={poolQuery}>
          {pool ? (
            <Descriptions size="small" column={1} bordered>
              <Descriptions.Item label="API 池">
                <Space wrap>
                  {(pool.api?.runtime_enabled || pool.api?.env_enabled) ? (
                    <Tag color="blue">enabled</Tag>
                  ) : (
                    <Tag>off</Tag>
                  )}
                  {policy?.cf_api_proxy_ready ? (
                    <Tag color="green">ready</Tag>
                  ) : (
                    <Tag color="orange">not ready</Tag>
                  )}
                  <Typography.Text type="secondary">
                    {(pool.api?.merged_base_urls || []).join(", ") || "（空）"}
                  </Typography.Text>
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="出图池">
                <Space wrap>
                  {(pool.image?.runtime_enabled || pool.image?.env_enabled) ? (
                    <Tag color="blue">enabled</Tag>
                  ) : (
                    <Tag>off</Tag>
                  )}
                  {policy?.image_edge_ready ? (
                    <Tag color="green">ready</Tag>
                  ) : (
                    <Tag color="orange">not ready</Tag>
                  )}
                  <Typography.Text type="secondary">
                    {(pool.image?.merged_base_urls || []).join(", ") || "（空）"}
                  </Typography.Text>
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="住宅策略">
                <Space wrap>
                  {policy?.residential_egress_emergency_only === false ? (
                    <Tag color="orange">emergency-only OFF</Tag>
                  ) : (
                    <Tag color="green">emergency-only ON</Tag>
                  )}
                  {policy?.force_residential_emergency ? (
                    <Tag color="red">强制住宅 ON</Tag>
                  ) : (
                    <Tag>强制住宅 off</Tag>
                  )}
                  <Switch
                    checkedChildren="应急开"
                    unCheckedChildren="应急关"
                    checked={Boolean(policy?.force_residential_emergency)}
                    loading={setForceResidential.isPending}
                    onChange={(checked) => {
                      if (checked) {
                        const ok = window.confirm(
                          "开启进程内强制住宅应急？仅影响本 BFF 进程、不持久化，且不是公开出图主路径。",
                        );
                        if (!ok) return;
                      }
                      setForceResidential.mutate(checked);
                    }}
                  />
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="API base 冷却">
                <Cooldown.Text type="secondary" style={{ fontSize: 12 }}>
                  {formatBaseCooldown(pool.api?.base_cooldown)}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="出图 base 冷却">
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {formatBaseCooldown(pool.image?.base_cooldown)}
                </Typography.Text>
              </Descriptions.Item>
            </Descriptions>
          ) : null}
        </QueryState>
        <Alert
          style={{ marginTop: 12 }}
          type="info"
          showIcon
          message="主路径说明"
          description="用户出图：签名 URL → img-worker → i.pximg.net。后台补全/Token：api-worker allowlist。住宅/EasyProxies 仅应急；上表「进程强制住宅」为一键应急开关（进程本地、不改 env）。base 冷却为进程本地指数退避（30s×2^(streak-1)，上限 300s），多副本不共享。"
        />
      </Card>

      <Typography.Paragraph type="secondary">
        维护工具中的池注册/探针/Engine/R2 为高级排障入口。日常切流请优先使用本页。
      </Typography.Paragraph>
    </div>
  );
}
