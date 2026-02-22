import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ProxyPoolsPage } from "./ProxyPoolsPage";

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("ProxyPoolsPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/admin/api/proxy-pools") && (!init || init.method === "GET" || !init.method)) {
          return new Response(
            JSON.stringify({
              ok: true,
              items: [{ id: "1", name: "默认代理池", description: null, enabled: true }],
              request_id: "req_pools",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.endsWith("/admin/api/proxies/endpoints")) {
          return new Response(
            JSON.stringify({
              ok: true,
              items: [
                {
                  id: "10",
                  uri_masked: "http://1.2.3.4:8080",
                  enabled: true,
                  pools: [{ id: "1", name: "默认代理池", pool_enabled: true, member_enabled: true, weight: 2 }],
                },
              ],
              request_id: "req_eps",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.endsWith("/admin/api/proxy-pools/1/endpoints") && init?.method === "POST") {
          return new Response(
            JSON.stringify({
              ok: true,
              pool_id: "1",
              created: 0,
              updated: 1,
              removed: 0,
              request_id: "req_set",
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

  it("renders pools and saves endpoint membership", async () => {
    const qc = makeClient();
    render(
      <MemoryRouter>
        <QueryClientProvider client={qc}>
          <ProxyPoolsPage />
        </QueryClientProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByText("代理池管理")).toBeInTheDocument();
    expect(await screen.findByText("默认代理池")).toBeInTheDocument();

    fireEvent.click(await screen.findByTestId("pool-config-1"));
    expect(await screen.findByText(/配置代理池节点/)).toBeInTheDocument();

    fireEvent.click(await screen.findByTestId("pool-endpoints-save"));

    await waitFor(() => {
      const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls;
      const matched = calls.find((c) => String(c[0]).endsWith("/admin/api/proxy-pools/1/endpoints"));
      expect(matched).toBeTruthy();
      const init = matched?.[1] as RequestInit | undefined;
      expect(init?.method).toBe("POST");
      expect(String(init?.body)).toContain("\"endpoint_id\":10");
      expect(String(init?.body)).toContain("\"weight\":2");
    });
  });
});

