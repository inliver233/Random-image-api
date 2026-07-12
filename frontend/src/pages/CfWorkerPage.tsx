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
  "填写账户 ID 与 Worker 名称（出图建议 -img 后缀、API 建议 -api；image/api 禁止同名以免互相覆盖）",
  "选择类型：出图边缘（默认启用业务）或 API 出口（默认仅入池、不启用业务），点击部署",
  "成功后自动加入本系统对应出口池，无需手写 BASE_URLS",
  "公开出图默认本域 200，字节上游经自建 img-worker 反代 i.pximg.net（redirect=1 才 302）；Pixiv OAuth/hydrate 默认不依赖 CF API 反代，需要时再勾选「部署后启用业务」",
  "多 Worker 名 = 多出口节点；「注销」仅摘本系统池、不需 Token；「删除脚本」走 CF API 硬删 Worker，须重填 API Token + 账户 ID（Token 不落库）",
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

/** Normalize Worker script name for same-name checks (CF names are case-insensitive). */
function normalizeWorkerName(name: string): string {
  return String(name || "")
    .trim()
    .toLowerCase()
    .replace(/\.workers\.dev$/i, "");
}

function workerNameFromHost(host: string): string {
  const h = String(host || "").trim();
  const noScheme = h.replace(/^https?:\/\//i, "");
  const first = noScheme.split("/")[0] || noScheme;
  const dot = first.indexOf(".");
  return dot > 0 ? first.slice(0, dot) : first;
}

/** Collect registered/env Worker names for the opposite kind (cross-kind overwrite guard). */
function collectWorkerNames(side: PoolSide | undefined): Set<string> {
  const out = new Set<string>();
  for (const raw of side?.merged_base_urls || []) {
    const host = String(raw || "")
      .replace(/^https?:\/\//i, "")
      .replace(/\/$/, "");
    const n = normalizeWorkerName(workerNameFromHost(host));
    if (n) out.add(n);
  }
  for (const m of side?.members || []) {
    const host = String(m.base_url || "")
      .replace(/^https?:\/\//i, "")
      .replace(/\/$/, "");
    const n = normalizeWorkerName(workerNameFromHost(host));
    if (n) out.add(n);
  }
  return out;
}

export function CfWorkerPage() {
  const queryClient = useQueryClient();
  const alerts = useActionAlerts();
  const [form] = Form.useForm<DeployFormValues>();
  const kind = Form.useWatch("kind", form) as "api" | "image" | undefined;
  const watchedApiToken = Form.useWatch("api_token", form);
  const watchedAccountId = Form.useWatch("account_id", form);
  const hasDeleteCredentials =
    Boolean(String(watchedApiToken || "").trim()) && Boolean(String(watchedAccountId || "").trim());

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
          enable_business: Boolean(values.enable_business),
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

  const unregister = useMutation({
    mutationFn: (payload: { kind: "api" | "image"; base_url: string }) =>
      apiJson<{ ok: true; unregistered?: boolean; request_id: string }>(
        "/admin/api/cf-workers/unregister",
        {
          method: "POST",
          body: JSON.stringify(payload),
        },
      ),
    onMutate: () => {
      alerts.clear();
    },
    onSuccess: (body, vars) => {
      const host = String(vars.base_url || "").replace(/^https?:\/\//, "");
      alerts.setSuccess(
        `已从 ${vars.kind === "api" ? "API" : "出图"} 运行时池移除 ${host}（env 成员需改环境变量）`,
        body.request_id,
      );
      void queryClient.invalidateQueries({ queryKey: ["admin", "cf-workers", "pool"] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "maintenance"] });
    },
    onError: (err) => {
      alerts.setErrorMessage(messageFromError(err) || "注销失败", requestIdFromError(err));
    },
  });

  const deleteScript = useMutation({
    mutationFn: (payload: {
      kind: "api" | "image";
      base_url: string;
      worker_name: string;
      api_token: string;
      account_id: string;
    }) =>
      apiJson<{
        ok: true;
        deleted?: boolean;
        already_absent?: boolean;
        unregistered?: boolean;
        request_id: string;
      }>("/admin/api/cf-workers/delete-script", {
        method: "POST",
        body: JSON.stringify({
          kind: payload.kind,
          base_url: payload.base_url,
          worker_name: payload.worker_name,
          api_token: payload.api_token,
          account_id: payload.account_id,
          unregister_pool: true,
        }),
      }),
    onMutate: () => {
      alerts.clear();
    },
    onSuccess: (body, vars) => {
      const host = String(vars.base_url || "").replace(/^https?:\/\//, "");
      const status = body.deleted
        ? "已删除 CF 脚本"
        : body.already_absent
          ? "CF 上脚本已不存在"
          : "删除请求已完成";
      alerts.setSuccess(
        `${status}：${host}` + (body.unregistered ? "；已从运行时池注销" : ""),
        body.request_id,
      );
      form.setFieldsValue({ api_token: "" });
      void queryClient.invalidateQueries({ queryKey: ["admin", "cf-workers", "pool"] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "maintenance"] });
    },
    onError: (err) => {
      alerts.setErrorMessage(messageFromError(err) || "删除脚本失败", requestIdFromError(err));
    },
  });

  const pool = poolQuery.data;
  const policy = pool?.egress_policy;
  const otherKindNames = React.useMemo(() => {
    if (kind === "image") return collectWorkerNames(pool?.api);
    if (kind === "api") return collectWorkerNames(pool?.image);
    return new Set<string>();
  }, [kind, pool?.api, pool?.image]);

  const renderPoolBases = (side: PoolSide | undefined, sideKind: "api" | "image") => {
    const envSet = new Set((side?.env_base_urls || []).map((u) => String(u).replace(/\/$/, "")));
    const merged = side?.merged_base_urls || [];
    if (merged.length === 0) {
      return <Typography.Text type="secondary">（空）</Typography.Text>;
    }
    return (
      <Space direction="vertical" size={4} style={{ width: "100%" }}>
        {merged.map((raw) => {
          const base = String(raw || "").replace(/\/$/, "");
          const fromEnv = envSet.has(base);
          const host = base.replace(/^https?:\/\//, "");
          const workerName = workerNameFromHost(host);
          return (
            <Space key={`${sideKind}:${base}`} wrap size={8} align="start">
              <Typography.Text code style={{ fontSize: 12 }}>
                {host}
              </Typography.Text>
              <Tag>{fromEnv ? "env" : "runtime"}</Tag>
              {!fromEnv ? (
                <Space direction="vertical" size={2}>
                  <Space wrap size={8}>
                    <Button
                      size="small"
                      danger
                      loading={
                        unregister.isPending &&
                        unregister.variables?.kind === sideKind &&
                        unregister.variables?.base_url === base
                      }
                      onClick={() => {
                        const ok = window.confirm(
                          `从运行时池注销 ${host}？\n不会删除 Cloudflare 上的 Worker 脚本；仅从本系统出口池移除（无需 CF Token）。`,
                        );
                        if (!ok) return;
                        unregister.mutate({ kind: sideKind, base_url: base });
                      }}
                    >
                      注销（仅摘池）
                    </Button>
                    <Button
                      size="small"
                      danger
                      type="dashed"
                      disabled={!hasDeleteCredentials}
                      title={
                        hasDeleteCredentials
                          ? "从 Cloudflare 删除脚本并尝试从本系统池注销"
                          : "须先在上方表单重填 API Token 与账户 ID（部署成功后 Token 已清空，不落库）"
                      }
                      loading={
                        deleteScript.isPending &&
                        deleteScript.variables?.kind === sideKind &&
                        deleteScript.variables?.base_url === base
                      }
                      onClick={() => {
                        const values = form.getFieldsValue();
                        const apiToken = String(values.api_token || "").trim();
                        const accountId = String(values.account_id || "").trim();
                        if (!apiToken || !accountId) {
                          alerts.setErrorMessage(
                            "删除脚本失败：须在上方表单重填 Cloudflare API Token 与账户 ID（Token 部署后会清空且永不落库）。「注销」仅摘池、不需要 Token。",
                          );
                          return;
                        }
                        const ok = window.confirm(
                          `从 Cloudflare 硬删 Worker 脚本「${workerName}」并尝试从本系统池注销 ${host}？\n此操作不可恢复（CF 侧脚本删除）。\n注意：这与「注销（仅摘池）」不同。`,
                        );
                        if (!ok) return;
                        deleteScript.mutate({
                          kind: sideKind,
                          base_url: base,
                          worker_name: workerName,
                          api_token: apiToken,
                          account_id: accountId,
                        });
                      }}
                    >
                      删除脚本
                    </Button>
                  </Space>
                  {!hasDeleteCredentials ? (
                    <Typography.Text type="danger" style={{ fontSize: 12 }}>
                      删除脚本须重填上方 API Token + 账户 ID（与「注销」不同，注销不需 Token）
                    </Typography.Text>
                  ) : null}
                </Space>
              ) : (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  env 成员请改 IMAGE_EDGE / CF_API_PROXY 环境变量
                </Typography.Text>
              )}
            </Space>
          );
        })}
      </Space>
    );
  };

  return (
    <div style={{ maxWidth: 880 }}>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        CF Worker
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        一页部署本仓硬化 Cloudflare Worker：出图边缘（img-worker →{" "}
        <Typography.Text code>i.pximg.net</Typography.Text>
        ，公开出图默认启用）与 API 出口（hydrate/Token，默认仅入池、不启用业务）。CF API Token 不落库。
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
          高级不在本页：R2 / Engine / Ports 见维护工具折叠；第三方图片镜像（i.pixiv.cat 等，非自建 CF）见系统设置（仅本地 /i 回退，非公开出图主路径）。
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
                { value: "api", label: "API 出口 (hydrate / Token，可选)" },
              ]}
              onChange={(value) => {
                // Product default: image on, api off (ops can still flip the switch).
                form.setFieldsValue({ enable_business: value === "image" });
              }}
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
            rules={[
              { required: true, message: "填写 Worker 名" },
              {
                validator: async (_, value) => {
                  const n = normalizeWorkerName(String(value || ""));
                  if (!n) return;
                  if (otherKindNames.has(n)) {
                    const other = kind === "image" ? "API" : "出图";
                    throw new Error(
                      `与已注册的${other} Worker 同名，会互相覆盖（曾导致 /u Forbidden）。请改用不同名称（建议出图 *-img、API *-api）。`,
                    );
                  }
                },
              },
            ]}
            extra="小写字母/数字/连字符；多名称 = 多出口节点。image/api 禁止同名。"
          >
            <Input
              placeholder={kind === "api" ? "ria-api-a" : "ria-img-a"}
              allowClear
            />
          </Form.Item>
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            message="禁止 image / API 使用相同 Worker 名称"
            description="同名会互相覆盖脚本：出图路径可能返回 API 的 Forbidden。默认占位已区分 -img / -api；部署前请确认与另一池成员不同名。"
          />
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
          <Form.Item
            name="enable_business"
            label="部署后启用业务"
            valuePropName="checked"
            extra={
              kind === "api"
                ? "API 默认关闭：OAuth/hydrate 不依赖 CF API 反代；需要时再打开"
                : "出图默认开启：公开 /random 与入库读图走自建 Image Edge"
            }
          >
            <Switch checkedChildren="启用" unCheckedChildren="仅入池" />
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
                <Space direction="vertical" size={8} style={{ width: "100%" }}>
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
                  </Space>
                  {renderPoolBases(pool.api, "api")}
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="出图池">
                <Space direction="vertical" size={8} style={{ width: "100%" }}>
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
                  </Space>
                  {renderPoolBases(pool.image, "image")}
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
                  {policy?.force_residential_emergency ? (
                    <Button
                      size="small"
                      type="primary"
                      loading={setForceResidential.isPending}
                      onClick={() => setForceResidential.mutate(false)}
                    >
                      确认默认 CF（关应急）
                    </Button>
                  ) : null}
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="API base 冷却">
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
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
          description="用户出图：默认本域 200（地址栏保持 /random，F5 换图），字节上游经自建 img-worker → i.pximg.net；仅 redirect=1 才 302 到 workers.dev 签名 URL。后台补全/Token：api-worker allowlist。住宅/EasyProxies 仅应急；「进程强制住宅」为一键应急（进程本地）。base 冷却为进程本地指数退避，多副本不共享。"
        />
      </Card>

      <Typography.Paragraph type="secondary">
        维护工具中的池注册/探针/Engine/R2 为高级排障入口。日常切流请优先使用本页。
      </Typography.Paragraph>
    </div>
  );
}
