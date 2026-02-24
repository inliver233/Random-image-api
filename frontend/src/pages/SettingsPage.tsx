import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Form, InputNumber, Select, Skeleton, Space, Switch, Typography } from "antd";
import React, { useEffect, useState } from "react";

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
  random_default_attempts: number;
  random_default_r18_strict: boolean;
  random_fail_cooldown_ms: number;
  random_strategy: "quality" | "random";
  random_quality_samples: number;
  security_hide_origin_url_in_public_json: boolean;
  pixiv_hydrate_min_interval_ms: number;
  pixiv_hydrate_jitter_ms: number;
};

const RANDOM_STRATEGY_VALUES = new Set<SettingsFormValues["random_strategy"]>(["quality", "random"]);

function requestIdFromError(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  return err.body?.request_id ? String(err.body.request_id) : null;
}

function asObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function asBool(value: unknown, fallback: boolean): boolean {
  if (typeof value === "boolean") return value;
  if (typeof value === "number" && (value === 0 || value === 1)) return Boolean(value);
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (normalized === "true" || normalized === "1" || normalized === "yes" || normalized === "on") return true;
    if (normalized === "false" || normalized === "0" || normalized === "no" || normalized === "off") return false;
  }
  return fallback;
}

function asInt(value: unknown, fallback: number): number {
  if (typeof value === "number" && Number.isFinite(value)) return Math.trunc(value);
  if (typeof value === "string") {
    const parsed = Number.parseInt(value.trim(), 10);
    if (Number.isFinite(parsed)) return parsed;
  }
  return fallback;
}

function asLowerEnum<T extends string>(value: unknown, allowed: Set<T>, fallback: T): T {
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (allowed.has(normalized as T)) return normalized as T;
  }
  return fallback;
}

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

  const [saveErrorMessage, setSaveErrorMessage] = useState<string | null>(null);
  const [saveRequestId, setSaveRequestId] = useState<string | null>(null);
  const [saveUpdated, setSaveUpdated] = useState<number | null>(null);

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
      random_default_attempts: asInt(random.default_attempts, 3),
      random_default_r18_strict: asBool(random.default_r18_strict, true),
      random_fail_cooldown_ms: asInt(random.fail_cooldown_ms, 600_000),
      random_strategy: asLowerEnum(random.strategy, RANDOM_STRATEGY_VALUES, "quality"),
      random_quality_samples: asInt(random.quality_samples, 5),
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
            },
            random: {
              default_attempts: values.random_default_attempts,
              default_r18_strict: values.random_default_r18_strict,
              fail_cooldown_ms: values.random_fail_cooldown_ms,
              strategy: values.random_strategy,
              quality_samples: values.random_quality_samples,
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
      setSaveErrorMessage(null);
      setSaveRequestId(null);
      setSaveUpdated(null);
    },
    onSuccess: (data) => {
      setSaveUpdated(data.updated);
      setSaveRequestId(data.request_id);
      queryClient.invalidateQueries({ queryKey: ["admin", "settings"] });
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        setSaveErrorMessage(err.message);
        setSaveRequestId(requestIdFromError(err));
        return;
      }
      if (err instanceof Error) {
        setSaveErrorMessage(err.message);
        return;
      }
      setSaveErrorMessage("保存失败");
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

      {save.isPending ? <Alert type="info" showIcon message="正在保存设置..." /> : null}
      {saveErrorMessage ? <Alert type="error" showIcon message={saveErrorMessage} /> : null}
      {saveUpdated !== null ? <Alert type="success" showIcon message="保存成功" description={`更新条目数: ${saveUpdated}`} /> : null}
      {saveRequestId ? <Typography.Text type="secondary">请求ID: {saveRequestId}</Typography.Text> : null}

      {query.isLoading ? (
        <Skeleton active />
      ) : query.isError ? (
        <Alert
          type="error"
          showIcon
          message="加载设置失败"
          description={requestIdFromError(query.error) ? `请求ID: ${requestIdFromError(query.error)}` : ""}
        />
      ) : !query.data ? (
        <Skeleton active />
      ) : (
        <Card>
          <Typography.Text type="secondary">请求ID: {query.data.request_id}</Typography.Text>
          <Form form={form} layout="vertical" onFinish={(values) => save.mutate(values)}>
            <Typography.Title level={5} style={{ marginTop: 12 }}>
              代理设置
            </Typography.Title>

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
              图片加速
            </Typography.Title>
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

            <Typography.Title level={5} style={{ marginTop: 12 }}>
              随机接口设置
            </Typography.Title>
            <Form.Item label="默认尝试次数" name="random_default_attempts">
              <InputNumber min={0} max={1000} style={{ width: 200 }} />
            </Form.Item>
            <Form.Item label="默认严格 R18 过滤" name="random_default_r18_strict" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item label="失败冷却时间（毫秒）" name="random_fail_cooldown_ms">
              <InputNumber min={0} max={10_000_000} style={{ width: 240 }} />
            </Form.Item>
            <Form.Item label="默认随机策略" name="random_strategy">
              <Select
                options={[
                  { value: "quality", label: "质量优先（更偏向高收藏/高清）" },
                  { value: "random", label: "纯随机（random_key）" },
                ]}
                style={{ maxWidth: 360 }}
              />
            </Form.Item>
            <Form.Item label="默认质量抽样数量（quality）" name="random_quality_samples" extra="仅在“质量优先”策略下生效（建议 3~50；大图库可更高）。">
              <InputNumber min={1} max={1000} style={{ width: 240 }} />
            </Form.Item>

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
      )}
    </Space>
  );
}
