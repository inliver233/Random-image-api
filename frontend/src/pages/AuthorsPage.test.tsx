import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthorsPage } from "./AuthorsPage";

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("AuthorsPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/authors?limit=50")) {
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
});
