import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BindingsPage } from "./BindingsPage";

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("BindingsPage", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    let listCalls = 0;
    let overrideProxyId: string | null = null;
    let overrideExpiresAt: string | null = null;
    let recomputeCalls = 0;

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/admin/api/bindings/recompute")) {
          recomputeCalls += 1;
          expect(init?.method).toBe("POST");
          const body = init?.body ? JSON.parse(String(init.body)) : {};
          expect(body.pool_id).toBe(1);
          expect(body.max_tokens_per_proxy).toBe(2);

          const strict = body.strict !== undefined ? Boolean(body.strict) : true;
          if (strict) {
            return new Response(
              JSON.stringify({
                ok: false,
                code: "BAD_REQUEST",
                message: "代理容量不足（请增加节点或调高单代理最多绑定令牌数）",
                request_id: `req_capacity_${recomputeCalls}`,
                details: { token_count: 5, proxy_count: 1, max_tokens_per_proxy: 2, weight_sum: 1, capacity: 2 },
              }),
              { status: 400, headers: { "Content-Type": "application/json" } },
            );
          }

          return new Response(
            JSON.stringify({
              ok: true,
              pool_id: "1",
              recomputed: 5,
              strict: false,
              over_capacity_assigned: 3,
              capacity: 2,
              token_count: 5,
              proxy_count: 1,
              max_tokens_per_proxy: 2,
              request_id: `req_recompute_${recomputeCalls}`,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.endsWith("/admin/api/bindings?pool_id=1")) {
          listCalls += 1;
          return new Response(
            JSON.stringify({
              ok: true,
              items: [
                {
                  id: "1",
                  token: { id: "1", label: "acc1" },
                  pool: { id: "1", name: "pixiv" },
                  primary_proxy: { id: "10", scheme: "http", host: "1.2.3.4", port: 8080, username: "u1" },
                  override_proxy: overrideProxyId
                    ? { id: overrideProxyId, scheme: "http", host: "9.9.9.9", port: 8080, username: "" }
                    : null,
                  override_expires_at: overrideExpiresAt,
                  effective_proxy_id: overrideProxyId ? overrideProxyId : "10",
                  effective_mode: overrideProxyId ? "override" : "primary",
                },
              ],
              request_id: `req_bindings_${listCalls}`,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.endsWith("/admin/api/bindings/1/override")) {
          expect(init?.method).toBe("POST");
          const body = init?.body ? JSON.parse(String(init.body)) : {};
          expect(body.override_proxy_id).toBe(11);
          expect(body.ttl_ms).toBe(30 * 60 * 1000);
          expect(body.reason).toBe("test");
          overrideProxyId = "11";
          overrideExpiresAt = "2026-02-15T00:00:00Z";
          return new Response(
            JSON.stringify({
              ok: true,
              binding_id: "1",
              override_proxy_id: "11",
              override_expires_at: overrideExpiresAt,
              request_id: "req_override",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.endsWith("/admin/api/bindings/1/clear-override")) {
          expect(init?.method).toBe("POST");
          overrideProxyId = null;
          overrideExpiresAt = null;
          return new Response(JSON.stringify({ ok: true, binding_id: "1", request_id: "req_clear" }), {
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
      <MemoryRouter initialEntries={["/admin/bindings?pool_id=1"]}>
        <QueryClientProvider client={qc}>
          <BindingsPage />
        </QueryClientProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByText("令牌与代理绑定")).toBeInTheDocument();
    expect(await screen.findByText("acc1（#1）")).toBeInTheDocument();
    expect(await screen.findByText("pixiv（#1）")).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_bindings_1/)).toBeInTheDocument();
  });

  it("sets and clears override", async () => {
    const qc = makeClient();
    render(
      <MemoryRouter initialEntries={["/admin/bindings?pool_id=1"]}>
        <QueryClientProvider client={qc}>
          <BindingsPage />
        </QueryClientProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByText("令牌与代理绑定")).toBeInTheDocument();
    expect(await screen.findByText("acc1（#1）")).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "设置覆盖" }));
    const dialog = await screen.findByRole("dialog", { name: /设置覆盖代理/ });
    fireEvent.change(within(dialog).getByPlaceholderText("例如：10"), { target: { value: "11" } });
    fireEvent.change(within(dialog).getByPlaceholderText("例如：60"), { target: { value: "30" } });
    fireEvent.change(within(dialog).getByPlaceholderText("例如：临时切换节点排查问题"), { target: { value: "test" } });

    const form = dialog.querySelector("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form as HTMLFormElement);

    expect(await screen.findByText(/已设置覆盖代理：绑定 #1 → 节点 #11/)).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_override/)).toBeInTheDocument();
    expect((await screen.findAllByText(/9\.9\.9\.9:8080/)).length).toBeGreaterThan(0);

    fireEvent.click(await screen.findByRole("button", { name: "清除覆盖" }));
    expect(await screen.findByText(/已清除覆盖代理：绑定 #1/)).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_clear/)).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.queryAllByText(/9\.9\.9\.9:8080/)).toHaveLength(0);
    });
  });

  it("continues recompute when proxy capacity is insufficient", async () => {
    const qc = makeClient();
    render(
      <MemoryRouter initialEntries={["/admin/bindings?pool_id=1"]}>
        <QueryClientProvider client={qc}>
          <BindingsPage />
        </QueryClientProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByText("令牌与代理绑定")).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "重新计算绑定" }));
    expect(await screen.findByText("重新计算绑定失败")).toBeInTheDocument();
    expect(await screen.findByText(/令牌数=5，代理数=1，单代理上限=2/)).toBeInTheDocument();
    expect(await screen.findByText(/权重和=1/)).toBeInTheDocument();
    expect(await screen.findByText(/总容量=2/)).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_capacity_1/)).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "继续计算（允许超出容量）" }));
    expect(await screen.findByText("重新计算绑定完成")).toBeInTheDocument();
    expect(await screen.findByText(/重算数量:\s*5/)).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_recompute_2/)).toBeInTheDocument();
  });
});
