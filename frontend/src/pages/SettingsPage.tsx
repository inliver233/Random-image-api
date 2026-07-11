import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Form, InputNumber, Select, Space, Switch, Typography } from "antd";
import React, { useEffect } from "react";

import { ActionAlerts } from "../admin/ActionAlerts";
import { PendingAlert } from "../admin/PendingAlert";
import { QueryState } from "../admin/QueryState";
import { asBool, asInt, asObject } from "../admin/softCoerce";
import { useActionAlerts } from "../admin/useActionAlerts";
import { apiJson } from "../api/client";

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

type SettingsResponse = {
  ok: true;
  settings: Record<string, unknown>;
  request_id: string;
};

type SettingsUpdateResponse = {
  ok: true;
  updated: number;
  request_id: string;
};

type SettingsFormValues = {
  proxy_enabled: boolean;
  proxy_fail_closed: boolean;
  proxy_route_mode: "pixiv_only" | "all" | "allowlist" | "off";
  proxy_allowlist_domains: string[];
  proxy_default_pool_id: number;
  image_proxy_use_pixiv_cat: boolean;
  image_proxy_pximg_mirror_host: "i.pixiv.cat" | "i.pixiv.re" | "i.pixiv.nl";
  image_proxy_extra_pximg_mirror_hosts: string[];
  random_default_attempts: number;
  random_default_r18_strict: boolean;
  random_fail_cooldown_ms: number;
  security_hide_origin_url_in_public_json: boolean;
  pixiv_hydrate_min_interval_ms: number;
  pixiv_hydrate_jitter_ms: number;
};

function asStrList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const out: string[] = [];
  const seen = new Set<string>();
  for (const item of value) {
    if (typeof item !== "string") continue;
    const trimmed = item.trim();
    if (!trimmed || trimmed.length > 200 || seen.has(trimmed)) continue;
    seen.add(trimmed);
    out.push(trimmed);
  }
  return out;
}

function asPximgMirrorHost(value: unknown, fallback: SettingsFormValues["image_proxy_pximg_mirror_host"]): SettingsFormValues["image_proxy_pximg_mirror_host"] {
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (normalized === "i.pixiv.cat" || normalized === "i.pixiv.re" || normalized === "i.pixiv.nl") return normalized;
  }
  return fallback;
}

export function SettingsPage() {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<SettingsFormValues>();
  const query = useQuery({
    queryKey: ["admin", "settings"],
    queryFn: () => apiJson<SettingsResponse>("/admin/api/settings"),
  });

  const poolsQuery = useQuery({
    queryKey: ["admin", "proxy-pools"],
    queryFn: () => apiJson<ProxyPoolsListResponse>("/admin/api/proxy-pools"),
  });

  const alerts = useActionAlerts();

  useEffect(() => {
    if (!query.data) return;
    const settings = asObject(query.data.settings);
    const proxy = asObject(settings.proxy);
    const imageProxy = asObject(settings.image_proxy);
    const random = asObject(settings.random);
    const security = asObject(settings.security);
    const rateLimit = asObject(settings.rate_limit);

    const routeModeRaw = String(proxy.route_mode || "pixiv_only").trim().toLowerCase();
    const routeMode: SettingsFormValues["proxy_route_mode"] =
      routeModeRaw === "all" || routeModeRaw === "allowlist" || routeModeRaw === "off" ? routeModeRaw : "pixiv_only";

    form.setFieldsValue({
      proxy_enabled: asBool(proxy.enabled, false),
      proxy_fail_closed: asBool(proxy.fail_closed, false),
      proxy_route_mode: routeMode,
      proxy_allowlist_domains: asStrList(proxy.allowlist_domains),
      proxy_default_pool_id: asInt(proxy.default_pool_id, 0),
      image_proxy_use_pixiv_cat: asBool(imageProxy.use_pixiv_cat, false),
      image_proxy_pximg_mirror_host: asPximgMirrorHost(imageProxy.pximg_mirror_host, "i.pixiv.cat"),
      image_proxy_extra_pximg_mirror_hosts: asStrList(imageProxy.extra_pximg_mirror_hosts),
      random_default_attempts: asInt(random.default_attempts, 3),
      random_default_r18_strict: asBool(random.default_r18_strict, true),
      random_fail_cooldown_ms: asInt(random.fail_cooldown_ms, 600_000),
      security_hide_origin_url_in_public_json: asBool(security.hide_origin_url_in_public_json, true),
      pixiv_hydrate_min_interval_ms: asInt(rateLimit.pixiv_hydrate_min_interval_ms, 800),
      pixiv_hydrate_jitter_ms: asInt(rateLimit.pixiv_hydrate_jitter_ms, 200),
    });
  }, [form, query.data]);

  const save = useMutation({
    mutationFn: (values: SettingsFormValues) =>
      apiJson<SettingsUpdateResponse>("/admin/api/settings", {
        method: "PUT",
        body: JSON.stringify({
          settings: {
            proxy: {
              enabled: values.proxy_enabled,
              fail_closed: values.proxy_fail_closed,
              route_mode: values.proxy_route_mode,
              allowlist_domains: values.proxy_allowlist_domains,
              default_pool_id: values.proxy_default_pool_id > 0 ? values.proxy_default_pool_id : "",
            },
            image_proxy: {
              use_pixiv_cat: values.image_proxy_use_pixiv_cat,
              pximg_mirror_host: values.image_proxy_pximg_mirror_host,
              extra_pximg_mirror_hosts: values.image_proxy_extra_pximg_mirror_hosts,
            },
            random: {
              default_attempts: values.random_default_attempts,
              default_r18_strict: values.random_default_r18_strict,
              fail_cooldown_ms: values.random_fail_cooldown_ms,
            },
            security: { hide_origin_url_in_public_json: values.security_hide_origin_url_in_public_json },
            rate_limit: {
              pixiv_hydrate_min_interval_ms: Math.max(0, Math.trunc(values.pixiv_hydrate_min_interval_ms || 0)),
              pixiv_hydrate_jitter_ms: Math.max(0, Math.trunc(values.pixiv_hydrate_jitter_ms || 0)),
            },
          },
        }),
      }),
    onMutate: () => {
      alerts.clear();
    },
    onSuccess: (data) => {
      alerts.setSuccess(`保存成功（更新条目数: ${data.updated}）`, data.request_id);
      queryClient.invalidateQueries({ queryKey: ["admin", "settings"] });
    },
    onError: (err) => {
      alerts.setError(err, "保存失败");
    },
  });

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        系统设置
      </Typography.Title>

      <Space wrap>
        <Button type="primary" onClick={() => form.submit()} loading={save.isPending} disabled={!query.data || query.isError || query.isLoading}>
          保存设置
        </Button>
      </Space>

      <PendingAlert pending={save.isPending} message="正在保存设置..." />
      <ActionAlerts
        message={alerts.message}
        requestId={alerts.requestId}
        errorMessage={alerts.errorMessage}
        errorRequestId={alerts.errorRequestId}
      />

      <QueryState query={query} errorMessage="加载设置失败">
        {query.data ? (
          <Card>
            <Typography.Text type="secondary">请求ID: {query.data.request_id}</Typography.Text>
            <Form form={form} layout="vertical" onFinish={(values) => save.mutate(values)}>
              <Typography.Title level={5} style={{ marginTop: 12 }}>
                代理设置（仅 Hydrate / OAuth）
              </Typography.Title>
              <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
                住宅代理池只服务 Pixiv App API 补全与 OAuth。公开出图走 Image Edge（或本地 /i
                回退），不经过本页代理路由。
              </Typography.Paragraph>

              <Form.Item label="启用代理" name="proxy_enabled" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item label="失败即拦截（严格模式）" name="proxy_fail_closed" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item label="代理路由模式" name="proxy_route_mode">
                <Select
                  options={[
                    { value: "pixiv_only", label: "仅 Pixiv" },
                    { value: "all", label: "全部流量" },
                    { value: "allowlist", label: "仅白名单域名" },
                    { value: "off", label: "关闭" },
                  ]}
                  style={{ maxWidth: 320 }}
                />
              </Form.Item>
              <Form.Item label="白名单域名" name="proxy_allowlist_domains">
                <Select mode="tags" style={{ maxWidth: 520 }} tokenSeparators={[",", "\n", " "]} placeholder="例如：example.com api.example.com" />
              </Form.Item>
              <Form.Item
                label="默认代理池"
                name="proxy_default_pool_id"
                extra={poolsQuery.isError ? "代理池列表加载失败：可先到“代理池”页面创建。" : "不指定时会自动选择第一个启用的代理池。"}
              >
                <Select
                  style={{ maxWidth: 420 }}
                  loading={poolsQuery.isLoading}
                  options={[
                    { value: 0, label: "不指定（自动选择）" },
                    ...(poolsQuery.data?.items || [])
                      .filter((p) => Boolean(p.enabled))
                      .map((p) => ({ value: Number(p.id), label: `${p.name}(#${p.id})` })),
                  ]}
                />
              </Form.Item>

              <Typography.Title level={5} style={{ marginTop: 12 }}>
                图片加速（本地 /i 回退）
              </Typography.Title>
              <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
                生产主路径是 Image Edge（环境变量 IMAGE_EDGE_*，维护页可看 ready 状态）。以下镜像开关仅影响源站本地反代回退，不是公开出图的一等路径。
              </Typography.Paragraph>
              <Form.Item
                label="使用第三方反向代理（仅图片上游）"
                name="image_proxy_use_pixiv_cat"
                valuePropName="checked"
                extra="开启后：服务端拉取图片时会把 i.pximg.net 替换为 i.pixiv.*（客户端仍访问本站域名，不会暴露第三方域名）。会按访问地区智能选择上游：大陆优先 i.pixiv.re，非大陆默认 i.pixiv.cat。"
              >
                <Switch />
              </Form.Item>
              <Form.Item
                label="镜像域名"
                name="image_proxy_pximg_mirror_host"
                extra="可选 i.pixiv.cat / i.pixiv.re / i.pixiv.nl。未显式指定 pximg_mirror_host 时：大陆访问会自动用 i.pixiv.re；非大陆使用这里选择的镜像（默认 i.pixiv.cat）。"
              >
                <Select
                  style={{ maxWidth: 360 }}
                  options={[
                    { value: "i.pixiv.cat", label: "i.pixiv.cat（默认）" },
                    { value: "i.pixiv.re", label: "i.pixiv.re（大陆优先）" },
                    { value: "i.pixiv.nl", label: "i.pixiv.nl（备用）" },
                  ]}
                />
              </Form.Item>
              <Form.Item
                label="自定义镜像白名单"
                name="image_proxy_extra_pximg_mirror_hosts"
                extra="用于公开接口的 proxy= 参数：仅允许这里配置的自定义域名被用作图片上游镜像（防止 SSRF）。示例：i.mirror.example.com"
              >
                <Select mode="tags" style={{ maxWidth: 520 }} tokenSeparators={[",", "\n", " "]} placeholder="例如：i.mirror.example.com" />
              </Form.Item>

              <Typography.Title level={5} style={{ marginTop: 12 }}>
                随机接口设置
              </Typography.Title>
              <Form.Item
                label="默认尝试次数"
                name="random_default_attempts"
                extra="与公开接口 runtime 校验一致：1–10。"
              >
                <InputNumber min={1} max={10} style={{ width: 200 }} />
              </Form.Item>
              <Form.Item label="默认严格 R18 过滤" name="random_default_r18_strict" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item label="失败冷却时间（毫秒）" name="random_fail_cooldown_ms">
                <InputNumber min={0} max={10_000_000} style={{ width: 240 }} />
              </Form.Item>
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 16 }}
                message="策略 / 质量抽样 / 去重"
                description="默认随机策略、quality_samples、推荐打分与去重参数请在「推荐策略」页面编辑，避免与系统设置重复维护。"
              />

              <Typography.Title level={5} style={{ marginTop: 12 }}>
                安全设置
              </Typography.Title>
              <Form.Item label="在公开 JSON 中隐藏原图 URL" name="security_hide_origin_url_in_public_json" valuePropName="checked">
                <Switch />
              </Form.Item>

              <Typography.Title level={5} style={{ marginTop: 12 }}>
                补全任务设置
              </Typography.Title>
              <Form.Item
                label="Pixiv 请求最小间隔（毫秒）"
                name="pixiv_hydrate_min_interval_ms"
                extra="每次补全任务请求 Pixiv（OAuth/作品详情）前至少等待该间隔。建议 300~2000。"
              >
                <InputNumber min={0} max={60_000} style={{ width: 240 }} />
              </Form.Item>
              <Form.Item
                label="随机抖动（毫秒）"
                name="pixiv_hydrate_jitter_ms"
                extra="在最小间隔基础上增加 0~抖动 的随机等待，降低固定节奏触发风控的概率。"
              >
                <InputNumber min={0} max={60_000} style={{ width: 240 }} />
              </Form.Item>
            </Form>
          </Card>
        ) : null}
      </QueryState>
    </Space>
  );
}
