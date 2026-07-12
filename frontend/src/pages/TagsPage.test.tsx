import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setPublicDebugApiKey } from "../auth/publicApiKeyStorage";
import { PlaygroundPage } from "./PlaygroundPage";
import { TagsPage } from "./TagsPage";

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("TagsPage", () => {
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
        if (url.includes("/tags?")) {
          return new Response(
            JSON.stringify({
              ok: true,
              items: [{ name: "tag1", translated_name: "t1", count_images: 5 }],
              next_cursor: "",
              request_id: "req_tags",
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
        <MemoryRouter initialEntries={["/admin/tags"]}>
          <Routes>
            <Route path="/admin/tags" element={<TagsPage />} />
            <Route path="/admin/random" element={<PlaygroundPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("标签列表")).toBeInTheDocument();
    expect(await screen.findByText("tag1")).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_tags/)).toBeInTheDocument();
  });

  it("sends X-API-Key when public debug key is stored", async () => {
    setPublicDebugApiKey("pk_test_tags");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/tags?")) {
        return new Response(
          JSON.stringify({
            ok: true,
            items: [{ name: "tag1", translated_name: "t1", count_images: 5 }],
            next_cursor: "",
            request_id: "req_tags",
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
        <MemoryRouter initialEntries={["/admin/tags"]}>
          <Routes>
            <Route path="/admin/tags" element={<TagsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("tag1")).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });
    const tagsCall = fetchMock.mock.calls.find(([input]) => String(input).includes("/tags?"));
    expect(tagsCall).toBeTruthy();
    const headers = new Headers(tagsCall?.[1]?.headers);
    expect(headers.get("X-API-Key")).toBe("pk_test_tags");
  });

  it("navigates to playground with included_tags prefill", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/admin/tags"]}>
          <Routes>
            <Route path="/admin/tags" element={<TagsPage />} />
            <Route path="/admin/random" element={<PlaygroundPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("tag1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /按此标签随机一张/ }));
    expect(await screen.findByText("随机接口调试")).toBeInTheDocument();

    await waitFor(() => {
      const input = screen.getByLabelText(/包含标签/) as HTMLInputElement;
      expect(input.value).toBe("tag1");
    });
  });
});
