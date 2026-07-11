import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Card, Descriptions, Form, InputNumber, Skeleton, Space, Switch, Tag, Typography } from "antd";
import React from "react";

import { ActionAlerts } from "../admin/ActionAlerts";
import { QueryState } from "../admin/QueryState";
import { useActionAlerts } from "../admin/useActionAlerts";
import { apiJson } from "../api/client";

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

type RandomEngineStatusResponse = {
  ok: true;
  enabled: boolean;
  url: string;
  traffic_percent?: number;
  timeout_ms?: number;
  healthy: boolean;
  health: Record<string, unknown> | null;
  request_id: string;
};

type RandomEngineSnapshotResponse = {
  ok: true;
  revision: string;
  engine: Record<string, unknown>;
  request_id: string;
};

type RandomEngineCompareResponse = {
  ok: true;
  match: boolean;
  python_filtered: number;
  engine_filtered: number;
  delta: number;
  engine_index_size: number;
  engine_revision: string;
  r18_strict: number;
  request_id: string;
};

export function MaintenancePage() {
  const [form] = Form.useForm<CleanupFormValues>();
  const alerts = useActionAlerts();
  const engineAlerts = useActionAlerts();
  const queryClient = useQueryClient();
  const [compareResult, setCompareResult] = React.useState<RandomEngineCompareResponse | null>(null);

  const engineStatus = useQuery({
    queryKey: ["admin", "maintenance", "random-engine"],
    queryFn: () => apiJson<RandomEngineStatusResponse>("/admin/api/maintenance/random-engine"),
    refetchInterval: 15_000,
  });

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
      alerts.clear();
    },
    onSuccess: (data) => {
      const msg = data.dry_run ? "预览完成" : "清理完成";
      const detail = data.dry_run
        ? `cutoff=${data.cutoff}，would_delete=${data.would_delete ?? 0}，has_more=${String(data.has_more)}`
        : `cutoff=${data.cutoff}，deleted=${data.deleted ?? 0}，has_more=${String(data.has_more)}`;
      alerts.setSuccess(`${msg}（${detail}）`, data.request_id);
    },
    onError: (err) => {
      alerts.setError(err);
    },
  });

  const pushSnapshot = useMutation({
    mutationFn: () =>
      apiJson<RandomEngineSnapshotResponse>("/admin/api/maintenance/random-engine/snapshot", {
        method: "POST",
        body: JSON.stringify({}),
      }),
    onMutate: () => {
      engineAlerts.clear();
    },
    onSuccess: (data) => {
      engineAlerts.setSuccess(`快照已推送 revision=${data.revision}`, data.request_id);
      void queryClient.invalidateQueries({ queryKey: ["admin", "maintenance", "random-engine"] });
    },
    onError: (err) => {
      engineAlerts.setError(err);
    },
  });

  const compareFilters = useMutation({
    mutationFn: () =>
      apiJson<RandomEngineCompareResponse>("/admin/api/maintenance/random-engine/compare-filters", {
        method: "POST",
        body: JSON.stringify({ r18: 0 }),
      }),
    onMutate: () => {
      engineAlerts.clear();
      setCompareResult(null);
    },
    onSuccess: (data) => {
      setCompareResult(data);
      const msg = data.match
        ? `过滤基数一致 python=${data.python_filtered} engine=${data.engine_filtered}`
        : `过滤基数不一致 delta=${data.delta}（python=${data.python_filtered} engine=${data.engine_filtered}）`;
      engineAlerts.setSuccess(msg, data.request_id);
    },
    onError: (err) => {
      engineAlerts.setError(err);
    },
  });

  const engine = engineStatus.data;
  const health = engine?.health && typeof engine.health === "object" ? engine.health : null;
  const indexSize =
    health && typeof (health as { index_size?: unknown }).index_size === "number"
      ? Number((health as { index_size: number }).index_size)
      : null;
  const revision =
    health && typeof (health as { snapshot_revision?: unknown }).snapshot_revision === "string"
      ? String((health as { snapshot_revision: string }).snapshot_revision)
      : null;

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        维护工具
      </Typography.Title>

      <Card title="Random Engine（Go 双跑）">
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          只读状态 + 全量快照推送。切流由环境变量控制（默认关）：RANDOM_ENGINE_ENABLED /
          RANDOM_ENGINE_TRAFFIC_PERCENT。未配置 URL 时推送会失败。
        </Typography.Paragraph>

        <QueryState query={engineStatus}>
          {engine ? (
            <Descriptions size="small" column={1} bordered style={{ maxWidth: 640, marginBottom: 16 }}>
              <Descriptions.Item label="URL">{engine.url || "（未配置）"}</Descriptions.Item>
              <Descriptions.Item label="双跑开关">
                {engine.enabled ? <Tag color="green">ENABLED</Tag> : <Tag>OFF</Tag>}
              </Descriptions.Item>
              <Descriptions.Item label="切流 %">
                {typeof engine.traffic_percent === "number" ? engine.traffic_percent : "—"}
              </Descriptions.Item>
              <Descriptions.Item label="超时 ms">
                {typeof engine.timeout_ms === "number" ? engine.timeout_ms : "—"}
              </Descriptions.Item>
              <Descriptions.Item label="健康">
                {engine.healthy ? <Tag color="green">healthy</Tag> : <Tag color="orange">unreachable</Tag>}
              </Descriptions.Item>
              <Descriptions.Item label="索引规模">{indexSize ?? "—"}</Descriptions.Item>
              <Descriptions.Item label="快照 revision">{revision || "—"}</Descriptions.Item>
            </Descriptions>
          ) : null}
        </QueryState>

        <Space wrap>
          <Button onClick={() => void engineStatus.refetch()} loading={engineStatus.isFetching}>
            刷新状态
          </Button>
          <Button type="primary" onClick={() => pushSnapshot.mutate()} loading={pushSnapshot.isPending}>
            推送全量快照
          </Button>
          <Button onClick={() => compareFilters.mutate()} loading={compareFilters.isPending}>
            对比过滤基数（默认 r18=0）
          </Button>
        </Space>

        {compareResult ? (
          <Descriptions size="small" column={1} bordered style={{ maxWidth: 640, marginTop: 16 }}>
            <Descriptions.Item label="match">
              {compareResult.match ? <Tag color="green">一致</Tag> : <Tag color="red">不一致</Tag>}
            </Descriptions.Item>
            <Descriptions.Item label="Python filtered">{compareResult.python_filtered}</Descriptions.Item>
            <Descriptions.Item label="Engine filtered">{compareResult.engine_filtered}</Descriptions.Item>
            <Descriptions.Item label="delta">{compareResult.delta}</Descriptions.Item>
            <Descriptions.Item label="Engine index">{compareResult.engine_index_size}</Descriptions.Item>
            <Descriptions.Item label="r18_strict">{compareResult.r18_strict}</Descriptions.Item>
          </Descriptions>
        ) : null}

        <div style={{ marginTop: 16 }}>
          <ActionAlerts
            message={engineAlerts.message}
            requestId={engineAlerts.requestId}
            errorMessage={engineAlerts.errorMessage}
            errorRequestId={engineAlerts.errorRequestId}
            requestIdPlacement="description"
          />
        </div>
      </Card>

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
        <div style={{ marginTop: 16 }}>
          <ActionAlerts
            message={alerts.message}
            requestId={alerts.requestId}
            errorMessage={alerts.errorMessage}
            errorRequestId={alerts.errorRequestId}
            requestIdPlacement="description"
          />
        </div>
      </Card>
    </Space>
  );
}
