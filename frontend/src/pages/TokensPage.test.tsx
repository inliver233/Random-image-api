import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TokensPage } from "./TokensPage";

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("TokensPage", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    let listCalls = 0;
    let tokenLabel: string | null = "acc1";
    let tokenEnabled = true;
    let tokenWeight = 1.0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/admin/api/tokens") && init?.method === "POST") {
          const body = init?.body ? JSON.parse(String(init.body)) : {};
          expect(body.refresh_token).toBe("rt_test");
          return new Response(JSON.stringify({ ok: true, token_id: "2", request_id: "req_create" }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (url.endsWith("/admin/api/tokens")) {
          listCalls += 1;
          return new Response(
            JSON.stringify({
              ok: true,
              items: [
                {
                  id: "1",
                  label: tokenLabel,
                  enabled: tokenEnabled,
                  refresh_token_masked: "***",
                  weight: tokenWeight,
                  error_count: 0,
                  backoff_until: null,
                  last_ok_at: null,
                  last_fail_at: null,
                  last_error_code: null,
                  last_error_msg: null,
                },
              ],
              request_id: `req_tokens_${listCalls}`,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.endsWith("/admin/api/tokens/1") && init?.method === "PUT") {
          const body = init?.body ? JSON.parse(String(init.body)) : {};
          if ("label" in body) tokenLabel = body.label ?? null;
          if ("enabled" in body) tokenEnabled = Boolean(body.enabled);
          if ("weight" in body) tokenWeight = Number(body.weight);
          return new Response(JSON.stringify({ ok: true, token_id: "1", request_id: "req_update" }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (url.endsWith("/admin/api/tokens/1/test-refresh")) {
          expect(init?.method).toBe("POST");
          return new Response(
            JSON.stringify({
              ok: true,
              expires_in: 123,
              user_id: "u1",
              proxy: { endpoint_id: "10", pool_id: "1" },
              request_id: "req_test_refresh",
            }),
            {
              status: 200,
              headers: { "Content-Type": "application/json" },
            },
          );
        }
        if (url.endsWith("/admin/api/tokens/1/reset-failures")) {
          expect(init?.method).toBe("POST");
          return new Response(JSON.stringify({ ok: true, token_id: "1", request_id: "req_reset" }), {
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
        <TokensPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("Pixiv 令牌管理")).toBeInTheDocument();
    expect(await screen.findByText("acc1")).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_tokens_1/)).toBeInTheDocument();
  });

  it("creates token", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <TokensPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("Pixiv 令牌管理")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /新增令牌/ }));
    const dialog = await screen.findByRole("dialog", { name: /新增令牌/ });
    fireEvent.change(within(dialog).getByPlaceholderText("必填"), { target: { value: "rt_test" } });

    const form = dialog.querySelector("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form as HTMLFormElement);

    expect(await screen.findByText(/令牌创建成功：2/)).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_create/)).toBeInTheDocument();
  });

  it("tests refresh and resets failures", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <TokensPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("Pixiv 令牌管理")).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: /测试刷新/ }));
    expect(await screen.findByText(/令牌刷新成功/)).toBeInTheDocument();
    expect(await screen.findByText(/代理 #10，代理池 #1/)).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_test_refresh/)).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: /重置失败计数/ }));
    expect(await screen.findByText(/已重置失败计数：1/)).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_reset/)).toBeInTheDocument();
  });

  it("updates token", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <TokensPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("Pixiv 令牌管理")).toBeInTheDocument();
    expect(await screen.findByText("acc1")).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: /编\s*辑/ }));
    const dialog = await screen.findByRole("dialog", { name: /编辑令牌/ });
    fireEvent.change(within(dialog).getByPlaceholderText("例如：主账号"), { target: { value: "acc2" } });

    const form = dialog.querySelector("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form as HTMLFormElement);

    expect(await screen.findByText(/令牌已更新：1/)).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_update/)).toBeInTheDocument();
    expect(await screen.findByText("acc2")).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: /禁\s*用/ }));
    expect(await screen.findByText(/令牌已更新：1/)).toBeInTheDocument();
    expect(await screen.findByText("否")).toBeInTheDocument();
  });
});
