import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardPage } from "./DashboardPage";
import { ImagesPage } from "./ImagesPage";
import { ImportPage } from "./ImportPage";
import { JobsPage } from "./JobsPage";
import { PlaygroundPage } from "./PlaygroundPage";
import { ProxiesPage } from "./ProxiesPage";
import { TokensPage } from "./TokensPage";

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("DashboardPage", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/version")) {
          return new Response(
            JSON.stringify({
              ok: true,
              version: "dev",
              build_time: "2026-02-15T00:00:00Z",
              git_commit: "abcdef1",
              request_id: "req_version",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.endsWith("/admin/api/settings")) {
          return new Response(
            JSON.stringify({
              ok: true,
              settings: {
                proxy: { enabled: false, fail_closed: true, route_mode: "pixiv_only", allowlist_domains: [] },
                random: {},
                security: { hide_origin_url_in_public_json: true },
                rate_limit: {},
              },
              request_id: "req_settings",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.endsWith("/admin/api/summary")) {
          return new Response(
            JSON.stringify({
              ok: true,
              counts: {
                images: { total: 14, enabled: 14 },
                tokens: { total: 2, enabled: 1 },
                proxies: { endpoints_total: 1, endpoints_enabled: 1 },
                proxy_pools: { total: 0, enabled: 0 },
                bindings: { total: 0 },
                jobs: { counts: { pending: 2, running: 0, failed: 3 } },
                worker: { last_seen_at: "2026-02-13T00:00:00Z" },
              },
              request_id: "req_summary",
            }),
            {
              status: 200,
              headers: { "Content-Type": "application/json" },
            },
          );
        }
        if (url.endsWith("/admin/api/stats/random")) {
          return new Response(
            JSON.stringify({
              ok: true,
              stats: {
                total_requests: 12,
                total_ok: 10,
                total_error: 2,
                in_flight: 0,
                window_seconds: 60,
                last_window_requests: 3,
                last_window_ok: 3,
                last_window_error: 0,
                last_window_success_rate: 1.0,
              },
              request_id: "req_stats",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.includes("/admin/api/jobs?status=failed")) {
          return new Response(
            JSON.stringify({
              ok: true,
              items: [
                {
                  id: "101",
                  type: "hydrate_metadata",
                  status: "failed",
                  last_error: "upstream 403 on illust 1",
                  updated_at: "2026-02-13T01:00:00Z",
                  attempt: 3,
                  max_attempts: 3,
                },
                {
                  id: "99",
                  type: "import_images",
                  status: "failed",
                  last_error: "sqlite busy",
                  updated_at: "2026-02-13T00:30:00Z",
                  attempt: 1,
                  max_attempts: 5,
                },
                {
                  id: "88",
                  type: "proxy_probe",
                  status: "failed",
                  last_error: null,
                  updated_at: "2026-02-12T23:00:00Z",
                  attempt: 2,
                  max_attempts: 3,
                },
              ],
              next_cursor: "",
              request_id: "req_jobs",
            }),
            {
              status: 200,
              headers: { "Content-Type": "application/json" },
            },
          );
        }
        if (url.endsWith("/admin/api/hydration-runs")) {
          return new Response(JSON.stringify({ ok: true, hydration_run_id: "10", job_id: "99", request_id: "req_hyd" }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (url.endsWith("/admin/api/maintenance/modular-ports")) {
          return new Response(
            JSON.stringify({
              ok: true,
              catalog: { backend: "sqlite" },
              tags: { backend: "sqlite" },
              job_queue: {
                backend: "sqlite",
                requested: "sqlite",
                implemented: true,
              },
              recent_dedup: {
                configured_backend: "redis",
                active_backend: "memory",
                using_memory_fallback: true,
              },
              random_service: { backend: "default" },
              random_pick: { backend: "sqlite" },
              request_id: "req_modular",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.endsWith("/admin/api/maintenance/image-edge")) {
          return new Response(
            JSON.stringify({
              ok: true,
              enabled_flag: true,
              ready: false,
              base_url_count: 0,
              has_secret: false,
              request_id: "req_edge",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.endsWith("/admin/api/maintenance/random-engine")) {
          return new Response(
            JSON.stringify({
              ok: true,
              enabled: true,
              traffic_percent: 0,
              healthy: false,
              index_empty: true,
              ready_for_traffic: false,
              cutover_warning: "traffic_percent=0 (engine not receiving picks)",
              circuit: {
                state: "open",
                consecutive_failures: 5,
                open_remaining_s: 12.4,
                failure_threshold: 5,
                open_s: 30,
              },
              request_id: "req_engine",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.endsWith("/admin/api/maintenance/cf-api-proxy")) {
          return new Response(
            JSON.stringify({
              ok: true,
              enabled_flag: false,
              ready: false,
              base_url_count: 0,
              has_secret: false,
              request_id: "req_cf",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.endsWith("/admin/api/maintenance/r2-prewarm")) {
          return new Response(
            JSON.stringify({
              ok: true,
              enabled_flag: true,
              ready: false,
              url_configured: true,
              secret_configured: false,
              request_id: "req_r2",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.endsWith("/admin/api/maintenance/api-key-rate-limit")) {
          return new Response(
            JSON.stringify({
              ok: true,
              required: true,
              rpm: 60,
              burst: 10,
              configured_backend: "redis",
              active_backend: "memory",
              redis_url_configured: false,
              using_memory_fallback: true,
              request_id: "req_rl",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response(JSON.stringify({ ok: false, code: "NOT_FOUND", message: "not found", request_id: "req_x", details: {} }), {
          status: 404,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
  });

  function renderDashboard() {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/admin"]}>
          <Routes>
            <Route path="/admin" element={<DashboardPage />} />
            <Route path="/admin/import" element={<ImportPage />} />
            <Route path="/admin/tokens" element={<TokensPage />} />
            <Route path="/admin/proxies" element={<ProxiesPage />} />
            <Route path="/admin/jobs" element={<JobsPage />} />
            <Route path="/admin/images" element={<ImagesPage />} />
            <Route path="/admin/random" element={<PlaygroundPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it("renders fetched counts", async () => {
    renderDashboard();

    expect(await screen.findByText("工作线程心跳: 2026-02-13T00:00:00Z")).toBeInTheDocument();
    expect(await screen.findByText("总数: 14")).toBeInTheDocument();
    expect(await screen.findByText("总数: 2")).toBeInTheDocument();
    expect(await screen.findByText("总请求: 12")).toBeInTheDocument();
    expect(await screen.findByText("近 1 分钟成功率: 100.0%")).toBeInTheDocument();
    expect(await screen.findByText("补全代理节点: 1/1 启用")).toBeInTheDocument();
    expect(await screen.findByText(/提交:\s*abcdef1/)).toBeInTheDocument();
    expect(await screen.findByText("数量: 3")).toBeInTheDocument();
    expect(await screen.findByText(/#101\s*·\s*hydrate_metadata/)).toBeInTheDocument();
    expect(await screen.findByText("upstream 403 on illust 1")).toBeInTheDocument();
    expect(await screen.findByText("模块端口 / 边缘切流（Phase 4）")).toBeInTheDocument();
    expect(await screen.findByText(/catalog=sqlite/)).toBeInTheDocument();
    expect(await screen.findByText(/job_queue=sqlite/)).toBeInTheDocument();
    expect((await screen.findAllByText("redis→memory fallback")).length).toBeGreaterThanOrEqual(2);
    expect(await screen.findByText(/api_key_rl=memory/)).toBeInTheDocument();
    expect(await screen.findByText("api_key required")).toBeInTheDocument();
    expect(await screen.findByText(/image_edge=flag-on-not-ready/)).toBeInTheDocument();
    expect(await screen.findByText(/cf_api_proxy=off/)).toBeInTheDocument();
    expect(await screen.findByText(/r2_prewarm=flag-on-not-ready/)).toBeInTheDocument();
    expect(await screen.findByText(/engine=enabled-not-ready/)).toBeInTheDocument();
    expect(await screen.findByText("engine index empty")).toBeInTheDocument();
    expect(await screen.findByText(/circuit=open\s*~13s/)).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:.*req_modular/)).toBeInTheDocument();
  });

  it("navigates to import", async () => {
    renderDashboard();

    expect(await screen.findByText("工作线程 / 队列")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /去导入链接/ }));
    expect(await screen.findByText("导入图片链接")).toBeInTheDocument();
  });

  it("creates hydration run", async () => {
    renderDashboard();

    expect(await screen.findByText("工作线程 / 队列")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /创建补全任务/ }));
    expect(await screen.findByText("补全任务已创建")).toBeInTheDocument();
    expect(await screen.findByText(/补全运行ID:\s*10/)).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_hyd/)).toBeInTheDocument();
  });

  it("navigates to tokens", async () => {
    renderDashboard();

    expect(await screen.findByText("工作线程 / 队列")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /去添加令牌/ }));
    expect(await screen.findByRole("button", { name: /新增令牌/ })).toBeInTheDocument();
  });

  it("navigates to proxies", async () => {
    renderDashboard();

    expect(await screen.findByText("工作线程 / 队列")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /去添加代理/ }));
    expect(await screen.findByText("代理管理（仅 Hydrate / OAuth）")).toBeInTheDocument();
  });

  it("navigates to playground", async () => {
    renderDashboard();

    expect(await screen.findByText("工作线程 / 队列")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /打开随机测试/ }));
    expect(await screen.findByText("随机接口调试")).toBeInTheDocument();
  });
});

