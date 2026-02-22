import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ImagesPage } from "./ImagesPage";

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("ImagesPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/admin/api/images?limit=50")) {
          return new Response(
            JSON.stringify({
              ok: true,
              items: [
                {
                  id: "1",
                  illust_id: "111",
                  page_index: 0,
                  ext: "jpg",
                  status: 1,
                  width: 100,
                  height: 200,
                  orientation: 1,
                  x_restrict: 0,
                  ai_type: 1,
                  illust_type: 0,
                  bookmark_count: 10,
                  view_count: 20,
                  comment_count: 3,
                  user: { id: "9", name: "u" },
                  title: "t",
                  created_at_pixiv: "2020-01-01T00:00:00Z",
                  original_url: "https://example.com/1.jpg",
                  proxy_path: "/i/1.jpg",
                  tag_count: 1,
                  missing: [],
                },
              ],
              next_cursor: "",
              request_id: "req_images",
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
        <ImagesPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("图片管理")).toBeInTheDocument();
    expect(await screen.findByText("111")).toBeInTheDocument();
    expect(await screen.findByText("N")).toBeInTheDocument();
    expect(await screen.findByText("Y")).toBeInTheDocument();
    expect(await screen.findByText("插画")).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_images/)).toBeInTheDocument();
  });
});
