import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuditPage } from "./AuditPage";

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("AuditPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/admin/api/audit?limit=50")) {
          return new Response(
            JSON.stringify({
              ok: true,
              items: [
                {
                  id: "a1",
                  created_at: "2026-02-11T00:00:00Z",
                  actor: "admin",
                  action: "CREATE",
                  resource: "token",
                  record_id: "1",
                  request_id: "req_1",
                  detail_json: { k: "v" },
                },
              ],
              next_cursor: "",
              request_id: "req_audit",
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
        <AuditPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("审计日志")).toBeInTheDocument();
    expect(await screen.findByText("token")).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_audit/)).toBeInTheDocument();
  });
});
