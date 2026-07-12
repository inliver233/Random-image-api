/**
 * Random-image-api — Cloudflare Worker API egress pool.
 *
 * Purpose (ds2api-style + hardened):
 *   - Give hydrate / OAuth / App API diverse CF edge egress IPs
 *   - Keep residential proxies as optional fallback only
 *   - Never open-proxy: host allowlist + required shared secret (fail closed)
 *
 * Contract:
 *   ANY https://{worker}/p/{host}/{path}?query
 *   host ∈ ALLOWED_HOSTS (exact match, no port)
 *   Required: header X-Proxy-Secret must match non-empty PROXY_SECRET
 *
 * Design notes:
 *   - Strip CF / forwarding headers that leak the client IP
 *   - Do not cache authenticated API responses
 *   - Multi-deploy the same script under different names for egress diversity;
 *     backend sticky-picks via CF_API_PROXY_BASE_URLS
 */

import {
  authorizeSecret,
  buildUpstreamHeaderPairs,
  hostAllowed,
  parseAllowedHosts,
  parseProxyPath,
  parseRateLimitConfig,
  takeRateLimitToken,
} from "./pure.js";

/** Per-isolate token bucket (resets on cold start; multi-POP multiplies capacity). */
let _rateBucket = null;

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,HEAD,POST,PUT,PATCH,DELETE,OPTIONS",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Max-Age": "86400",
  };
}

function jsonError(status, message, extra = {}) {
  return new Response(JSON.stringify({ ok: false, message, ...extra }), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      ...corsHeaders(),
    },
  });
}

function authorize(request, env) {
  const expected = String(env.PROXY_SECRET || "").trim();
  const got = String(request.headers.get("X-Proxy-Secret") || "").trim();
  return authorizeSecret(expected, got);
}

function buildUpstreamHeaders(request, host) {
  const headers = new Headers();
  for (const [k, v] of buildUpstreamHeaderPairs(request.headers.entries(), host)) {
    headers.set(k, v);
  }
  return headers;
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }

    const url = new URL(request.url);
    const allowed = parseAllowedHosts(env);

    const rateCfg = parseRateLimitConfig(env);

    if (url.pathname === "/healthz" || url.pathname === "/") {
      return new Response(
        JSON.stringify({
          ok: true,
          service: "random-image-api-proxy",
          allowed_hosts: allowed,
          // Always true when secret env empty too — Worker is fail-closed.
          secret_required: true,
          secret_configured: Boolean(String(env.PROXY_SECRET || "").trim()),
          rate_limit: rateCfg.enabled
            ? { enabled: true, rpm: rateCfg.rpm, burst: rateCfg.burst }
            : { enabled: false },
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json; charset=utf-8", ...corsHeaders() },
        },
      );
    }

    if (!authorize(request, env)) {
      return jsonError(403, "Forbidden");
    }

    if (rateCfg.enabled) {
      const decision = takeRateLimitToken(_rateBucket, rateCfg, Date.now());
      _rateBucket = decision.bucket;
      if (!decision.allow) {
        return new Response(JSON.stringify({ ok: false, message: "Rate limited" }), {
          status: 429,
          headers: {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "no-store",
            "Retry-After": String(decision.retryAfterS || 1),
            ...corsHeaders(),
          },
        });
      }
    }

    const parsed = parseProxyPath(url);
    if (!parsed) {
      return jsonError(400, "Bad path; use /p/{host}/{path}");
    }
    if (!hostAllowed(parsed.host, allowed)) {
      return jsonError(403, "Host not allowed", { host: parsed.host });
    }

    const targetURL = `https://${parsed.host}${parsed.pathWithQuery}`;
    const headers = buildUpstreamHeaders(request, parsed.host);

    const init = {
      method: request.method,
      headers,
      redirect: "follow",
    };
    // GET/HEAD must not send a body.
    if (request.method !== "GET" && request.method !== "HEAD") {
      init.body = request.body;
    }

    let upstream;
    try {
      upstream = await fetch(targetURL, init);
    } catch {
      // One retry for cold POP / transient network blip.
      try {
        upstream = await fetch(targetURL, init);
      } catch {
        return jsonError(502, "Upstream fetch failed");
      }
    }

    const outHeaders = new Headers(upstream.headers);
    outHeaders.set("Access-Control-Allow-Origin", "*");
    outHeaders.set("X-Proxied-By", "random-image-api-proxy");
    outHeaders.set("X-Proxy-Host", parsed.host);
    // Never let browsers cache authenticated API responses via this edge.
    outHeaders.set("Cache-Control", "no-store");
    outHeaders.delete("set-cookie");

    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: outHeaders,
    });
  },
};
