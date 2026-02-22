import { useQuery } from "@tanstack/react-query";
import { Button, Layout, Menu, Popover, Space, Typography } from "antd";
import React from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

import { apiJson } from "../api/client";

type NavItem = { key: string; label: string; external?: boolean; href?: string };

const NAV_ITEMS: NavItem[] = [
  { key: "/admin", label: "主页" },
  { key: "docs", label: "使用文档", external: true, href: "/docs" },
  { key: "/admin/import", label: "导入链接" },
  { key: "/admin/images", label: "图片管理" },
  { key: "/admin/hydration", label: "补全管理" },
  { key: "/admin/jobs", label: "任务队列" },
  { key: "/admin/tokens", label: "令牌管理" },
  { key: "/admin/proxies", label: "代理节点" },
  { key: "/admin/proxy-pools", label: "代理池" },
  { key: "/admin/bindings", label: "绑定关系" },
  { key: "/admin/settings", label: "系统设置" },
  { key: "/admin/audit", label: "审计日志" },
  { key: "/admin/recommendation", label: "推荐策略" },
  { key: "/admin/random", label: "随机调试" },
];

function pickSelectedKey(pathname: string): string {
  const path = String(pathname || "").trim() || "/admin";
  const candidates = [...NAV_ITEMS].sort((a, b) => b.key.length - a.key.length);
  for (const item of candidates) {
    if (path === item.key) return item.key;
    if (path.startsWith(item.key + "/")) return item.key;
  }
  return "/admin";
}

type VersionResponse = {
  ok: true;
  version: string;
  build_time: string;
  git_commit: string;
  request_id: string;
};

export function AdminLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const isRoot = location.pathname === "/admin" || location.pathname === "/admin/";
  const selectedKey = pickSelectedKey(location.pathname);

  const version = useQuery({
    queryKey: ["public", "version"],
    queryFn: () => apiJson<VersionResponse>("/version"),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });

  const versionText = (() => {
    if (version.isLoading) return "版本: 加载中…";
    if (version.isError || !version.data) return "版本: （获取失败）";
    const commitRaw = String(version.data.git_commit || "").trim();
    const commitShort = commitRaw ? commitRaw.slice(0, 7) : "";
    const ver = String(version.data.version || "").trim() || "（未知）";
    return commitShort ? `版本: ${ver} (${commitShort})` : `版本: ${ver}`;
  })();

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Layout.Sider width={220} theme="light" style={{ borderRight: "1px solid #f0f0f0" }}>
        <div style={{ padding: "16px 16px 8px" }}>
          <Typography.Title level={5} style={{ margin: 0, cursor: "pointer" }} onClick={() => navigate("/admin")}>
            随机图片管理后台
          </Typography.Title>
          <Typography.Text type="secondary">一站式管理面板</Typography.Text>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selectedKey]}
          items={NAV_ITEMS.map((it) => ({ key: it.key, label: it.label }))}
          onClick={(e) => {
            const key = String(e.key || "");
            const item = NAV_ITEMS.find((x) => x.key === key);
            if (item?.external && item.href) {
              window.location.assign(String(item.href));
              return;
            }
            navigate(key);
          }}
        />
      </Layout.Sider>

      <Layout>
        <Layout.Header style={{ background: "#fff", padding: "0 24px" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", height: 64 }}>
            <Space align="center">
              {!isRoot ? (
                <Button size="small" onClick={() => navigate(-1)}>
                  返回
                </Button>
              ) : null}
              <Button size="small" onClick={() => navigate("/admin")}>
                主页
              </Button>
            </Space>

            <Space align="center">
              <Typography.Text type="secondary">{versionText}</Typography.Text>
              <Popover
                title="升级提示"
                content={
                  <div style={{ maxWidth: 420 }}>
                    <div>如果你更新了代码但页面还是旧的，通常是因为没有重建镜像。</div>
                    <div>
                      请在部署目录执行：<Typography.Text code>docker compose up -d --build</Typography.Text>
                    </div>
                    <div style={{ marginTop: 8 }}>
                      {version.data?.request_id ? (
                        <Typography.Text type="secondary">请求ID: {version.data.request_id}</Typography.Text>
                      ) : null}
                    </div>
                  </div>
                }
              >
                <Button size="small">升级提示</Button>
              </Popover>
            </Space>
          </div>
        </Layout.Header>
        <Layout.Content style={{ padding: 24 }}>
          <Outlet />
        </Layout.Content>
      </Layout>
    </Layout>
  );
}
