import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setPublicDebugApiKey } from "../auth/publicApiKeyStorage";
import { AdminLayout } from "./AdminLayout";

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("AdminLayout external public links", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
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
        if (url.includes("/version")) {
          return new Response(
            JSON.stringify({
              ok: true,
              version: "test",
              build_time: "t",
              git_commit: "abcdef1",
              request_id: "req_v",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response(JSON.stringify({ ok: false }), {
          status: 404,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
  });

  it("opens /wtf via resolvePublicApiUrl and appends api_key when set", async () => {
    setPublicDebugApiKey("pk_admin_nav");
    const assign = vi.fn();
    vi.stubGlobal("location", {
      ...window.location,
      assign,
    });

    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/admin"]}>
          <AdminLayout />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("瀑布流")).toBeInTheDocument();
    fireEvent.click(screen.getByText("瀑布流"));
    expect(assign).toHaveBeenCalled();
    const target = String(assign.mock.calls[0]?.[0] || "");
    expect(target.includes("/wtf")).toBe(true);
    expect(target.includes("api_key=pk_admin_nav")).toBe(true);
  });
});
