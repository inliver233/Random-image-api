import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ProxiesPage } from "./ProxiesPage";

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("ProxiesPage", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    let endpointsCalls = 0;
    let endpointEnabled = true;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/admin/api/proxy-pools")) {
          return new Response(
            JSON.stringify({
              ok: true,
              items: [{ id: "1", name: "pixiv", description: null, enabled: true }],
              request_id: "req_pools_1",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.endsWith("/admin/api/proxies/endpoints")) {
          endpointsCalls += 1;
          return new Response(
            JSON.stringify({
              ok: true,
              items: [
                {
                  id: "1",
                  uri_masked: "http://***:***@1.2.3.4:8080",
                  source: "manual",
                  source_ref: null,
                  enabled: endpointEnabled,
                  latency_ms: 123,
                  status: "ok",
                  blacklisted_until: null,
                  last_error: null,
                  success_count: 10,
                  failure_count: 2,
                  last_ok_at: "2026-02-13T00:00:00Z",
                  last_fail_at: null,
                  pools: [{ id: "1", name: "pixiv", pool_enabled: true, member_enabled: true, weight: 1 }],
                  bindings: { primary_count: 1, override_count: 0 },
                },
              ],
              request_id: `req_proxies_${endpointsCalls}`,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.endsWith("/admin/api/proxies/endpoints/1") && init?.method === "PUT") {
          const body = init?.body ? JSON.parse(String(init.body)) : {};
          endpointEnabled = Boolean(body.enabled);
          return new Response(JSON.stringify({ ok: true, endpoint_id: "1", enabled: endpointEnabled, request_id: "req_toggle" }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (url.endsWith("/admin/api/proxies/endpoints/1/reset-failures") && init?.method === "POST") {
          return new Response(JSON.stringify({ ok: true, endpoint_id: "1", request_id: "req_reset" }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (url.endsWith("/admin/api/proxies/endpoints/import")) {
          expect(init?.method).toBe("POST");
          const body = init?.body ? JSON.parse(String(init.body)) : {};
          expect(body.conflict_policy).toBe("overwrite");
          expect(String(body.text || "")).toContain("http://u:pa@ss@1.2.3.4:8080");
          expect(String(body.text || "")).toContain("socks5://5.6.7.8:1080");
          return new Response(JSON.stringify({ ok: true, created: 2, updated: 0, skipped: 0, errors: [], request_id: "req_manual" }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (url.endsWith("/admin/api/proxies/easy-proxies/import")) {
          expect(init?.method).toBe("POST");
          const body = init?.body ? JSON.parse(String(init.body)) : {};
          expect(body.base_url).toBe("http://easy.test");
          expect(body.password).toBe("pw_test");
          expect(body.conflict_policy).toBe("skip_non_easy_proxies");
          expect(body.attach_pool_id).toBe(1);
          expect(body.attach_weight).toBe(1);
          expect(body.recompute_bindings).toBe(true);
          expect(body.max_tokens_per_proxy).toBe(2);
          expect(body.strict).toBe(false);
          return new Response(
            JSON.stringify({ ok: true, created: 1, updated: 0, skipped: 0, errors: [], request_id: "req_easy" }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.endsWith("/admin/api/proxies/probe")) {
          expect(init?.method).toBe("POST");
          return new Response(JSON.stringify({ ok: true, job_id: "job_1", request_id: "req_probe" }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response(JSON.stringify({ ok: false, code: "NOT_FOUND", message: "not found", request_id: "req_x", details: {} }), {
          status: 404,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
  });

  it("renders list", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <ProxiesPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("代理管理")).toBeInTheDocument();
    expect(await screen.findByText("http://***:***@1.2.3.4:8080")).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_proxies_1/)).toBeInTheDocument();
  });

  it("imports from easy-proxies", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <ProxiesPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("代理管理")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("http://easy-proxies:15666"), { target: { value: "http://easy.test" } });
    fireEvent.change(screen.getByPlaceholderText("可选"), { target: { value: "pw_test" } });
    fireEvent.click(screen.getByRole("button", { name: "开始导入" }));

    expect(await screen.findByText("外部代理服务导入完成")).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_easy/)).toBeInTheDocument();
  });

  it("imports manual endpoints", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <ProxiesPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("代理管理")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("http://user:pass@1.2.3.4:8080"), {
      target: { value: "http://u:pa@ss@1.2.3.4:8080\nsocks5://5.6.7.8:1080\n" },
    });

    const form = document.querySelector("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form as HTMLFormElement);

    expect(await screen.findByText("手动导入完成")).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_manual/)).toBeInTheDocument();
  });

  it("enqueues proxy probe job", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <ProxiesPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("代理管理")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "启动健康探测任务" }));

    expect(await screen.findByText("探测任务已入队")).toBeInTheDocument();
    expect(await screen.findByText(/任务ID:\s*job_1/)).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_probe/)).toBeInTheDocument();
  });

  it("toggles endpoint enabled", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <ProxiesPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("代理管理")).toBeInTheDocument();
    expect(await screen.findByText("http://***:***@1.2.3.4:8080")).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: /禁\s*用/ }));
    expect(await screen.findByText(/代理节点已禁用：1/)).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_toggle/)).toBeInTheDocument();
    expect(await screen.findByText("否")).toBeInTheDocument();
  });

  it("resets endpoint failures", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <ProxiesPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("代理管理")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "解除拉黑" }));

    expect(await screen.findByText(/代理节点已重置失败并解除拉黑：1/)).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_reset/)).toBeInTheDocument();
  });
});
