import { useMutation } from "@tanstack/react-query";
import { Alert, Button, Card, Form, Input, Space, Switch, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import React, { useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, apiJson } from "../api/client";

type ImportFormValues = {
  text: string;
  dry_run: boolean;
  hydrate_on_import: boolean;
};

type ImportCreateResponse = {
  ok: true;
  import_id: string;
  job_id: string;
  executed_inline?: boolean;
  accepted: number;
  deduped: number;
  errors: Array<{ line: number; url: string; code: string; message: string }>;
  preview: Array<{ illust_id: number; page_index: number; ext: string; url: string }>;
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

const previewColumns: ColumnsType<ImportCreateResponse["preview"][number]> = [
  { title: "作品ID", dataIndex: "illust_id", key: "illust_id" },
  { title: "页码", dataIndex: "page_index", key: "page_index" },
  { title: "扩展名", dataIndex: "ext", key: "ext" },
  {
    title: "URL",
    dataIndex: "url",
    key: "url",
    render: (value: string) => (
      <Typography.Text code copyable={{ text: String(value || "") }}>
        {String(value || "")}
      </Typography.Text>
    ),
  },
];

const errorColumns: ColumnsType<ImportCreateResponse["errors"][number]> = [
  { title: "行号", dataIndex: "line", key: "line" },
  { title: "错误码", dataIndex: "code", key: "code" },
  { title: "说明", dataIndex: "message", key: "message" },
  {
    title: "URL",
    dataIndex: "url",
    key: "url",
    render: (value: string) => (
      <Typography.Text code copyable={{ text: String(value || "") }}>
        {String(value || "")}
      </Typography.Text>
    ),
  },
];

export function ImportPage() {
  const navigate = useNavigate();
  const [form] = Form.useForm<ImportFormValues>();
  const [file, setFile] = useState<File | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [result, setResult] = useState<ImportCreateResponse | null>(null);

  const mutation = useMutation({
    mutationFn: (values: ImportFormValues) => {
      if (file) {
        const formData = new FormData();
        formData.append("file", file);
        formData.append("dry_run", values.dry_run ? "true" : "false");
        formData.append("hydrate_on_import", values.hydrate_on_import ? "true" : "false");
        formData.append("source", "manual");
        return apiJson<ImportCreateResponse>("/admin/api/imports", { method: "POST", body: formData });
      }

      return apiJson<ImportCreateResponse>("/admin/api/imports", {
        method: "POST",
        body: JSON.stringify({
          text: values.text,
          dry_run: values.dry_run,
          hydrate_on_import: values.hydrate_on_import,
          source: "manual",
        }),
      });
    },
    onMutate: () => {
      setErrorMessage(null);
      setRequestId(null);
      setResult(null);
    },
    onSuccess: (data) => {
      setResult(data);
      setRequestId(data.request_id);
    },
    onError: (err) => {
      setErrorMessage(messageFromError(err));
      setRequestId(requestIdFromError(err));
    },
  });

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        导入图片链接
      </Typography.Title>

      {errorMessage ? <Alert type="error" message={errorMessage} showIcon /> : null}
      {requestId ? <Typography.Text type="secondary">请求ID: {requestId}</Typography.Text> : null}

      <Card title="支持的链接格式（重要）">
        <Space direction="vertical" size={4}>
          <Typography.Text>仅支持 Pixiv 原图（pximg）链接，每行一个：</Typography.Text>
          <Typography.Text code copyable>
            https://i.pximg.net/img-original/img/2024/01/01/00/00/00/12345678_p0.png
          </Typography.Text>
          <Typography.Text type="secondary">多P作品需要分别提供 p0/p1/... 的链接。</Typography.Text>
          <Typography.Text type="secondary">不支持作品页链接（例如 www.pixiv.net/artworks/12345678）。</Typography.Text>
        </Space>
      </Card>

      <Card>
        <Form<ImportFormValues>
          form={form}
          layout="vertical"
          initialValues={{ dry_run: false, hydrate_on_import: true, text: "" }}
          onFinish={(values) => mutation.mutate(values)}
        >
          <Form.Item label="上传文本文件（可选，.txt）">
            <input
              data-testid="import-file-input"
              type="file"
              accept=".txt,text/plain"
              onChange={(e) => setFile(e.target.files && e.target.files[0] ? e.target.files[0] : null)}
            />
            <div style={{ marginTop: 8 }}>
              <Typography.Text type="secondary">{file ? `已选择文件：${file.name}` : "未选择文件（可在下方粘贴链接）。"}</Typography.Text>
            </div>
          </Form.Item>

          <Form.Item
            label="链接列表"
            name="text"
            rules={[
              {
                validator: async (_, value) => {
                  if (file) return Promise.resolve();
                  if (String(value || "").trim()) return Promise.resolve();
                  return Promise.reject(new Error("请粘贴 Pixiv 原图链接，或上传 .txt 文件"));
                },
              },
            ]}
          >
            <Input.TextArea rows={8} placeholder="每行一个链接" disabled={Boolean(file)} />
          </Form.Item>

          <Space size="large">
            <Form.Item label="仅预览（不入库）" name="dry_run" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item
              label="导入后立即补全元数据（推荐）"
              name="hydrate_on_import"
              valuePropName="checked"
              extra="随机质量与筛选依赖元数据覆盖率。需要已配置令牌且 worker 正在运行。"
            >
              <Switch />
            </Form.Item>
          </Space>

          <Button type="primary" htmlType="submit" loading={mutation.isPending}>
            开始导入
          </Button>
        </Form>
      </Card>

      {mutation.isPending ? <Alert type="info" showIcon message="正在导入..." /> : null}

      {result ? (
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Alert
            type="success"
            showIcon
            message={result.import_id ? `导入任务已创建：#${result.import_id}` : "预览完成"}
            description={`接收: ${result.accepted}，去重: ${result.deduped}，错误: ${result.errors.length}`}
          />

          {result.import_id ? (
            <Card>
              <Space wrap>
                <Button type="primary" onClick={() => navigate(`/admin/import/${result.import_id}`)}>
                  打开导入详情
                </Button>
                <Typography.Text type="secondary">任务ID: {result.job_id || "-"}</Typography.Text>
                {"executed_inline" in result ? (
                  <Typography.Text type="secondary">
                    前台执行: {result.executed_inline ? "是（可能较慢）" : "否（由 worker 处理）"}
                  </Typography.Text>
                ) : null}
              </Space>
              {result.executed_inline ? (
                <Alert
                  type="warning"
                  showIcon
                  message="提示：本次导入已在前台直接执行"
                  description="小批量导入可能会在 API 进程内直接执行（占用更多时间/资源）。如需全部后台执行，请把 IMPORT_INLINE_MAX_ACCEPTED 设为 0 或导入超过该阈值。"
                  style={{ marginTop: 12 }}
                />
              ) : null}
            </Card>
          ) : null}

          {result.preview.length > 0 ? (
            <Card title={`预览（前 ${result.preview.length} 条）`}>
              <Table
                rowKey={(row) => `${row.illust_id}_${row.page_index}_${row.ext}`}
                columns={previewColumns}
                dataSource={result.preview}
                pagination={false}
                size="small"
              />
            </Card>
          ) : null}

          {result.errors.length > 0 ? (
            <Card title={`错误行（前 ${result.errors.length} 条）`}>
              <Alert
                type="warning"
                showIcon
                message="存在无法识别的链接"
                description="请检查链接是否为 pximg 原图格式（含 _p0/_p1 等）。"
                style={{ marginBottom: 12 }}
              />
              <Table rowKey={(row) => `${row.line}_${row.code}`} columns={errorColumns} dataSource={result.errors} pagination={false} size="small" />
            </Card>
          ) : null}
        </Space>
      ) : null}
    </Space>
  );
}
