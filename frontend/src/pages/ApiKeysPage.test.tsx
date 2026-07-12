import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getPublicDebugApiKey } from "../auth/publicApiKeyStorage";
import { ApiKeysPage } from "./ApiKeysPage";

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("ApiKeysPage", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    try {
      sessionStorage.clear();
    } catch {
      // ignore
    }
  });

  beforeEach(() => {
    let listCalls = 0;
    let keyEnabled = true;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/admin/api/api-keys") && init?.method === "POST") {
          const body = init?.body ? JSON.parse(String(init.body)) : {};
          expect(body.name).toBe("public-bot");
          expect(String(body.api_key).length).toBeGreaterThanOrEqual(20);
          return new Response(
            JSON.stringify({ ok: true, api_key_id: "9", hint: "…abcd", request_id: "req_create_key" }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.includes("/admin/api/api-keys/1") && init?.method === "PUT") {
          const body = init?.body ? JSON.parse(String(init.body)) : {};
          keyEnabled = Boolean(body.enabled);
          return new Response(JSON.stringify({ ok: true, api_key_id: "1", request_id: "req_toggle_key" }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (url.includes("/admin/api/api-keys") && (!init?.method || init.method === "GET")) {
          listCalls += 1;
          return new Response(
            JSON.stringify({
              ok: true,
              items: [
                {
                  id: "1",
                  name: "demo-key",
                  description: "for bots",
                  enabled: keyEnabled,
                  hint: "…wxyz",
                  created_at: "2026-07-01T00:00:00Z",
                  updated_at: "2026-07-01T00:00:00Z",
                  last_used_at: null,
                },
              ],
              next_cursor: "",
              request_id: `req_keys_${listCalls}`,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response(
          JSON.stringify({
            ok: false,
            code: "NOT_FOUND",
            message: "not found",
            request_id: "req_x",
            details: {},
          }),
          { status: 404, headers: { "Content-Type": "application/json" } },
        );
      }),
    );
  });

  it("renders list", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <ApiKeysPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("API Keys")).toBeInTheDocument();
    expect(await screen.findByText("demo-key")).toBeInTheDocument();
    expect(await screen.findByText("for bots")).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_keys_1/)).toBeInTheDocument();
  });

  it("creates api key", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <ApiKeysPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("demo-key")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "创建 API Key" }));
    const dialog = await screen.findByRole("dialog", { name: /创建 API Key/ });
    fireEvent.change(within(dialog).getByPlaceholderText("例如：public-bot"), {
      target: { value: "public-bot" },
    });
    fireEvent.change(within(dialog).getByPlaceholderText("至少 20 字符的随机密钥"), {
      target: { value: "abcdefghijklmnopqrstuvwxyz12" },
    });

    const form = dialog.querySelector("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form as HTMLFormElement);

    expect(await screen.findByText(/API Key 已创建：#9/)).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_create_key/)).toBeInTheDocument();
    // Plaintext is only available at create time — store for Playground/public debug use.
    expect(getPublicDebugApiKey()).toBe("abcdefghijklmnopqrstuvwxyz12");
    expect(await screen.findByText(/已写入调试密钥/)).toBeInTheDocument();
  });

  it("toggles enabled", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <ApiKeysPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("demo-key")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /禁\s*用/ }));
    expect(await screen.findByText(/API Key #1 已禁用/)).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_toggle_key/)).toBeInTheDocument();
  });
});
