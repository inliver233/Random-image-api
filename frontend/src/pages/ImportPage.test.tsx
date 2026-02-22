import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ImportPage } from "./ImportPage";

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("ImportPage", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/admin/api/imports")) {
          const headers = new Headers(init?.headers || {});
          const body = init?.body;
          if (body instanceof FormData) {
            expect(headers.get("Content-Type")).toBeNull();
            expect(body.get("file")).toBeInstanceOf(File);
            expect(String(body.get("source"))).toBe("manual");
          } else if (body) {
            expect(headers.get("Content-Type")).toBe("application/json");
          }
          return new Response(
            JSON.stringify({
              ok: true,
              import_id: "",
              job_id: "",
              accepted: 1,
              deduped: 0,
              errors: [],
              preview: [],
              request_id: "req_import",
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

  it("renders", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/admin/import"]}>
          <Routes>
            <Route path="/admin/import" element={<ImportPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("导入图片链接")).toBeInTheDocument();
    expect(await screen.findByText("支持的链接格式（重要）")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /开始导入/ })).toBeInTheDocument();
    expect(screen.getByPlaceholderText("每行一个链接")).toBeInTheDocument();

    const switches = screen.getAllByRole("switch");
    expect(switches).toHaveLength(2);
    expect(switches[0]).toHaveAttribute("aria-checked", "false");
    expect(switches[1]).toHaveAttribute("aria-checked", "true");
  });

  it("submits import and shows request_id", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/admin/import"]}>
          <Routes>
            <Route path="/admin/import" element={<ImportPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    fireEvent.change(screen.getByPlaceholderText("每行一个链接"), { target: { value: "https://example.com/1" } });
    fireEvent.click(screen.getByRole("button", { name: /开始导入/ }));

    expect(await screen.findByText(/请求ID:\s*req_import/)).toBeInTheDocument();
    expect(await screen.findByText(/接收:\s*1/)).toBeInTheDocument();

    await waitFor(() => {
      const fetchMock = globalThis.fetch as unknown as { mock: { calls: unknown[][] } };
      expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith("/admin/api/imports"))).toBe(true);
    });
  });

  it("submits import with file upload (FormData)", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/admin/import"]}>
          <Routes>
            <Route path="/admin/import" element={<ImportPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const fileInput = screen.getByTestId("import-file-input") as HTMLInputElement;
    const file = new File(["https://example.com/1\n"], "urls.txt", { type: "text/plain" });
    fireEvent.change(fileInput, { target: { files: [file] } });

    fireEvent.click(screen.getByRole("button", { name: /开始导入/ }));

    expect(await screen.findByText(/请求ID:\s*req_import/)).toBeInTheDocument();

    await waitFor(() => {
      const fetchMock = globalThis.fetch as unknown as { mock: { calls: unknown[][] } };
      expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith("/admin/api/imports"))).toBe(true);
    });
  });
});
