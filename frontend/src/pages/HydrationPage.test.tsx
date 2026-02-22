import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HydrationPage } from "./HydrationPage";

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("HydrationPage", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);

      if (url.endsWith("/admin/api/summary")) {
        return new Response(
          JSON.stringify({
            ok: true,
            counts: {
              hydration: {
                enabled_images_total: 10,
                missing: { tags: 7, geometry: 5, r18: 10, ai: 3, user: 2, title: 1, created_at: 4, popularity: 6 },
              },
              jobs: { counts: { pending: 2, running: 1, failed: 0 } },
              worker: { last_seen_at: "2026-02-14T00:00:00Z" },
            },
            request_id: "req_summary",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }

      if (url.includes("/admin/api/hydration-runs?") && (!init?.method || init.method === "GET")) {
        return new Response(
          JSON.stringify({
            ok: true,
            items: [
              {
                id: "1",
                type: "backfill",
                status: "pending",
                criteria: { missing: ["tags", "geometry"] },
                cursor: { image_id: 100 },
                total: null,
                processed: 5,
                success: 5,
                failed: 0,
                started_at: null,
                finished_at: null,
                last_error: null,
                created_at: "2026-02-14T00:00:00Z",
                updated_at: "2026-02-14T00:01:00Z",
                latest_job: {
                  id: "88",
                  status: "pending",
                  attempt: 0,
                  max_attempts: 3,
                  run_after: null,
                  last_error: null,
                  locked_by: null,
                  locked_at: null,
                  updated_at: "2026-02-14T00:01:00Z",
                },
              },
            ],
            next_cursor: "",
            request_id: "req_runs",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }

      if (url.endsWith("/admin/api/hydration-runs") && init?.method === "POST") {
        return new Response(
          JSON.stringify({ ok: true, hydration_run_id: "10", job_id: "99", request_id: "req_create" }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }

      if (url.endsWith("/admin/api/hydration-runs/manual") && init?.method === "POST") {
        return new Response(
          JSON.stringify({ ok: true, created: true, job_id: "123", illust_id: "112233", request_id: "req_manual" }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }

      if (url.endsWith("/admin/api/hydration-runs/1/pause") && init?.method === "POST") {
        return new Response(
          JSON.stringify({ ok: true, hydration_run_id: "1", status: "paused", job_status: "paused", request_id: "req_pause" }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }

      return new Response(
        JSON.stringify({ ok: false, code: "NOT_FOUND", message: "not found", request_id: "req_x", details: {} }),
        { status: 404, headers: { "Content-Type": "application/json" } },
      );
    });

    vi.stubGlobal("fetch", fetchMock);
  });

  it("renders hydration runs and worker queue summary", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/admin/hydration"]}>
          <Routes>
            <Route path="/admin/hydration" element={<HydrationPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("元数据补全管理")).toBeInTheDocument();
    expect(await screen.findByText("工作线程心跳: 2026-02-14T00:00:00Z")).toBeInTheDocument();
    expect(await screen.findByText("元数据覆盖率统计")).toBeInTheDocument();
    expect(await screen.findByText("标签（缺 7）")).toBeInTheDocument();
    expect(await screen.findByText("#88 等待中 0/3")).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_runs/)).toBeInTheDocument();
  });

  it("creates backfill run and manual hydrate job", async () => {
    const qc = makeClient();
    const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;

    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/admin/hydration"]}>
          <Routes>
            <Route path="/admin/hydration" element={<HydrationPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("元数据补全管理")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "创建全量补全任务" }));
    expect(await screen.findByText("全量补全任务已创建")).toBeInTheDocument();
    expect(await screen.findByText(/补全运行ID:\s*10/)).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("例如：12345678"), { target: { value: "112233" } });
    fireEvent.click(screen.getByRole("button", { name: "创建单图补全任务" }));

    expect(await screen.findByText("单图补全任务已创建")).toBeInTheDocument();
    expect(await screen.findByText(/任务ID:\s*123/)).toBeInTheDocument();

    expect(
      fetchMock.mock.calls.some((call: unknown[]) => {
        const url = String(call[0] ?? "");
        const init = (call[1] ?? {}) as RequestInit;
        if (!url.endsWith("/admin/api/hydration-runs/manual") || init.method !== "POST" || !init.body) {
          return false;
        }
        const body = JSON.parse(String(init.body));
        return Number(body.illust_id) === 112233;
      }),
    ).toBe(true);
  });
});
