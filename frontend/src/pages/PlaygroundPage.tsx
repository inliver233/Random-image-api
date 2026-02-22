import { useMutation } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Form, Input, InputNumber, Row, Select, Skeleton, Space, Switch, Typography } from "antd";
import React, { useEffect } from "react";
import { useLocation } from "react-router-dom";

import { ApiError, type ApiErrorBody, apiFetch, apiJson, formatApiErrorMessage } from "../api/client";

type PlaygroundFormValues = {
  format: "image" | "json" | "redirect";
  attempts: number | null;
  seed: string;
  strategy: "default" | "quality" | "random";
  quality_samples: number | null;
  r18: 0 | 1 | 2;
  r18_strict: "default" | "1" | "0";
  adaptive: boolean;
  layout: "any" | "portrait" | "landscape" | "square";
  min_width: number | null;
  min_height: number | null;
  min_pixels: number | null;
  min_bookmarks: number | null;
  min_views: number | null;
  min_comments: number | null;
  included_tags: string;
  excluded_tags: string;
  user_id: number | null;
  illust_id: number | null;
  ai_type: "any" | "0" | "1";
  illust_type: "any" | "illust" | "manga" | "ugoira";
  created_from: string;
  created_to: string;
};

type RandomJsonResponse = {
  ok: true;
  request_id: string;
  data: { urls?: { proxy?: string } };
};

type PlaygroundResult =
  | { kind: "json"; url: string; request_id: string; payload: RandomJsonResponse }
  | { kind: "image"; url: string; request_id: string | null; src: string; headers: Record<string, string> }
  | { kind: "redirect"; url: string; request_id: string | null; location: string | null; note: string | null };

function requestIdFromError(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  return err.body?.request_id ? String(err.body.request_id) : null;
}

function buildRandomUrl(values: PlaygroundFormValues): string {
  const sp = new URLSearchParams();

  if (values.attempts != null) sp.set("attempts", String(values.attempts));
  if (values.seed.trim()) sp.set("seed", values.seed.trim());

  if (values.strategy !== "default") sp.set("strategy", values.strategy);
  if (values.strategy === "quality" && values.quality_samples != null) sp.set("quality_samples", String(values.quality_samples));

  sp.set("r18", String(values.r18));
  if (values.r18_strict !== "default") sp.set("r18_strict", values.r18_strict);
  if (values.adaptive) sp.set("adaptive", "1");
  if (values.layout !== "any") sp.set("layout", values.layout);

  if (values.min_width != null) sp.set("min_width", String(values.min_width));
  if (values.min_height != null) sp.set("min_height", String(values.min_height));
  if (values.min_pixels != null) sp.set("min_pixels", String(values.min_pixels));
  if (values.min_bookmarks != null) sp.set("min_bookmarks", String(values.min_bookmarks));
  if (values.min_views != null) sp.set("min_views", String(values.min_views));
  if (values.min_comments != null) sp.set("min_comments", String(values.min_comments));

  if (values.included_tags.trim()) sp.set("included_tags", values.included_tags.trim());
  if (values.excluded_tags.trim()) sp.set("excluded_tags", values.excluded_tags.trim());

  if (values.user_id != null) sp.set("user_id", String(values.user_id));
  if (values.illust_id != null) sp.set("illust_id", String(values.illust_id));

  if (values.ai_type !== "any") sp.set("ai_type", values.ai_type);
  if (values.illust_type !== "any") sp.set("illust_type", values.illust_type);
  if (values.created_from.trim()) sp.set("created_from", values.created_from.trim());
  if (values.created_to.trim()) sp.set("created_to", values.created_to.trim());

  if (values.format === "redirect") {
    sp.set("redirect", "1");
    sp.set("format", "image");
  } else {
    sp.set("format", values.format);
  }

  const qs = sp.toString();
  return qs ? `/random?${qs}` : "/random";
}

function pickHeaders(headers: Headers, keys: string[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const key of keys) {
    const v = headers.get(key);
    if (v) out[key] = v;
  }
  return out;
}

function isApiErrorBody(value: unknown): value is ApiErrorBody {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    v.ok === false &&
    typeof v.code === "string" &&
    typeof v.message === "string" &&
    typeof v.request_id === "string" &&
    !!v.details &&
    typeof v.details === "object"
  );
}

async function parseApiErrorFromResponse(resp: Response): Promise<ApiError> {
  const contentType = resp.headers.get("content-type") || "";
  if (contentType.toLowerCase().includes("application/json")) {
    try {
      const raw = (await resp.json()) as unknown;
      const body = isApiErrorBody(raw) ? raw : null;
      const message = formatApiErrorMessage(resp.status, body);
      return new ApiError(message, { status: resp.status, body });
    } catch {
      // fall through
    }
  }
  return new ApiError(`HTTP ${resp.status}`, { status: resp.status });
}

function hintsFromError(err: unknown): { appliedFilters: Record<string, unknown> | null; suggestions: string[] } | null {
  if (!(err instanceof ApiError)) return null;
  const details = err.body?.details;
  if (!details || typeof details !== "object") return null;
  const hintsRaw = (details as Record<string, unknown>)["hints"];
  if (!hintsRaw || typeof hintsRaw !== "object" || Array.isArray(hintsRaw)) return null;

  const hints = hintsRaw as Record<string, unknown>;
  const appliedFiltersRaw = hints["applied_filters"];
  const suggestionsRaw = hints["suggestions"];

  const appliedFilters =
    appliedFiltersRaw && typeof appliedFiltersRaw === "object" && !Array.isArray(appliedFiltersRaw)
      ? (appliedFiltersRaw as Record<string, unknown>)
      : null;

  const suggestions = Array.isArray(suggestionsRaw)
    ? suggestionsRaw.map((v) => String(v || "").trim()).filter((v) => v.length > 0)
    : [];

  return appliedFilters || suggestions.length > 0 ? { appliedFilters, suggestions } : null;
}

async function fetchPlayground(values: PlaygroundFormValues): Promise<PlaygroundResult> {
  const url = buildRandomUrl(values);

  if (values.format === "json") {
    const payload = await apiJson<RandomJsonResponse>(url);
    return { kind: "json", url, request_id: payload.request_id, payload };
  }

  if (values.format === "image") {
    const resp = await apiFetch(url, { method: "GET" });
    const reqId = resp.headers.get("x-request-id") || resp.headers.get("x-request_id") || null;
    if (!resp.ok) throw await parseApiErrorFromResponse(resp);

    const blob = await resp.blob();
    if (!("createObjectURL" in URL)) {
      throw new Error("当前浏览器不支持 URL.createObjectURL");
    }
    const src = URL.createObjectURL(blob);
    const headers = pickHeaders(resp.headers, [
      "content-type",
      "cache-control",
      "content-disposition",
      "x-origin-url",
      "x-request-id",
    ]);

    return { kind: "image", url, request_id: reqId, src, headers };
  }

  const resp = await apiFetch(url, { method: "GET", redirect: "manual" });
  const reqId = resp.headers.get("x-request-id") || resp.headers.get("x-request_id") || null;
  const location = resp.headers.get("location");
  if (location) return { kind: "redirect", url, request_id: reqId, location, note: null };

  const fallbackUrl = url.replace(/[?&]redirect=1(&|$)/, "$1");
  const payload = await apiJson<RandomJsonResponse>(fallbackUrl.replace(/([?&])format=image(&|$)/, "$1format=json$2"));
  const proxy = payload.data?.urls?.proxy || null;
  return {
    kind: "redirect",
    url,
    request_id: reqId ?? payload.request_id,
    location: proxy,
    note: proxy ? "未获取到 Location 响应头，已回退为结构化返回中的代理链接。" : "未获取到 Location 响应头。",
  };
}

async function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const el = document.createElement("textarea");
  el.value = text;
  el.setAttribute("readonly", "");
  el.style.position = "fixed";
  el.style.left = "-9999px";
  document.body.appendChild(el);
  el.select();
  document.execCommand("copy");
  document.body.removeChild(el);
}

export function PlaygroundPage() {
  const location = useLocation();
  const [form] = Form.useForm<PlaygroundFormValues>();

  const m = useMutation({ mutationFn: fetchPlayground });

  useEffect(() => {
    const sp = new URLSearchParams(location.search);
    const format = sp.get("format");
    const includedTags = sp.get("included_tags");

    const updates: Partial<PlaygroundFormValues> = {};
    if (format === "image" || format === "json" || format === "redirect") updates.format = format;
    if (includedTags != null) updates.included_tags = includedTags;

    if (Object.keys(updates).length > 0) form.setFieldsValue(updates);
  }, [form, location.search]);

  useEffect(() => {
    if (m.data?.kind !== "image") return;
    const src = m.data.src;
    return () => {
      URL.revokeObjectURL(src);
    };
  }, [m.data]);

  const url = m.data?.url || null;

  const onCopyUrl = async () => {
    if (!url) return;
    await copyText(url);
  };

  const onCopyCurl = async () => {
    if (!url) return;
    await copyText(`curl -i ${JSON.stringify(url)}`);
  };

  const onOpen = () => {
    if (!url) return;
    window.open(url, "_blank", "noopener,noreferrer");
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        随机接口调试
      </Typography.Title>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={8}>
          <Card title="筛选条件">
            <Form<PlaygroundFormValues>
              form={form}
              layout="vertical"
              initialValues={{
                format: "json",
                attempts: null,
                seed: "",
                strategy: "default",
                quality_samples: null,
                r18: 0,
                r18_strict: "default",
                adaptive: false,
                layout: "any",
                min_width: null,
                min_height: null,
                min_pixels: null,
                min_bookmarks: null,
                min_views: null,
                min_comments: null,
                included_tags: "",
                excluded_tags: "",
                user_id: null,
                illust_id: null,
                ai_type: "any",
                illust_type: "any",
                created_from: "",
                created_to: "",
              }}
              onFinish={(v) => m.mutate(v)}
            >
              <Form.Item label="返回格式" name="format">
                <Select
                  options={[
                    { value: "image", label: "图片流" },
                    { value: "json", label: "JSON" },
                    { value: "redirect", label: "重定向" },
                  ]}
                />
              </Form.Item>

              <Form.Item label="尝试次数（留空=服务端默认）" name="attempts">
                <InputNumber min={1} max={10} placeholder="留空使用服务端默认" style={{ width: "100%" }} />
              </Form.Item>

              <Form.Item label="随机种子" name="seed">
                <Input placeholder="可选" />
              </Form.Item>

              <Form.Item label="随机策略" name="strategy">
                <Select
                  options={[
                    { value: "default", label: "使用服务端默认" },
                    { value: "quality", label: "质量优先（更偏向高收藏/高清）" },
                    { value: "random", label: "纯随机（random_key）" },
                  ]}
                />
              </Form.Item>

              <Form.Item noStyle shouldUpdate={(prev, next) => prev.strategy !== next.strategy}>
                {({ getFieldValue }) => (
                  <Form.Item label="质量抽样数量（quality，留空=服务端默认）" name="quality_samples">
                    <InputNumber
                      min={1}
                      max={1000}
                      placeholder="留空使用服务端默认"
                      style={{ width: "100%" }}
                      disabled={getFieldValue("strategy") !== "quality"}
                    />
                  </Form.Item>
                )}
              </Form.Item>

              <Form.Item label="R18" name="r18">
                <Select
                  options={[
                    { value: 0, label: "0" },
                    { value: 1, label: "1" },
                    { value: 2, label: "2" },
                  ]}
                />
              </Form.Item>

              <Form.Item label="严格 R18 过滤（留空=服务端默认）" name="r18_strict">
                <Select
                  options={[
                    { value: "default", label: "使用服务端默认" },
                    { value: "1", label: "开启（更严格）" },
                    { value: "0", label: "关闭（允许未知 x_restrict）" },
                  ]}
                />
              </Form.Item>

              <Form.Item
                label="自适应（自动识别移动/桌面）"
                name="adaptive"
                valuePropName="checked"
                extra="开启后：在未显式设置方向/分辨率门槛时，服务端会根据设备类型自动选择更合适的默认值。"
              >
                <Switch />
              </Form.Item>

              <Form.Item label="画面方向（layout）" name="layout">
                <Select
                  options={[
                    { value: "any", label: "不限" },
                    { value: "portrait", label: "竖图" },
                    { value: "landscape", label: "横图" },
                    { value: "square", label: "方图" },
                  ]}
                />
              </Form.Item>

              <Form.Item label="最小宽度" name="min_width">
                <InputNumber min={0} style={{ width: "100%" }} />
              </Form.Item>
              <Form.Item label="最小高度" name="min_height">
                <InputNumber min={0} style={{ width: "100%" }} />
              </Form.Item>
              <Form.Item label="最小像素" name="min_pixels">
                <InputNumber min={0} style={{ width: "100%" }} />
              </Form.Item>

              <Form.Item label="最小收藏数" name="min_bookmarks">
                <InputNumber min={0} style={{ width: "100%" }} />
              </Form.Item>
              <Form.Item label="最小浏览数" name="min_views">
                <InputNumber min={0} style={{ width: "100%" }} />
              </Form.Item>
              <Form.Item label="最小评论数" name="min_comments">
                <InputNumber min={0} style={{ width: "100%" }} />
              </Form.Item>

              <Form.Item label="包含标签（用 | 分隔）" name="included_tags">
                <Input placeholder="标签1|标签2" />
              </Form.Item>
              <Form.Item label="排除标签（用 | 分隔）" name="excluded_tags">
                <Input placeholder="标签1|标签2" />
              </Form.Item>

              <Form.Item label="作者ID" name="user_id">
                <InputNumber min={1} style={{ width: "100%" }} />
              </Form.Item>
              <Form.Item label="作品ID" name="illust_id">
                <InputNumber min={1} style={{ width: "100%" }} />
              </Form.Item>

              <Form.Item label="AI 类型" name="ai_type">
                <Select
                  options={[
                    { value: "any", label: "不限" },
                    { value: "0", label: "0" },
                    { value: "1", label: "1" },
                  ]}
                />
              </Form.Item>

              <Form.Item label="作品类型（illust_type）" name="illust_type">
                <Select
                  options={[
                    { value: "any", label: "不限" },
                    { value: "illust", label: "插画（illust）" },
                    { value: "manga", label: "漫画（manga）" },
                    { value: "ugoira", label: "动图（ugoira）" },
                  ]}
                />
              </Form.Item>

              <Form.Item label="创建时间起点（ISO）" name="created_from">
                <Input placeholder="2024-01-01T00:00:00Z" />
              </Form.Item>
              <Form.Item label="创建时间终点（ISO）" name="created_to">
                <Input placeholder="2024-12-31T23:59:59Z" />
              </Form.Item>

              <Button type="primary" htmlType="submit" loading={m.isPending} style={{ width: "100%" }}>
                开始请求
              </Button>
            </Form>
          </Card>
        </Col>

        <Col xs={24} lg={10}>
          <Card title="返回结果">
            {m.isPending ? (
              <Skeleton active />
            ) : m.isError ? (
              <Alert
                type="error"
                showIcon
                message={m.error instanceof Error ? m.error.message : "请求失败"}
                description={
                  (() => {
                    const rid = requestIdFromError(m.error);
                    const hints = hintsFromError(m.error);
                    if (!rid && !hints) return "";
                    return (
                      <Space direction="vertical" size={4}>
                        {rid ? <Typography.Text type="secondary">请求ID: {rid}</Typography.Text> : null}
                        {hints?.suggestions && hints.suggestions.length > 0 ? (
                          <>
                            <Typography.Text>建议：</Typography.Text>
                            <ul style={{ margin: 0, paddingLeft: 18 }}>
                              {hints.suggestions.map((s) => (
                                <li key={s}>{s}</li>
                              ))}
                            </ul>
                          </>
                        ) : null}
                        {hints?.appliedFilters ? (
                          <>
                            <Typography.Text>本次筛选条件：</Typography.Text>
                            <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                              {JSON.stringify(hints.appliedFilters, null, 2)}
                            </pre>
                          </>
                        ) : null}
                      </Space>
                    );
                  })()
                }
              />
            ) : m.isSuccess ? (
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                <Typography.Text type="secondary">请求ID: {m.data.request_id || "-"}</Typography.Text>
                <Typography.Text type="secondary">请求链接: {m.data.url}</Typography.Text>

                {m.data.kind === "json" ? (
                  <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                    {JSON.stringify(m.data.payload, null, 2)}
                  </pre>
                ) : m.data.kind === "image" ? (
                  <>
                    <img src={m.data.src} alt="随机图片" style={{ maxWidth: "100%", borderRadius: 6 }} />
                    <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{JSON.stringify(m.data.headers, null, 2)}</pre>
                  </>
                ) : (
                  <>
                    <Typography.Text>
                      跳转地址: {m.data.location ? m.data.location : "（未提供）"}
                    </Typography.Text>
                    {m.data.note ? <Alert type="info" showIcon message={m.data.note} /> : null}
                  </>
                )}
              </Space>
            ) : (
              <Alert
                type="info"
                showIcon
                message="就绪"
                description="设置筛选条件后，点击“开始请求”即可测试 /random。"
              />
            )}
          </Card>
        </Col>

        <Col xs={24} lg={6}>
          <Card title="快捷工具">
            <Space direction="vertical" style={{ width: "100%" }}>
              <Button onClick={onCopyUrl} disabled={!url}>
                复制链接
              </Button>
              <Button onClick={onCopyCurl} disabled={!url}>
                复制命令
              </Button>
              <Button onClick={onOpen} disabled={!url}>
                新窗口打开
              </Button>
              <Alert type="info" showIcon message="说明" description="重定向模式会受浏览器 fetch 规则限制。" />
            </Space>
          </Card>
        </Col>
      </Row>
    </Space>
  );
}

