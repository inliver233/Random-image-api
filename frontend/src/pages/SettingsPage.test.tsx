import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsPage } from "./SettingsPage";

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("SettingsPage", () => {
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
          expect(body.settings.proxy.enabled).toBe(true);
          expect(body.settings.proxy.fail_closed).toBe(false);
          expect(body.settings.proxy.route_mode).toBe("pixiv_only");
          expect(body.settings.proxy.allowlist_domains).toEqual(["pixiv.net"]);
          expect(body.settings.proxy.default_pool_id).toBe("");
          expect(body.settings.image_proxy.use_pixiv_cat).toBe(false);
          expect(body.settings.image_proxy.pximg_mirror_host).toBe("i.pixiv.cat");
          expect(body.settings.image_proxy.extra_pximg_mirror_hosts).toEqual([]);
          expect(body.settings.random.default_attempts).toBe(3);
          expect(body.settings.random.default_r18_strict).toBe(true);
          expect(body.settings.random.fail_cooldown_ms).toBe(600000);
          expect(body.settings.random.strategy).toBe("quality");
          expect(body.settings.random.quality_samples).toBe(5);
          expect(body.settings.security.hide_origin_url_in_public_json).toBe(true);
          expect(body.settings.rate_limit.pixiv_hydrate_min_interval_ms).toBe(800);
          expect(body.settings.rate_limit.pixiv_hydrate_jitter_ms).toBe(200);
          return new Response(JSON.stringify({ ok: true, updated: 10, request_id: "req_save" }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (url.endsWith("/admin/api/settings")) {
          return new Response(
            JSON.stringify({
              ok: true,
              settings: {
                proxy: {
                  enabled: true,
                  fail_closed: false,
                  route_mode: "pixiv_only",
                  allowlist_domains: ["pixiv.net"],
                  default_pool_id: "",
                },
                image_proxy: { use_pixiv_cat: false, pximg_mirror_host: "i.pixiv.cat", extra_pximg_mirror_hosts: [] },
                random: { default_attempts: 3, default_r18_strict: true, fail_cooldown_ms: 600000, strategy: "quality", quality_samples: 5 },
                security: { hide_origin_url_in_public_json: true },
                rate_limit: {},
              },
              request_id: "req_settings",
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
        <SettingsPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("系统设置")).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_settings/)).toBeInTheDocument();
  });

  it("saves settings", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <SettingsPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("系统设置")).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_settings/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /保存设置/ }));

    expect(await screen.findByText("保存成功")).toBeInTheDocument();
    expect(await screen.findByText(/更新条目数:\s*10/)).toBeInTheDocument();
    expect(await screen.findByText(/请求ID:\s*req_save/)).toBeInTheDocument();
  });
});
