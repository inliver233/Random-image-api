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
  });

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/admin/api/settings") && init?.method === "PUT") {
          const body = init.body ? JSON.parse(String(init.body)) : {};
          expect(body.settings.random.strategy).toBe("quality");
          expect(body.settings.random.quality_samples).toBe(5);
          expect(body.settings.random.recommendation.pick_mode).toBe("weighted");
          expect(body.settings.random.recommendation.temperature).toBe(1);
          expect(body.settings.random.recommendation.score_weights.bookmark).toBe(4);
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
                  quality_samples: 5,
                  recommendation: {
                    pick_mode: "weighted",
                    temperature: 1,
                    score_weights: { bookmark: 4, view: 0.5, comment: 2, pixels: 1, bookmark_rate: 3 },
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
          return new Response(
            JSON.stringify({
              ok: true,
              request_id: "req_preview",
              data: { debug: { picked_by: "quality_weighted" } },
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

    expect(await screen.findByText("保存成功")).toBeInTheDocument();
    expect(await screen.findByText(/更新条目数:\s*3/)).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_save/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /预览一次随机结果/ }));
    expect(await screen.findByText(/请求ID:\s*req_preview/)).toBeInTheDocument();
    expect(await screen.findByText(/quality_weighted/)).toBeInTheDocument();
  });
});
