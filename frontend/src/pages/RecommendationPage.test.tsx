import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RecommendationPage } from "./RecommendationPage";

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("RecommendationPage", () => {
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
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/admin/api/settings") && init?.method === "PUT") {
          const body = init.body ? JSON.parse(String(init.body)) : {};
          expect(body.settings.random.strategy).toBe("quality");
          expect(body.settings.random.quality_samples).toBe(12);
          expect(body.settings.random.dedup.enabled).toBe(true);
          expect(body.settings.random.dedup.window_s).toBe(1200);
          expect(body.settings.random.dedup.max_images).toBe(5000);
          expect(body.settings.random.dedup.max_authors).toBe(2000);
          expect(body.settings.random.dedup.strict).toBe(false);
          expect(body.settings.random.dedup.image_penalty).toBe(8);
          expect(body.settings.random.dedup.author_penalty).toBe(2.5);
          expect(body.settings.random.recommendation.pick_mode).toBe("weighted");
          expect(body.settings.random.recommendation.temperature).toBe(1);
          expect(body.settings.random.recommendation.freshness_half_life_days).toBe(21);
          expect(body.settings.random.recommendation.velocity_smooth_days).toBe(2);
          expect(body.settings.random.recommendation.score_weights.bookmark).toBe(4);
          expect(body.settings.random.recommendation.score_weights.freshness).toBe(1);
          expect(body.settings.random.recommendation.score_weights.bookmark_velocity).toBe(1.2);
          expect(body.settings.random.recommendation.multipliers.ai).toBe(0.5);
          expect(body.settings.random.recommendation.multipliers.manga).toBe(0);
          return new Response(JSON.stringify({ ok: true, updated: 3, request_id: "req_save" }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (url.endsWith("/admin/api/settings")) {
          return new Response(
            JSON.stringify({
              ok: true,
              settings: {
                random: {
                  strategy: "quality",
                  quality_samples: 12,
                  dedup: {
                    enabled: true,
                    window_s: 1200,
                    max_images: 5000,
                    max_authors: 2000,
                    strict: false,
                    image_penalty: 8,
                    author_penalty: 2.5,
                  },
                  recommendation: {
                    pick_mode: "weighted",
                    temperature: 1,
                    freshness_half_life_days: 21,
                    velocity_smooth_days: 2,
                    score_weights: { bookmark: 4, view: 0.5, comment: 2, pixels: 1, bookmark_rate: 3, freshness: 1, bookmark_velocity: 1.2 },
                    multipliers: {
                      ai: 1,
                      non_ai: 1,
                      unknown_ai: 1,
                      illust: 1,
                      manga: 1,
                      ugoira: 1,
                      unknown_illust_type: 1,
                    },
                  },
                },
              },
              request_id: "req_settings",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.startsWith("/random?")) {
          expect(url).toContain("debug=1");
          return new Response(
            JSON.stringify({
              ok: true,
              request_id: "req_preview",
              data: {
                debug: { picked_by: "quality_weighted", engine_status: "skipped_circuit" },
                urls: { proxy: "/i/42.jpg" },
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

  it("renders", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <RecommendationPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("推荐策略")).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_settings/)).toBeInTheDocument();
  });

  it("saves and previews", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <RecommendationPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("推荐策略")).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_settings/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /AI 倍率设为 0.5/ }));
    fireEvent.click(screen.getByRole("button", { name: /漫画倍率设为 0/ }));
    fireEvent.click(screen.getByRole("button", { name: /保存推荐配置/ }));

    expect(await screen.findByText(/保存成功（更新条目数:\s*3）/)).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_save/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /预览一次随机结果/ }));
    expect(await screen.findByText(/请求ID:\s*req_preview/)).toBeInTheDocument();
    expect(await screen.findByText(/quality_weighted/)).toBeInTheDocument();
    // Preview always sends debug=1 so dual-run engine_status is visible.
    expect(await screen.findByText("双跑 engine_status:")).toBeInTheDocument();
    expect(await screen.findByText("skipped_circuit")).toBeInTheDocument();
    // Request + proxy links are browser-openable (absolute when VITE_API_BASE_URL set).
    expect(screen.getByRole("link", { name: /\/random\?/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /\/i\/42\.jpg/ })).toBeInTheDocument();
  });

  it("appends api_key on preview link when debug key is set", async () => {
    const { setPublicDebugApiKey } = await import("../auth/publicApiKeyStorage");
    setPublicDebugApiKey("pk_rec_preview");

    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <RecommendationPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("推荐策略")).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_settings/)).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /预览一次随机结果/ }));
    expect(await screen.findByText(/请求ID:\s*req_preview/)).toBeInTheDocument();
    const reqLink = screen.getByRole("link", { name: /\/random\?.*api_key=pk_rec_preview/ });
    expect(reqLink.getAttribute("href") || "").toContain("api_key=pk_rec_preview");
    const proxyLink = screen.getByRole("link", { name: /\/i\/42\.jpg.*api_key=pk_rec_preview/ });
    expect(proxyLink.getAttribute("href") || "").toContain("/i/42.jpg");
    expect(proxyLink.getAttribute("href") || "").toContain("api_key=pk_rec_preview");

    const writeText = vi.fn(async (_text: string) => undefined);
    vi.stubGlobal("navigator", {
      ...navigator,
      clipboard: { writeText },
    });
    fireEvent.click(screen.getByRole("button", { name: "复制链接" }));
    expect(writeText).toHaveBeenCalled();
    expect(String(writeText.mock.calls[0]?.[0] || "")).toContain("api_key=pk_rec_preview");

    const openSpy = vi.fn();
    vi.stubGlobal("open", openSpy);
    fireEvent.click(screen.getByRole("button", { name: "新窗口打开" }));
    expect(openSpy).toHaveBeenCalled();
    expect(String(openSpy.mock.calls[0]?.[0] || "")).toContain("api_key=pk_rec_preview");
  });
});
