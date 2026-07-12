import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setPublicDebugApiKey } from "../auth/publicApiKeyStorage";
import { AuthorsPage } from "./AuthorsPage";

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("AuthorsPage", () => {
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
    try {
      sessionStorage.clear();
    } catch {
      // ignore
    }
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/authors?")) {
          return new Response(
            JSON.stringify({
              ok: true,
              items: [{ user_id: "9", user_name: "u", count_images: 12 }],
              next_cursor: "",
              request_id: "req_authors",
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

  it("renders list", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <AuthorsPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("作者列表")).toBeInTheDocument();
    expect(await screen.findByText("9")).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_authors/)).toBeInTheDocument();
  });

  it("sends X-API-Key when public debug key is stored", async () => {
    setPublicDebugApiKey("pk_test_authors");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/authors?")) {
        return new Response(
          JSON.stringify({
            ok: true,
            items: [{ user_id: "9", user_name: "u", count_images: 12 }],
            next_cursor: "",
            request_id: "req_authors",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(JSON.stringify({ ok: false, code: "NOT_FOUND", message: "not found", request_id: "req_x", details: {} }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <AuthorsPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("9")).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });
    const authorsCall = fetchMock.mock.calls.find(([input]) => String(input).includes("/authors?"));
    expect(authorsCall).toBeTruthy();
    const headers = new Headers(authorsCall?.[1]?.headers);
    expect(headers.get("X-API-Key")).toBe("pk_test_authors");
  });
});
