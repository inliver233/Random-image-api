import { Alert, Button, Card, Form, Input, Space, Typography } from "antd";
import React, { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { ActionAlerts } from "../admin/ActionAlerts";
import { useActionAlerts } from "../admin/useActionAlerts";
import { apiJson } from "../api/client";
import { setAdminToken } from "../auth/tokenStorage";

type LoginFormValues = {
  username: string;
  password: string;
};

type LoginResponse = {
  ok: true;
  token: string;
  request_id: string;
};

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [form] = Form.useForm<LoginFormValues>();
  const alerts = useActionAlerts();
  const [loading, setLoading] = useState(false);

  const searchParams = new URLSearchParams(location.search);
  const reason = String(searchParams.get("reason") || "").trim();
  const next = String(searchParams.get("next") || "").trim();

  const reasonMessage =
    reason === "unauthorized"
      ? "登录已失效，请重新登录。"
      : reason === "missing_token"
        ? "请先登录后再访问管理后台。"
        : reason === "logout"
          ? "已退出登录。"
          : null;

  const onFinish = async (values: LoginFormValues) => {
    setLoading(true);
    alerts.clear();

    try {
      const resp = await apiJson<LoginResponse>("/admin/api/login", {
        method: "POST",
        body: JSON.stringify({ username: values.username, password: values.password }),
      });

      setAdminToken(resp.token);
      // No success banner — only flash request id before navigate (matches prior UX / tests).
      alerts.setSuccess("", resp.request_id);
      form.resetFields(["password"]);

      const nextPath = next && next.startsWith("/admin") && !next.startsWith("/admin/login") ? next : "/admin";
      navigate(nextPath, { replace: true });
    } catch (err: unknown) {
      alerts.setError(err, "登录失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
      }}
    >
      <Card style={{ width: 360 }}>
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Typography.Title level={3} style={{ margin: 0 }}>
            管理后台登录
          </Typography.Title>

          {reasonMessage ? <Alert type="info" message={reasonMessage} showIcon /> : null}
          <ActionAlerts
            message={alerts.message}
            requestId={alerts.requestId}
            errorMessage={alerts.errorMessage}
            errorRequestId={alerts.errorRequestId}
          />

          <Form<LoginFormValues> form={form} layout="vertical" onFinish={onFinish}>
            <Form.Item label="用户名" name="username" rules={[{ required: true, message: "请输入用户名" }]}>
              <Input placeholder="请输入用户名" autoComplete="username" />
            </Form.Item>

            <Form.Item label="密码" name="password" rules={[{ required: true, message: "请输入密码" }]}>
              <Input.Password placeholder="请输入密码" autoComplete="current-password" />
            </Form.Item>

            <Button type="primary" htmlType="submit" block loading={loading}>
              登录
            </Button>
          </Form>
        </Space>
      </Card>
    </div>
  );
}
