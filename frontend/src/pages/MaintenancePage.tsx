import { useMutation } from "@tanstack/react-query";
import { Alert, Button, Card, Form, InputNumber, Skeleton, Space, Switch, Typography } from "antd";
import React from "react";

import { ApiError, apiJson } from "../api/client";

type CleanupFormValues = {
  keep_days: number;
  max_delete_rows: number;
  chunk_size: number;
  dry_run: boolean;
};

type CleanupResponse = {
  ok: true;
  dry_run: boolean;
  cutoff: string;
  deleted?: number;
  would_delete?: number;
  has_more: boolean;
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

export function MaintenancePage() {
  const [form] = Form.useForm<CleanupFormValues>();
  const [result, setResult] = React.useState<CleanupResponse | null>(null);
  const [errorMessage, setErrorMessage] = React.useState<string | null>(null);
  const [errorRequestId, setErrorRequestId] = React.useState<string | null>(null);

  const cleanup = useMutation({
    mutationFn: (values: CleanupFormValues) =>
      apiJson<CleanupResponse>("/admin/api/maintenance/request-logs/cleanup", {
        method: "POST",
        body: JSON.stringify({
          keep_days: values.keep_days,
          max_delete_rows: values.max_delete_rows,
          chunk_size: values.chunk_size,
          dry_run: Boolean(values.dry_run),
        }),
      }),
    onMutate: () => {
      setResult(null);
      setErrorMessage(null);
      setErrorRequestId(null);
    },
    onSuccess: (data) => setResult(data),
    onError: (err) => {
      setErrorMessage(messageFromError(err));
      setErrorRequestId(requestIdFromError(err));
    },
  });

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        维护工具
      </Typography.Title>

      <Card title="请求日志清理">
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          清理过期 request_logs，建议先 dry-run 预览再实际删除。可多次执行直到 has_more=false。
        </Typography.Paragraph>

        <Form
          form={form}
          layout="vertical"
          initialValues={{ keep_days: 14, max_delete_rows: 50000, chunk_size: 2000, dry_run: true }}
          onFinish={(values) => cleanup.mutate(values)}
          style={{ maxWidth: 520 }}
        >
          <Form.Item label="保留天数" name="keep_days" rules={[{ required: true }]}>
            <InputNumber min={0} max={36500} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item label="单次最大删除行数" name="max_delete_rows" rules={[{ required: true }]}>
            <InputNumber min={1} max={10_000_000} step={1000} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item label="分块大小" name="chunk_size" rules={[{ required: true }]}>
            <InputNumber min={1} max={100_000} step={100} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item label="仅预览（dry_run）" name="dry_run" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Space wrap>
            <Button type="primary" htmlType="submit" loading={cleanup.isPending}>
              执行
            </Button>
            <Button
              onClick={() => {
                form.setFieldsValue({ dry_run: true });
                form.submit();
              }}
              loading={cleanup.isPending}
            >
              预览
            </Button>
          </Space>
        </Form>

        {cleanup.isPending ? <Skeleton active style={{ marginTop: 16 }} /> : null}
        {errorMessage ? (
          <Alert
            type="error"
            showIcon
            style={{ marginTop: 16 }}
            message={errorMessage}
            description={errorRequestId ? `请求ID: ${errorRequestId}` : ""}
          />
        ) : null}
        {result ? (
          <Alert
            type="success"
            showIcon
            style={{ marginTop: 16 }}
            message={result.dry_run ? "预览完成" : "清理完成"}
            description={
              result.dry_run
                ? `cutoff=${result.cutoff}，would_delete=${result.would_delete ?? 0}，has_more=${String(result.has_more)}，请求ID: ${result.request_id}`
                : `cutoff=${result.cutoff}，deleted=${result.deleted ?? 0}，has_more=${String(result.has_more)}，请求ID: ${result.request_id}`
            }
          />
        ) : null}
      </Card>
    </Space>
  );
}
