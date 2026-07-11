/**
 * Random-image-api — Cloudflare Worker API egress pool.
 *
 * Purpose (ds2api-style + hardened):
 *   - Give hydrate / OAuth / App API diverse CF edge egress IPs
 *   - Keep residential proxies as optional fallback only
 *   - Never open-proxy: host allowlist + optional shared secret
 *
 * Contract:
 *   ANY https://{worker}/p/{host}/{path}?query
 *   host ∈ ALLOWED_HOSTS (exact match, no port)
 *   Optional: header X-Proxy-Secret must match PROXY_SECRET when configured
 *
 * Design notes:
 *   - Strip CF / forwarding headers that leak the client IP
 *   - Do not cache authenticated API responses
 *   - Multi-deploy the same script under different names for egress diversity;
 *     backend sticky-picks via CF_API_PROXY_BASE_URLS
 */

const DEFAULT_ALLOWED = [
  "oauth.secure.pixiv.net",
  "app-api.pixiv.net",
  "public-api.secure.pixiv.net",
];

const STRIP_REQ_HEADERS = new Set([
  "cf-connecting-ip",
  "cf-ipcountry",
  "cf-ray",
  "cf-visitor",
  "cf-ew-via",
  "cf-worker",
  "x-forwarded-for",
  "x-forwarded-proto",
  "x-forwarded-host",
  "x-real-ip",
  "true-client-ip",
  "x-proxy-secret", // never forward our gate secret upstream
  "host",
  "connection",
  "content-length", // fetch recalculates
  "transfer-encoding",
]);

function parseAllowedHosts(env) {
  const raw = String(env.ALLOWED_HOSTS || "").trim();
  const out = [];
  const seen = new Set();
  const push = (h) => {
    const host = String(h || "")
      .trim()
      .toLowerCase()
      .replace(/^\.+|\.+$/g, "");
    if (!host || host.includes("/") || host.includes(":") || host.includes("@")) return;
    if (seen.has(host)) return;
    seen.add(host);
    out.push(host);
  };
  if (raw) {
    for (const part of raw.split(/[,;\s]+/)) push(part);
  }
  if (!out.length) {
    for (const h of DEFAULT_ALLOWED) push(h);
  }
  return out;
}

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

function timingSafeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function authorize(request, env) {
  const expected = String(env.PROXY_SECRET || "").trim();
  if (!expected) return true; // open within allowlist only (ops may rely on obscure worker URL)
  const got = String(request.headers.get("X-Proxy-Secret") || "").trim();
  return timingSafeEqual(got, expected);
}

/**
 * Parse /p/{host}/{path...}
 * Returns { host, pathWithQuery } or null.
 */
function parseProxyPath(url) {
  const pathname = url.pathname || "";
  // /p/host  or /p/host/rest
  const m = pathname.match(/^\/p\/([^/]+)(\/.*)?$/);
  if (!m) return null;
  const host = String(m[1] || "")
    .trim()
    .toLowerCase();
  if (!host) return null;
  const rest = m[2] || "/";
  const pathWithQuery = rest + (url.search || "");
  return { host, pathWithQuery };
}

function buildUpstreamHeaders(request, host) {
  const headers = new Headers();
  for (const [k, v] of request.headers.entries()) {
    const key = k.toLowerCase();
    if (STRIP_REQ_HEADERS.has(key)) continue;
    // Avoid leaking browser cookies from random clients if worker URL is guessed.
    if (key === "cookie") continue;
    headers.set(k, v);
  }
  headers.set("Host", host);
  return headers;
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }

    const url = new URL(request.url);
    const allowed = parseAllowedHosts(env);

    if (url.pathname === "/healthz" || url.pathname === "/") {
      return new Response(
        JSON.stringify({
          ok: true,
          service: "random-image-api-proxy",
          allowed_hosts: allowed,
          secret_required: Boolean(String(env.PROXY_SECRET || "").trim()),
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

    const parsed = parseProxyPath(url);
    if (!parsed) {
      return jsonError(400, "Bad path; use /p/{host}/{path}");
    }
    if (!allowed.includes(parsed.host)) {
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
