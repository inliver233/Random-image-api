import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import React, { useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { AdminLayout } from "./AdminLayout";
import { getAdminToken } from "../auth/tokenStorage";
import { DashboardPage } from "../pages/DashboardPage";
import { ImportPage } from "../pages/ImportPage";
import { ImportDetailPage } from "../pages/ImportDetailPage";
import { ImagesPage } from "../pages/ImagesPage";
import { LoginPage } from "../pages/LoginPage";
import { PlaygroundPage } from "../pages/PlaygroundPage";
import { TagsPage } from "../pages/TagsPage";
import { AuthorsPage } from "../pages/AuthorsPage";
import { TokensPage } from "../pages/TokensPage";
import { ProxiesPage } from "../pages/ProxiesPage";
import { ProxyPoolsPage } from "../pages/ProxyPoolsPage";
import { BindingsPage } from "../pages/BindingsPage";
import { JobsPage } from "../pages/JobsPage";
import { HydrationPage } from "../pages/HydrationPage";
import { SettingsPage } from "../pages/SettingsPage";
import { AuditPage } from "../pages/AuditPage";
import { RecommendationPage } from "../pages/RecommendationPage";
import { PlaceholderPage } from "../pages/PlaceholderPage";

function buildNextParam(location: { pathname: string; search: string }): string {
  const next = `${location.pathname}${location.search || ""}`;
  return encodeURIComponent(next);
}

function AriaI18nPatches() {
  useEffect(() => {
    if (typeof document === "undefined") return;

    const translations: Record<string, string> = {
      "Increase Value": "增加数值",
      "Decrease Value": "减少数值",
    };

    const applyOnce = () => {
      for (const [from, to] of Object.entries(translations)) {
        document.querySelectorAll(`[aria-label="${from}"]`).forEach((node) => {
          node.setAttribute("aria-label", to);
        });
      }
    };

    applyOnce();

    if (typeof MutationObserver === "undefined") return;
    const observer = new MutationObserver(() => applyOnce());
    observer.observe(document.body, { subtree: true, childList: true });
    return () => observer.disconnect();
  }, []);

  return null;
}

function RequireAdminToken({ children }: { children: React.ReactElement }) {
  const location = useLocation();
  const token = getAdminToken();
  if (!token) {
    const next = buildNextParam(location);
    return <Navigate to={`/admin/login?reason=missing_token&next=${next}`} replace />;
  }
  return children;
}

function UnauthorizedListener() {
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    const handler = () => {
      if (location.pathname.startsWith("/admin/login")) return;
      const next = buildNextParam(location);
      navigate(`/admin/login?reason=unauthorized&next=${next}`, { replace: true });
    };

    window.addEventListener("admin:unauthorized", handler);
    return () => {
      window.removeEventListener("admin:unauthorized", handler);
    };
  }, [location, navigate]);

  return null;
}

export function App() {
  return (
    <ConfigProvider locale={zhCN}>
      <BrowserRouter>
        <AriaI18nPatches />
        <UnauthorizedListener />
        <Routes>
          <Route path="/" element={<Navigate to="/admin" replace />} />
          <Route path="/admin/login" element={<LoginPage />} />
          <Route
            path="/admin"
            element={
              <RequireAdminToken>
                <AdminLayout />
              </RequireAdminToken>
            }
          >
            <Route index element={<DashboardPage />} />
            <Route path="import" element={<ImportPage />} />
            <Route path="import/:id" element={<ImportDetailPage />} />
            <Route path="random" element={<PlaygroundPage />} />
            <Route path="recommendation" element={<RecommendationPage />} />
            <Route path="images" element={<ImagesPage />} />
            <Route path="tags" element={<TagsPage />} />
            <Route path="authors" element={<AuthorsPage />} />
            <Route path="tokens" element={<TokensPage />} />
            <Route path="proxies" element={<ProxiesPage />} />
            <Route path="proxy-pools" element={<ProxyPoolsPage />} />
            <Route path="bindings" element={<BindingsPage />} />
            <Route path="jobs" element={<JobsPage />} />
            <Route path="hydration" element={<HydrationPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="audit" element={<AuditPage />} />
            <Route path="*" element={<PlaceholderPage title="页面不存在" />} />
          </Route>
          <Route path="*" element={<PlaceholderPage title="页面不存在" />} />
        </Routes>
      </BrowserRouter>
    </ConfigProvider>
  );
}
