import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    try {
      sessionStorage.clear();
    } catch {
      // ignore
    }
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
            JSON.stringify({
              ok: true,
              request_id: "req_play",
              data: {
                urls: { proxy: "/i/1.jpg" },
                debug: { engine_status: "skipped_circuit" },
              },
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
    // JSON mode always sends debug=1 so dual-run engine_status is visible.
    expect(await screen.findByText("双跑 engine_status:")).toBeInTheDocument();
    expect(await screen.findByText("skipped_circuit")).toBeInTheDocument();
    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    expect(fetchMock).toHaveBeenCalled();
    const calledUrl = String(fetchMock.mock.calls[0]?.[0] || "");
    expect(calledUrl).toContain("debug=1");
  });

  it("sends X-API-Key header when debug key is set", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/random") && url.includes("format=json")) {
        const headers = new Headers(init?.headers || {});
        expect(headers.get("X-API-Key")).toBe("debug-public-key-1234567890");
        expect(url).toContain("debug=1");
        return new Response(
          JSON.stringify({
            ok: true,
            request_id: "req_play_key",
            data: { urls: { proxy: "/i/1.jpg" }, debug: { engine_status: "ok" } },
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
        <MemoryRouter initialEntries={["/admin/random"]}>
          <Routes>
            <Route path="/admin/random" element={<PlaygroundPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("随机接口调试")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("留空=不发送 X-API-Key"), {
      target: { value: "debug-public-key-1234567890" },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始请求" }));
    expect(await screen.findByText(/请求ID:\s*req_play_key/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalled();
    // Displayed request + proxy links must be browser-openable when key required.
    const reqLink = await screen.findByRole("link", { name: /format=json.*api_key=debug-public-key-1234567890|api_key=debug-public-key-1234567890.*format=json/ });
    expect(reqLink.getAttribute("href") || "").toContain("api_key=debug-public-key-1234567890");
    const proxyLink = await screen.findByRole("link", { name: /\/i\/1\.jpg.*api_key=debug-public-key-1234567890/ });
    expect(proxyLink.getAttribute("href") || "").toContain("/i/1.jpg");
    expect(proxyLink.getAttribute("href") || "").toContain("api_key=debug-public-key-1234567890");

    const writeText = vi.fn(async (_text: string) => undefined);
    vi.stubGlobal("navigator", {
      ...navigator,
      clipboard: { writeText },
    });
    fireEvent.click(screen.getByRole("button", { name: "复制链接" }));
    await waitFor(() => {
      expect(writeText).toHaveBeenCalled();
    });
    const copied = String(writeText.mock.calls[0]?.[0] || "");
    expect(copied).toContain("api_key=debug-public-key-1234567890");

    writeText.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "复制代理链接" }));
    await waitFor(() => {
      expect(writeText).toHaveBeenCalled();
    });
    expect(String(writeText.mock.calls[0]?.[0] || "")).toContain("/i/1.jpg");
    expect(String(writeText.mock.calls[0]?.[0] || "")).toContain("api_key=debug-public-key-1234567890");
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

  it("makes redirect Location openable with public api_key", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/random") && (url.includes("redirect=1") || init?.redirect === "manual")) {
        return new Response(null, {
          status: 302,
          headers: {
            Location: "/i/9.jpg",
            "x-request-id": "req_redirect",
          },
        });
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
        <MemoryRouter initialEntries={["/admin/random?format=redirect"]}>
          <Routes>
            <Route path="/admin/random" element={<PlaygroundPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("随机接口调试")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("留空=不发送 X-API-Key"), {
      target: { value: "debug-public-key-1234567890" },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始请求" }));

    expect(await screen.findByText(/请求ID:\s*req_redirect/)).toBeInTheDocument();
    const locLink = await screen.findByRole("link", { name: /\/i\/9\.jpg.*api_key=debug-public-key-1234567890/ });
    expect(locLink.getAttribute("href") || "").toContain("/i/9.jpg");
    expect(locLink.getAttribute("href") || "").toContain("api_key=debug-public-key-1234567890");

    const writeText = vi.fn(async (_text: string) => undefined);
    vi.stubGlobal("navigator", {
      ...navigator,
      clipboard: { writeText },
    });
    fireEvent.click(screen.getByRole("button", { name: "复制跳转地址" }));
    await waitFor(() => {
      expect(writeText).toHaveBeenCalled();
    });
    expect(String(writeText.mock.calls[0]?.[0] || "")).toContain("/i/9.jpg");
    expect(String(writeText.mock.calls[0]?.[0] || "")).toContain("api_key=debug-public-key-1234567890");

    const openSpy = vi.fn();
    vi.stubGlobal("open", openSpy);
    fireEvent.click(screen.getByRole("button", { name: "打开跳转地址" }));
    expect(openSpy).toHaveBeenCalled();
    expect(String(openSpy.mock.calls[0]?.[0] || "")).toContain("api_key=debug-public-key-1234567890");
  });
});
