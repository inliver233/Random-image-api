import { Alert, Button, Card, Form, Input, Space, Typography } from "antd";
import React, { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { ApiError, apiJson } from "../api/client";
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
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const searchParams = new URLSearchParams(location.search);
  const reason = String(searchParams.get("reason") || "").trim();
  const next = String(searchParams.get("next") || "").trim();

  const reasonMessage =
    reason === "unauthorized"
      ? "登录已失效，请重新登录。"
      : reason === "missing_token"
        ? "请先登录后再访问管理后台。"
        : null;

  const onFinish = async (values: LoginFormValues) => {
    setLoading(true);
    setErrorMessage(null);
    setRequestId(null);

    try {
      const resp = await apiJson<LoginResponse>("/admin/api/login", {
        method: "POST",
        body: JSON.stringify({ username: values.username, password: values.password }),
      });

      setAdminToken(resp.token);
      setRequestId(resp.request_id);
      form.resetFields(["password"]);

      const nextPath = next && next.startsWith("/admin") && !next.startsWith("/admin/login") ? next : "/admin";
      navigate(nextPath, { replace: true });
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setErrorMessage(err.message);
        setRequestId(err.body?.request_id ? String(err.body.request_id) : null);
      } else if (err instanceof Error) {
        setErrorMessage(err.message);
      } else {
        setErrorMessage("登录失败");
      }
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
          {errorMessage ? <Alert type="error" message={errorMessage} showIcon /> : null}
          {requestId ? <Typography.Text type="secondary">请求ID: {requestId}</Typography.Text> : null}

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
