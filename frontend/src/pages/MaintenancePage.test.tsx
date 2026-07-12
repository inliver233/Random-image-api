import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MaintenancePage } from "./MaintenancePage";

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

type FixtureMode = "fallback" | "ready";

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function fixtureFor(url: string, mode: FixtureMode): Response {
  if (url.includes("/admin/api/maintenance/image-edge")) {
    return json({
      ok: true,
      enabled_flag: mode === "ready",
      ready: mode === "ready",
      base_urls: mode === "ready" ? ["https://img.example.com"] : [],
      base_url_count: mode === "ready" ? 1 : 0,
      sign_ttl_seconds: 604800,
      has_secret: mode === "ready",
      has_secret_previous: false,
      missing: mode === "ready" ? [] : ["IMAGE_EDGE_ENABLED", "IMAGE_EDGE_BASE_URLS", "IMAGE_EDGE_SECRET"],
      request_id: "req_edge",
    });
  }
  if (url.includes("/admin/api/maintenance/cf-api-proxy")) {
    return json({
      ok: true,
      enabled_flag: mode === "ready",
      ready: mode === "ready",
      base_urls: mode === "ready" ? ["https://api-proxy.example.com"] : [],
      base_url_count: mode === "ready" ? 1 : 0,
      has_secret: mode === "ready",
      missing: mode === "ready" ? [] : ["CF_API_PROXY_ENABLED", "CF_API_PROXY_BASE_URLS"],
      request_id: "req_cf",
    });
  }
  if (url.includes("/admin/api/cf-workers/probe")) {
    return json({
      ok: true,
      probed: true,
      kind: "all",
      timeout_s: 3,
      api: {
        results:
          mode === "ready"
            ? [
                {
                  base_url: "https://api-proxy.example.com",
                  ok: true,
                  status_code: 200,
                  secret_configured: true,
                  latency_ms: 12,
                },
              ]
            : [
                {
                  base_url: "https://api-nosecret.example.com",
                  ok: false,
                  status_code: 200,
                  secret_configured: false,
                  error: "secret_not_configured",
                },
              ],
        summary: mode === "ready" ? { total: 1, ok: 1, fail: 0 } : { total: 1, ok: 0, fail: 1 },
      },
      image: {
        results: mode === "ready" ? [{ base_url: "https://img.example.com", ok: true, status_code: 200 }] : [],
        summary: mode === "ready" ? { total: 1, ok: 1, fail: 0 } : { total: 0, ok: 0, fail: 0 },
      },
      note: "Probe never enables flags",
      request_id: "req_probe",
    });
  }
  if (url.includes("/admin/api/cf-workers/register")) {
    return json({
      ok: true,
      kind: "api",
      base_url: "https://api-rt.example.workers.dev",
      runtime_base_urls: ["https://api-rt.example.workers.dev"],
      registered: true,
      request_id: "req_register",
    });
  }
  if (url.includes("/admin/api/cf-workers/unregister")) {
    return json({
      ok: true,
      kind: "api",
      base_url: "https://api-rt.example.workers.dev",
      runtime_base_urls: [],
      unregistered: true,
      request_id: "req_unregister",
    });
  }
  if (url.includes("/admin/api/cf-workers/pool")) {
    return json({
      ok: true,
      api: {
        env_base_urls: mode === "ready" ? ["https://api-proxy.example.com"] : [],
        runtime_base_urls: [],
        merged_base_urls: mode === "ready" ? ["https://api-proxy.example.com"] : [],
        members:
          mode === "ready"
            ? [{ kind: "api", base_url: "https://api-proxy.example.com", source: "env" }]
            : [],
      },
      image: {
        env_base_urls: mode === "ready" ? ["https://img.example.com"] : [],
        runtime_base_urls: [],
        merged_base_urls: mode === "ready" ? ["https://img.example.com"] : [],
        members:
          mode === "ready" ? [{ kind: "image", base_url: "https://img.example.com", source: "env" }] : [],
      },
      egress_policy: {
        residential_egress_emergency_only: true,
        cf_api_proxy_ready: mode === "ready",
        image_edge_ready: mode === "ready",
        pixiv_api_allows_residential_when_cf_ready: mode !== "ready",
        image_origin_allows_residential_when_edge_ready: mode !== "ready",
      },
      note: "Deploy/register does not flip flags",
      request_id: "req_pool",
    });
  }
  if (url.includes("/admin/api/maintenance/r2-prewarm")) {
    if (mode === "ready") {
      return json({
        ok: true,
        enabled_flag: true,
        ready: true,
        url_configured: true,
        secret_configured: true,
        payload_shape: "paths",
        url_preview: "https://prewarm.example.com",
        missing: [],
        request_id: "req_r2",
      });
    }
    return json({
      ok: true,
      enabled_flag: true,
      ready: false,
      url_configured: true,
      secret_configured: false,
      payload_shape: "paths",
      url_preview: "https://prewarm.example.com",
      missing: ["R2_PREWARM_SECRET|IMAGE_EDGE_SECRET"],
      request_id: "req_r2",
    });
  }
  if (url.includes("/admin/api/maintenance/api-key-rate-limit")) {
    return json({
      ok: true,
      required: false,
      rpm: 60,
      burst: 10,
      configured_backend: mode === "fallback" ? "redis" : "memory",
      active_backend: "memory",
      redis_url_configured: mode === "fallback",
      using_memory_fallback: mode === "fallback",
      request_id: "req_rl",
    });
  }
  if (url.includes("/admin/api/maintenance/modular-ports")) {
    if (mode === "fallback") {
      return json({
        ok: true,
        catalog: { backend: "sqlite" },
        tags: { backend: "sqlite" },
        job_queue: {
          backend: "sqlite",
          requested: "sqlite",
          implemented: true,
        },
        recent_dedup: {
          configured_backend: "redis",
          active_backend: "memory",
          using_memory_fallback: true,
        },
        random_service: { backend: "default" },
        random_pick: { backend: "sqlite" },
        request_id: "req_ports",
      });
    }
    return json({
      ok: true,
      catalog: { backend: "sqlite" },
      tags: { backend: "sqlite" },
      job_queue: {
        backend: "memory",
        requested: "memory",
        implemented: true,
      },
      recent_dedup: {
        configured_backend: "memory",
        active_backend: "memory",
        using_memory_fallback: false,
      },
      random_service: { backend: "default" },
      random_pick: { backend: "sqlite" },
      request_id: "req_ports",
    });
  }
  if (url.includes("/admin/api/maintenance/random-engine/compare-filters")) {
    return json({
      ok: true,
      match: true,
      cardinality_match: true,
      python_filtered: 42,
      engine_filtered: 42,
      delta: 0,
      engine_index_size: 100,
      engine_revision: "rev-compare",
      r18_strict: 1,
      pick_probe: {
        seed: "compare-filters-probe-v1",
        strategy: "random",
        engine_status: "OK",
        engine_image_id: 7,
        in_catalog: true,
        python_quality_score: 1.2345,
        ok: true,
        detail: "engine_id_in_catalog",
      },
      request_id: "req_compare",
    });
  }
  if (url.includes("/admin/api/maintenance/random-engine")) {
    if (mode === "fallback") {
      return json({
        ok: true,
        enabled: true,
        url: "http://127.0.0.1:18080",
        traffic_percent: 100,
        timeout_ms: 50,
        healthy: true,
        health: { index_size: 0, snapshot_revision: "rev0" },
        index_size: 0,
        index_empty: true,
        ready_for_traffic: false,
        cutover_warning: "engine index empty — push snapshot before cutover",
        circuit: {
          state: "closed",
          consecutive_failures: 0,
          open_remaining_s: 0,
          failure_threshold: 5,
          open_s: 30,
        },
        request_id: "req_engine",
      });
    }
    return json({
      ok: true,
      enabled: false,
      url: "",
      traffic_percent: 0,
      timeout_ms: 50,
      healthy: false,
      health: null,
      index_size: null,
      index_empty: null,
      ready_for_traffic: false,
      cutover_warning: null,
      circuit: {
        state: "closed",
        consecutive_failures: 0,
        open_remaining_s: 0,
        failure_threshold: 5,
        open_s: 30,
      },
      request_id: "req_engine",
    });
  }
  return json({ ok: false, code: "NOT_FOUND", message: "not found", request_id: "req_x", details: {} }, 404);
}

function stubFetch(mode: FixtureMode) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => fixtureFor(String(input), mode)),
  );
}

/** Expand a collapsed Ant Design Collapse panel by its header label. */
async function expandCollapse(label: string | RegExp) {
  const header = await screen.findByRole("button", { name: label });
  if (header.getAttribute("aria-expanded") !== "true") {
    fireEvent.click(header);
  }
}

describe("MaintenancePage", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    stubFetch("fallback");
  });

  it("renders honesty cards with redis→memory fallbacks and R2 missing secret", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <MaintenancePage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("维护工具")).toBeInTheDocument();

    await expandCollapse(/模块端口 \/ API Key 限流/);
    await expandCollapse(/R2 Prewarm/);
    await expandCollapse(/CF Worker 池（成员 \+ 探针）/);

    await waitFor(() => {
      expect(screen.getAllByText("redis→memory fallback").length).toBeGreaterThanOrEqual(2);
    });
    // modular ports + API key RL both show redis→memory; job queue is fail-loud sqlite (no nats→sqlite badge)
    expect(screen.queryByText(/→sqlite fallback/)).not.toBeInTheDocument();
    expect(screen.getByText("implemented")).toBeInTheDocument();
    expect(screen.getByText("R2_PREWARM_SECRET|IMAGE_EDGE_SECRET")).toBeInTheDocument();
    expect(screen.getByText("缺失（R2_PREWARM_SECRET / IMAGE_EDGE_SECRET）")).toBeInTheDocument();
    expect(screen.getByText("paths（Worker 契约）")).toBeInTheDocument();
    // Engine empty-index cutover ZH
    expect(screen.getByText("双跑切流风险")).toBeInTheDocument();
    expect(screen.getByText("Engine 索引为空 — 切流前请先推送快照")).toBeInTheDocument();
    expect(screen.getByText("empty")).toBeInTheDocument();
    // Dual-run circuit honesty (row + readiness tags both show circuit=closed)
    expect(screen.getAllByText("circuit=closed").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("failures=0")).toBeInTheDocument();
    // CF pool card (empty pool + residential demotion honesty)
    expect(screen.getByRole("button", { name: "探针全部 healthz" })).toBeInTheDocument();
    expect(screen.getAllByText("（空）").length).toBeGreaterThanOrEqual(1);
  });

  it("renders ready modular ports and R2 when fully configured", async () => {
    stubFetch("ready");
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <MaintenancePage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("维护工具")).toBeInTheDocument();
    await expandCollapse(/模块端口 \/ API Key 限流/);
    await expandCollapse(/R2 Prewarm/);
    await expandCollapse(/CF Worker 池（成员 \+ 探针）/);
    await waitFor(() => {
      expect(screen.getByText("implemented")).toBeInTheDocument();
    });
    expect(screen.queryByText("redis→memory fallback")).not.toBeInTheDocument();
    expect(screen.getByText("active=memory (SQLite storage)")).toBeInTheDocument();
    // R2 ready tag (multiple "ready" tags exist across cards)
    expect(screen.getAllByText("ready").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("双跑切流风险")).not.toBeInTheDocument();
    // CF pool shows merged bases when ready (URLs also appear on Image Edge / CF API cards)
    expect(screen.getAllByText("https://api-proxy.example.com").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("https://img.example.com").length).toBeGreaterThanOrEqual(1);
  });

  it("probes CF pool healthz and shows summary", async () => {
    stubFetch("ready");
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <MaintenancePage />
      </QueryClientProvider>,
    );

    await expandCollapse(/CF Worker 池（成员 \+ 探针）/);
    const probeBtn = await screen.findByRole("button", { name: "探针全部 healthz" });
    fireEvent.click(probeBtn);

    await waitFor(() => {
      expect(screen.getByText(/探针完成：api ok=1\/1；image ok=1\/1/)).toBeInTheDocument();
    });
    expect(screen.getByText(/最近探针 API/)).toBeInTheDocument();
    expect(screen.getByText(/api-proxy\.example\.com ok/)).toBeInTheDocument();
  });

  it("surfaces secret_not_configured on probe fail rows", async () => {
    // fallback fixture returns api probe row with secret_configured:false
    stubFetch("fallback");
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <MaintenancePage />
      </QueryClientProvider>,
    );

    await expandCollapse(/CF Worker 池（成员 \+ 探针）/);
    fireEvent.click(await screen.findByRole("button", { name: "探针全部 healthz" }));

    await waitFor(() => {
      expect(screen.getByText(/fail\(secret_not_configured\)/)).toBeInTheDocument();
    });
  });

  it("registers a runtime CF pool base from the form", async () => {
    stubFetch("ready");
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <MaintenancePage />
      </QueryClientProvider>,
    );

    await expandCollapse(/CF Worker 池（成员 \+ 探针）/);
    expect(screen.getByRole("button", { name: "注册进池" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "注销 runtime" })).toBeInTheDocument();

    const baseInput = screen.getByPlaceholderText("https://ria-api-a.example.workers.dev");
    fireEvent.change(baseInput, { target: { value: "https://api-rt.example.workers.dev" } });
    fireEvent.click(screen.getByRole("button", { name: "注册进池" }));

    await waitFor(() => {
      expect(
        screen.getByText(/已注册 api → https:\/\/api-rt\.example\.workers\.dev/),
      ).toBeInTheDocument();
    });
  });

  it("surfaces pick_probe on compare-filters success", async () => {
    const qc = makeClient();
    render(
      <QueryClientProvider client={qc}>
        <MaintenancePage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("维护工具")).toBeInTheDocument();
    const compareBtn = await screen.findByRole("button", {
      name: "对比过滤（基数 + pick 探针，默认 r18=0）",
    });
    expect(compareBtn).not.toBeDisabled();
    fireEvent.click(compareBtn);

    await waitFor(() => {
      expect(screen.getByText("总体一致：基数一致 python=42 engine=42；pick_probe ok id=7")).toBeInTheDocument();
    });
    expect(screen.getByText("match（基数∧探针）")).toBeInTheDocument();
    expect(screen.getByText("基数一致")).toBeInTheDocument();
    expect(screen.getByText("id=7")).toBeInTheDocument();
    expect(screen.getByText("engine_id_in_catalog")).toBeInTheDocument();
    expect(screen.getByText("py_quality=1.2345")).toBeInTheDocument();
  });
});
