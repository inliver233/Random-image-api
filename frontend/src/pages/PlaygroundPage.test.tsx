import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PlaygroundPage } from "./PlaygroundPage";

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("PlaygroundPage", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/random") && url.includes("format=image")) {
          return new Response(
            JSON.stringify({
              ok: false,
              code: "NO_MATCH",
              message: "No matching image.",
              request_id: "req_nomatch",
              details: {
                hints: {
                  applied_filters: { r18: 0, r18_strict: 1, orientation: "any", min_width: 0, min_height: 0, min_pixels: 0 },
                  suggestions: ["运行元数据补全任务以提升元数据覆盖率"],
                },
              },
            }),
            { status: 404, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.includes("/random") && url.includes("format=json")) {
          return new Response(
            JSON.stringify({ ok: true, request_id: "req_play", data: { urls: { proxy: "/i/1.jpg" } } }),
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

  it("runs and shows request_id", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/admin/random"]}>
          <Routes>
            <Route path="/admin/random" element={<PlaygroundPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("随机接口调试")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "开始请求" }));
    expect(await screen.findByText(/请求ID:\s*req_play/)).toBeInTheDocument();
  });

  it("shows NO_MATCH hints in image mode and keeps message Chinese", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/admin/random?format=image"]}>
          <Routes>
            <Route path="/admin/random" element={<PlaygroundPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("随机接口调试")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "开始请求" }));

    expect(await screen.findByText("没有匹配的图片")).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_nomatch/)).toBeInTheDocument();
    expect(await screen.findByText("运行元数据补全任务以提升元数据覆盖率")).toBeInTheDocument();
    expect(await screen.findByText("本次筛选条件：")).toBeInTheDocument();
    expect(await screen.findByText(/r18_strict/)).toBeInTheDocument();
  });
});
