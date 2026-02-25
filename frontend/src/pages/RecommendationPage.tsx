import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Form, Input, InputNumber, Select, Skeleton, Space, Switch, Typography } from "antd";
import React, { useEffect, useMemo, useState } from "react";

import { ApiError, apiJson } from "../api/client";

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

type RandomPreviewResponse = {
  ok: true;
  request_id: string;
  data: Record<string, unknown>;
};

type FormValues = {
  random_strategy: "quality" | "random";
  random_quality_samples: number;
  pick_mode: "weighted" | "best";
  temperature: number;

  w_bookmark: number;
  w_view: number;
  w_comment: number;
  w_pixels: number;
  w_bookmark_rate: number;
  w_freshness: number;
  w_bookmark_velocity: number;
  freshness_half_life_days: number;
  velocity_smooth_days: number;

  m_ai: number;
  m_non_ai: number;
  m_unknown_ai: number;
  m_illust: number;
  m_manga: number;
  m_ugoira: number;
  m_unknown_illust_type: number;

  dedup_enabled: boolean;
  dedup_window_s: number;
  dedup_max_images: number;
  dedup_max_authors: number;
  dedup_strict: boolean;
  dedup_image_penalty: number;
  dedup_author_penalty: number;

  preview_seed: string;
};

const STRATEGY_VALUES = new Set<FormValues["random_strategy"]>(["quality", "random"]);
const PICK_MODE_VALUES = new Set<FormValues["pick_mode"]>(["weighted", "best"]);

const DEFAULTS: Omit<FormValues, "preview_seed"> = {
  random_strategy: "quality",
  random_quality_samples: 12,
  pick_mode: "weighted",
  temperature: 1.0,
  w_bookmark: 4.0,
  w_view: 0.5,
  w_comment: 2.0,
  w_pixels: 1.0,
  w_bookmark_rate: 3.0,
  w_freshness: 1.0,
  w_bookmark_velocity: 1.2,
  freshness_half_life_days: 21.0,
  velocity_smooth_days: 2.0,
  m_ai: 1.0,
  m_non_ai: 1.0,
  m_unknown_ai: 1.0,
  m_illust: 1.0,
  m_manga: 1.0,
  m_ugoira: 1.0,
  m_unknown_illust_type: 1.0,
  dedup_enabled: true,
  dedup_window_s: 20 * 60,
  dedup_max_images: 5000,
  dedup_max_authors: 2000,
  dedup_strict: false,
  dedup_image_penalty: 8.0,
  dedup_author_penalty: 2.5,
};

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

function asFloat(value: unknown, fallback: number): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number.parseFloat(value.trim());
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

function buildPreviewUrl(seed: string): string {
  const sp = new URLSearchParams();
  sp.set("format", "json");
  sp.set("attempts", "1");
  if (seed.trim()) sp.set("seed", seed.trim());
  return `/random?${sp.toString()}`;
}

export function RecommendationPage() {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<FormValues>();

  const query = useQuery({
    queryKey: ["admin", "settings"],
    queryFn: () => apiJson<SettingsResponse>("/admin/api/settings"),
  });

  const [saveErrorMessage, setSaveErrorMessage] = useState<string | null>(null);
  const [saveRequestId, setSaveRequestId] = useState<string | null>(null);
  const [saveUpdated, setSaveUpdated] = useState<number | null>(null);

  const [previewErrorMessage, setPreviewErrorMessage] = useState<string | null>(null);
  const [previewRequestId, setPreviewRequestId] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewBody, setPreviewBody] = useState<RandomPreviewResponse | null>(null);

  useEffect(() => {
    if (!query.data) return;
    const settings = asObject(query.data.settings);
    const random = asObject(settings.random);
    const recommendation = asObject(random.recommendation);
    const dedup = asObject(random.dedup);
    const scoreWeights = asObject(recommendation.score_weights);
    const multipliers = asObject(recommendation.multipliers);

    form.setFieldsValue({
      random_strategy: asLowerEnum(random.strategy, STRATEGY_VALUES, DEFAULTS.random_strategy),
      random_quality_samples: asInt(random.quality_samples, DEFAULTS.random_quality_samples),
      pick_mode: asLowerEnum(recommendation.pick_mode, PICK_MODE_VALUES, DEFAULTS.pick_mode),
      temperature: asFloat(recommendation.temperature, DEFAULTS.temperature),
      freshness_half_life_days: asFloat(recommendation.freshness_half_life_days, DEFAULTS.freshness_half_life_days),
      velocity_smooth_days: asFloat(recommendation.velocity_smooth_days, DEFAULTS.velocity_smooth_days),
      w_bookmark: asFloat(scoreWeights.bookmark, DEFAULTS.w_bookmark),
      w_view: asFloat(scoreWeights.view, DEFAULTS.w_view),
      w_comment: asFloat(scoreWeights.comment, DEFAULTS.w_comment),
      w_pixels: asFloat(scoreWeights.pixels, DEFAULTS.w_pixels),
      w_bookmark_rate: asFloat(scoreWeights.bookmark_rate, DEFAULTS.w_bookmark_rate),
      w_freshness: asFloat(scoreWeights.freshness, DEFAULTS.w_freshness),
      w_bookmark_velocity: asFloat(scoreWeights.bookmark_velocity, DEFAULTS.w_bookmark_velocity),
      m_ai: asFloat(multipliers.ai, DEFAULTS.m_ai),
      m_non_ai: asFloat(multipliers.non_ai, DEFAULTS.m_non_ai),
      m_unknown_ai: asFloat(multipliers.unknown_ai, DEFAULTS.m_unknown_ai),
      m_illust: asFloat(multipliers.illust, DEFAULTS.m_illust),
      m_manga: asFloat(multipliers.manga, DEFAULTS.m_manga),
      m_ugoira: asFloat(multipliers.ugoira, DEFAULTS.m_ugoira),
      m_unknown_illust_type: asFloat(multipliers.unknown_illust_type, DEFAULTS.m_unknown_illust_type),
      dedup_enabled: asBool(dedup.enabled, DEFAULTS.dedup_enabled),
      dedup_window_s: asInt(dedup.window_s, DEFAULTS.dedup_window_s),
      dedup_max_images: asInt(dedup.max_images, DEFAULTS.dedup_max_images),
      dedup_max_authors: asInt(dedup.max_authors, DEFAULTS.dedup_max_authors),
      dedup_strict: asBool(dedup.strict, DEFAULTS.dedup_strict),
      dedup_image_penalty: asFloat(dedup.image_penalty, DEFAULTS.dedup_image_penalty),
      dedup_author_penalty: asFloat(dedup.author_penalty, DEFAULTS.dedup_author_penalty),
      preview_seed: "",
    });
  }, [form, query.data]);

  const qualityFormulaText = useMemo(() => {
    return [
      "score =",
      "  w_bookmark * ln(1 + bookmark_count)",
      "+ w_view * ln(1 + view_count)",
      "+ w_comment * ln(1 + comment_count)",
      "+ w_pixels * ln(1 + (width*height)/1_000_000)",
      "+ w_bookmark_rate * ln(1 + (bookmark_count/view_count)*1000)",
      "- w_freshness * (age_days / freshness_half_life_days)",
      "+ w_bookmark_velocity * ln(1 + bookmark_count / (age_days + velocity_smooth_days))",
      "",
      "说明：有 seed 时，为保证可复现，会自动关闭 freshness/bookmark_velocity 两项。",
      "最终得分会再乘以“类别倍率”（AI/插画/漫画/动图等）。",
      "倍率=0 表示彻底不返回该类别。",
    ].join("\n");
  }, []);

  const save = useMutation({
    mutationFn: (values: FormValues) =>
      apiJson<SettingsUpdateResponse>("/admin/api/settings", {
        method: "PUT",
        body: JSON.stringify({
          settings: {
            random: {
              strategy: values.random_strategy,
              quality_samples: values.random_quality_samples,
              dedup: {
                enabled: values.dedup_enabled,
                window_s: Math.max(0, Math.trunc(values.dedup_window_s || 0)),
                max_images: Math.max(1, Math.trunc(values.dedup_max_images || 1)),
                max_authors: Math.max(1, Math.trunc(values.dedup_max_authors || 1)),
                strict: values.dedup_strict,
                image_penalty: values.dedup_image_penalty,
                author_penalty: values.dedup_author_penalty,
              },
              recommendation: {
                pick_mode: values.pick_mode,
                temperature: values.temperature,
                freshness_half_life_days: values.freshness_half_life_days,
                velocity_smooth_days: values.velocity_smooth_days,
                score_weights: {
                  bookmark: values.w_bookmark,
                  view: values.w_view,
                  comment: values.w_comment,
                  pixels: values.w_pixels,
                  bookmark_rate: values.w_bookmark_rate,
                  freshness: values.w_freshness,
                  bookmark_velocity: values.w_bookmark_velocity,
                },
                multipliers: {
                  ai: values.m_ai,
                  non_ai: values.m_non_ai,
                  unknown_ai: values.m_unknown_ai,
                  illust: values.m_illust,
                  manga: values.m_manga,
                  ugoira: values.m_ugoira,
                  unknown_illust_type: values.m_unknown_illust_type,
                },
              },
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

  const preview = useMutation({
    mutationFn: async (values: FormValues) => {
      const url = buildPreviewUrl(values.preview_seed);
      setPreviewUrl(url);
      const data = await apiJson<RandomPreviewResponse>(url);
      return { url, data };
    },
    onMutate: () => {
      setPreviewErrorMessage(null);
      setPreviewRequestId(null);
      setPreviewBody(null);
      setPreviewUrl(null);
    },
    onSuccess: ({ url, data }) => {
      setPreviewUrl(url);
      setPreviewBody(data);
      setPreviewRequestId(data.request_id);
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        setPreviewErrorMessage(err.message);
        setPreviewRequestId(requestIdFromError(err));
        return;
      }
      if (err instanceof Error) {
        setPreviewErrorMessage(err.message);
        return;
      }
      setPreviewErrorMessage("预览失败");
    },
  });

  const onResetDefaults = () => {
    form.setFieldsValue({ ...DEFAULTS, preview_seed: form.getFieldValue("preview_seed") || "" });
  };

  const onAiHalf = () => {
    form.setFieldsValue({ m_ai: 0.5 });
  };

  const onDisableManga = () => {
    form.setFieldsValue({ m_manga: 0 });
  };

  const onOnlyIllust = () => {
    form.setFieldsValue({ m_illust: 1.0, m_manga: 0, m_ugoira: 0, m_unknown_illust_type: 0 });
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        推荐策略
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        这里管理的是 <Typography.Text code>/random</Typography.Text> 默认的“质量优先推荐”策略：权重/倍率/随机程度都会实时生效（保存后无需重启）。
      </Typography.Paragraph>

      <Space wrap>
        <Button
          type="primary"
          onClick={() => form.submit()}
          loading={save.isPending}
          disabled={!query.data || query.isError || query.isLoading}
        >
          保存推荐配置
        </Button>
        <Button onClick={onResetDefaults} disabled={!query.data || query.isError || query.isLoading}>
          重置为默认值
        </Button>
        <Button onClick={onAiHalf} disabled={!query.data || query.isError || query.isLoading}>
          AI 倍率设为 0.5
        </Button>
        <Button onClick={onDisableManga} disabled={!query.data || query.isError || query.isLoading}>
          漫画倍率设为 0
        </Button>
        <Button onClick={onOnlyIllust} disabled={!query.data || query.isError || query.isLoading}>
          只返回插画（漫画/动图=0）
        </Button>
      </Space>

      {save.isPending ? <Alert type="info" showIcon message="正在保存推荐配置..." /> : null}
      {saveErrorMessage ? <Alert type="error" showIcon message={saveErrorMessage} /> : null}
      {saveUpdated !== null ? (
        <Alert type="success" showIcon message="保存成功" description={`更新条目数: ${saveUpdated}`} />
      ) : null}
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
              选择路径
            </Typography.Title>
            <Form.Item label="默认随机策略" name="random_strategy">
              <Select
                options={[
                  { value: "quality", label: "质量优先（推荐）" },
                  { value: "random", label: "纯随机（random_key）" },
                ]}
                style={{ maxWidth: 360 }}
              />
            </Form.Item>
            <Form.Item
              label="质量抽样数量（quality_samples）"
              name="random_quality_samples"
              extra="质量优先会先抽样 N 张候选再按评分挑选，N 越大越偏向高分，但每次请求会读取并评分更多候选。建议 3~50；大图库可提高到 100~1000。"
            >
              <InputNumber min={1} max={1000} style={{ width: 240 }} />
            </Form.Item>
            <Form.Item label="质量选择模式（pick_mode）" name="pick_mode" extra="best 更偏向稳定返回高分；weighted 更随机但仍偏向高分。">
              <Select
                options={[
                  { value: "weighted", label: "加权随机（weighted）" },
                  { value: "best", label: "直接取最高分（best）" },
                ]}
                style={{ maxWidth: 360 }}
              />
            </Form.Item>
            <Form.Item label="随机温度（temperature）" name="temperature" extra="越小越趋近“只挑最高分”，越大越接近随机。建议 0.3~3。">
              <InputNumber min={0.05} max={100} step={0.05} style={{ width: 240 }} />
            </Form.Item>

            <Typography.Title level={5} style={{ marginTop: 12 }}>
              全局防重复（dedup）
            </Typography.Title>
            <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
              开启后，同一张图片在窗口期内会尽量不重复返回（进程内 best-effort；严格模式可禁止回退重复）。
            </Typography.Paragraph>
            <Space wrap align="start">
              <Form.Item label="启用" name="dedup_enabled" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item label="窗口（秒）" name="dedup_window_s" extra="例如 1200 表示 20 分钟。">
                <InputNumber min={0} max={24 * 60 * 60} step={10} style={{ width: 240 }} />
              </Form.Item>
              <Form.Item label="最大图片缓存" name="dedup_max_images">
                <InputNumber min={1} max={200000} step={100} style={{ width: 240 }} />
              </Form.Item>
              <Form.Item label="最大作者缓存" name="dedup_max_authors">
                <InputNumber min={1} max={200000} step={50} style={{ width: 240 }} />
              </Form.Item>
              <Form.Item label="严格模式" name="dedup_strict" valuePropName="checked" extra="开启后，当窗口内无可用图时不会回退重复。">
                <Switch />
              </Form.Item>
              <Form.Item label="图片重复惩罚" name="dedup_image_penalty">
                <InputNumber min={0} max={1000} step={0.5} style={{ width: 240 }} />
              </Form.Item>
              <Form.Item label="作者重复惩罚" name="dedup_author_penalty">
                <InputNumber min={0} max={1000} step={0.5} style={{ width: 240 }} />
              </Form.Item>
            </Space>

            <Typography.Title level={5} style={{ marginTop: 12 }}>
              评分公式（score_weights）
            </Typography.Title>
            <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
              <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{qualityFormulaText}</pre>
            </Typography.Paragraph>
            <Space wrap>
              <Form.Item label="收藏权重（bookmark）" name="w_bookmark">
                <InputNumber min={-100} max={100} step={0.1} style={{ width: 240 }} />
              </Form.Item>
              <Form.Item label="浏览权重（view）" name="w_view">
                <InputNumber min={-100} max={100} step={0.1} style={{ width: 240 }} />
              </Form.Item>
              <Form.Item label="评论权重（comment）" name="w_comment">
                <InputNumber min={-100} max={100} step={0.1} style={{ width: 240 }} />
              </Form.Item>
              <Form.Item label="分辨率权重（pixels）" name="w_pixels">
                <InputNumber min={-100} max={100} step={0.1} style={{ width: 240 }} />
              </Form.Item>
              <Form.Item label="收藏率权重（bookmark_rate）" name="w_bookmark_rate">
                <InputNumber min={-100} max={100} step={0.1} style={{ width: 240 }} />
              </Form.Item>
              <Form.Item label="新鲜度权重（freshness）" name="w_freshness" extra="时间衰减（loss）：-w * (age_days/half_life)，越老越扣分。">
                <InputNumber min={-100} max={100} step={0.1} style={{ width: 240 }} />
              </Form.Item>
              <Form.Item label="新鲜度半衰期（天）" name="freshness_half_life_days" extra="半衰期越小，衰减越强。">
                <InputNumber min={0.1} max={3650} step={0.5} style={{ width: 240 }} />
              </Form.Item>
              <Form.Item
                label="收藏增长率权重（bookmark_velocity）"
                name="w_bookmark_velocity"
                extra="ln(1 + bookmark_count/(age_days + smooth_days))，帮助“好看的新图”被选中。"
              >
                <InputNumber min={-100} max={100} step={0.1} style={{ width: 240 }} />
              </Form.Item>
              <Form.Item label="增长率平滑（天）" name="velocity_smooth_days" extra="值越大，越不容易因为“刚发布”而爆表。">
                <InputNumber min={0} max={3650} step={0.5} style={{ width: 240 }} />
              </Form.Item>
            </Space>

            <Typography.Title level={5} style={{ marginTop: 12 }}>
              类别倍率（multipliers）
            </Typography.Title>
            <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
              提示：倍率=0 会直接剔除该类别；倍率越大，被选中的概率越高。建议范围 0~3（通常不需要很大）。
            </Typography.Paragraph>
            <Space wrap>
              <Form.Item label="AI（ai_type=1）" name="m_ai">
                <InputNumber min={0} max={100} step={0.05} style={{ width: 240 }} />
              </Form.Item>
              <Form.Item label="非 AI（ai_type=0）" name="m_non_ai">
                <InputNumber min={0} max={100} step={0.05} style={{ width: 240 }} />
              </Form.Item>
              <Form.Item label="未知 AI（ai_type=NULL）" name="m_unknown_ai">
                <InputNumber min={0} max={100} step={0.05} style={{ width: 240 }} />
              </Form.Item>
              <Form.Item label="插画（illust_type=0）" name="m_illust">
                <InputNumber min={0} max={100} step={0.05} style={{ width: 240 }} />
              </Form.Item>
              <Form.Item label="漫画（illust_type=1）" name="m_manga">
                <InputNumber min={0} max={100} step={0.05} style={{ width: 240 }} />
              </Form.Item>
              <Form.Item label="动图（illust_type=2）" name="m_ugoira">
                <InputNumber min={0} max={100} step={0.05} style={{ width: 240 }} />
              </Form.Item>
              <Form.Item label="未知类型（illust_type=NULL）" name="m_unknown_illust_type">
                <InputNumber min={0} max={100} step={0.05} style={{ width: 240 }} />
              </Form.Item>
            </Space>

            <Typography.Title level={5} style={{ marginTop: 12 }}>
              预览（/random）
            </Typography.Title>
            <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
              预览使用的是“已保存并生效”的配置（不是未保存的草稿）。建议先保存，再预览。
            </Typography.Paragraph>
            <Space wrap align="end">
              <Form.Item label="种子（可选）" name="preview_seed">
                <Input placeholder="例如: demo-seed-1" style={{ width: 280 }} />
              </Form.Item>
              <Button onClick={() => preview.mutate(form.getFieldsValue(true))} loading={preview.isPending}>
                预览一次随机结果
              </Button>
            </Space>

            {preview.isPending ? <Alert type="info" showIcon message="正在预览..." /> : null}
            {previewErrorMessage ? <Alert type="error" showIcon message={previewErrorMessage} /> : null}
            {previewRequestId ? <Typography.Text type="secondary">请求ID: {previewRequestId}</Typography.Text> : null}
            {previewUrl ? <Typography.Text type="secondary">请求链接: {previewUrl}</Typography.Text> : null}
            {previewBody ? (
              <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                {JSON.stringify(previewBody, null, 2)}
              </pre>
            ) : null}
          </Form>
        </Card>
      )}
    </Space>
  );
}
