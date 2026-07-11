/**
 * Random-image-api — Cloudflare Worker image edge.
 *
 * Public contract:
 *   GET|HEAD /u/{exp}/{sig}/{b64url(path)}
 *   where path is the original i.pximg.net path, e.g.
 *   /img-original/img/2020/01/01/00/00/00/12345_p0.jpg
 *   sig = base64url(HMAC-SHA256(secret, `${exp}\n${path}`))
 *
 * Flow:
 *   verify HMAC + expiry → Cache API (path-keyed) → fetch i.pximg.net
 *   → optional FALLBACK_MIRROR_HOST / FALLBACK_MIRROR_HOSTS chain → stream
 *
 * Design notes (ds2api + CF docs):
 * - Never accept raw target URLs (no open proxy).
 * - Strip client identity headers; never forward cookies/auth.
 * - Always send Referer: https://www.pixiv.net/
 * - Cache key ignores exp/sig so re-signed URLs share cache.
 */

const ALLOWED_PREFIXES = ["/img-original/", "/img-master/", "/img-/", "/c/"];
const ALLOWED_EXT = new Set(["jpg", "jpeg", "png", "gif", "webp"]);
const DEFAULT_ORIGIN = "i.pximg.net";
const REFERER = "https://www.pixiv.net/";
const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36";
const BUILTIN_MIRRORS = new Set(["i.pixiv.cat", "i.pixiv.re", "i.pixiv.nl"]);

function b64urlEncodeBytes(buf) {
  const bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  let binary = "";
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function b64urlDecodeToBytes(s) {
  const raw = String(s || "").replace(/-/g, "+").replace(/_/g, "/");
  const pad = raw.length % 4 === 0 ? "" : "=".repeat(4 - (raw.length % 4));
  const bin = atob(raw + pad);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function b64urlDecodeToUtf8(s) {
  return new TextDecoder().decode(b64urlDecodeToBytes(s));
}

function timingSafeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function hmacSign(secret, msg) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(msg));
  return b64urlEncodeBytes(sig);
}

function parseSignedPath(pathname) {
  // /u/{exp}/{sig}/{b64path}
  const m = String(pathname || "").match(/^\/u\/(\d+)\/([A-Za-z0-9_-]+)\/([A-Za-z0-9_-]+)$/);
  if (!m) return null;
  return { exp: Number(m[1]), sig: m[2], b64path: m[3] };
}

function validPath(path) {
  if (typeof path !== "string" || !path.startsWith("/") || path.includes("..") || path.includes("\\")) {
    return false;
  }
  if (path.includes("://") || path.includes("@") || path.includes("?")) return false;
  // Contract allowlist only — do not accept arbitrary /img-* beyond documented prefixes.
  const okPrefix = ALLOWED_PREFIXES.some((p) => path.startsWith(p));
  if (!okPrefix) return false;
  const ext = path.split(".").pop()?.toLowerCase() || "";
  return ALLOWED_EXT.has(ext);
}

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,HEAD,OPTIONS",
    "Access-Control-Max-Age": "86400",
  };
}

function jsonError(status, message, extraHeaders = {}) {
  return new Response(JSON.stringify({ ok: false, message }), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      ...corsHeaders(),
      ...extraHeaders,
    },
  });
}

function isAllowedMirrorHost(host) {
  const h = String(host || "").trim().toLowerCase();
  if (!h) return false;
  if (BUILTIN_MIRRORS.has(h)) return true;
  // Allow same-org worker emergency origins only (not open proxy).
  if (h.endsWith(".workers.dev")) return true;
  return false;
}

/** Ordered unique hosts from FALLBACK_MIRROR_HOSTS (csv) + legacy FALLBACK_MIRROR_HOST. */
function resolveFallbackHosts(env) {
  const out = [];
  const seen = new Set();
  const push = (raw) => {
    const host = String(raw || "").trim().toLowerCase();
    if (!host || seen.has(host) || !isAllowedMirrorHost(host)) return;
    seen.add(host);
    out.push(host);
  };
  for (const part of String(env.FALLBACK_MIRROR_HOSTS || "").split(",")) push(part);
  push(env.FALLBACK_MIRROR_HOST);
  return out;
}

function originFetchInit(env) {
  return {
    method: "GET",
    headers: {
      Referer: REFERER,
      "User-Agent": UA,
      Accept: "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    },
    redirect: "follow",
    cf: {
      cacheEverything: true,
      cacheTtlByStatus: {
        "200-299": Number(env.CACHE_TTL_SECONDS || 604800),
        "404": 60,
        "400-499": 30,
        "500-599": 0,
      },
    },
  };
}

async function fetchOrigin(path, env) {
  const originHost = String(env.ORIGIN_HOST || DEFAULT_ORIGIN).trim() || DEFAULT_ORIGIN;
  const url = `https://${originHost}${path}`;
  const init = originFetchInit(env);
  try {
    return await fetch(url, init);
  } catch {
    // One retry for transient network errors on cold POP / origin blip.
    return await fetch(url, init);
  }
}

async function fetchMirrorHost(path, host) {
  const url = `https://${host}${path}`;
  return fetch(url, {
    method: "GET",
    headers: {
      Referer: REFERER,
      "User-Agent": UA,
      Accept: "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    },
    redirect: "follow",
  });
}

/**
 * Primary origin, then ordered emergency mirrors. Returns { response, via }.
 * via is origin host or mirror host that produced HTTP 200.
 */
async function fetchUpstreamWithFallback(path, env) {
  const originHost = String(env.ORIGIN_HOST || DEFAULT_ORIGIN).trim() || DEFAULT_ORIGIN;
  let upstream;
  try {
    upstream = await fetchOrigin(path, env);
  } catch {
    upstream = null;
  }
  if (upstream && upstream.status === 200) {
    return { response: upstream, via: originHost };
  }

  const primaryStatus = upstream ? upstream.status : 0;
  for (const host of resolveFallbackHosts(env)) {
    try {
      const fb = await fetchMirrorHost(path, host);
      if (fb && fb.status === 200) {
        return { response: fb, via: host, primaryStatus };
      }
    } catch {
      // try next host
    }
  }
  return { response: upstream, via: null, primaryStatus };
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }
    if (request.method !== "GET" && request.method !== "HEAD") {
      return jsonError(405, "Method Not Allowed");
    }

    const secret = String(env.IMAGE_EDGE_SECRET || "").trim();
    if (!secret) {
      return jsonError(500, "IMAGE_EDGE_SECRET not configured");
    }

    const url = new URL(request.url);
    if (url.pathname === "/healthz" || url.pathname === "/") {
      return new Response(JSON.stringify({ ok: true, service: "random-image-edge" }), {
        status: 200,
        headers: { "Content-Type": "application/json; charset=utf-8", ...corsHeaders() },
      });
    }

    const parsed = parseSignedPath(url.pathname);
    if (!parsed || !Number.isFinite(parsed.exp)) {
      return jsonError(400, "Bad path");
    }

    const now = Math.floor(Date.now() / 1000);
    // Contract: now >= exp → 403 (expired at exact second boundary).
    if (parsed.exp <= now) {
      return jsonError(403, "URL expired");
    }

    let path;
    try {
      path = b64urlDecodeToUtf8(parsed.b64path);
    } catch {
      return jsonError(400, "Bad encoding");
    }
    if (!validPath(path)) {
      return jsonError(400, "Path not allowed");
    }

    const expect = await hmacSign(secret, `${parsed.exp}\n${path}`);
    if (!timingSafeEqual(expect, parsed.sig)) {
      return jsonError(403, "Invalid signature");
    }

    // Path-only cache key: ignore exp/sig so re-signed URLs share cache.
    const cache = caches.default;
    const cacheKey = new Request(new URL(`/pximg${path}`, url.origin), { method: "GET" });
    const cached = await cache.match(cacheKey);
    if (cached) {
      const headers = new Headers(cached.headers);
      headers.set("X-Edge-Cache", "HIT");
      headers.set("Access-Control-Allow-Origin", "*");
      if (request.method === "HEAD") {
        return new Response(null, { status: cached.status, headers });
      }
      return new Response(cached.body, { status: cached.status, headers });
    }

    const fetched = await fetchUpstreamWithFallback(path, env);
    const upstream = fetched.response;
    if (!upstream) {
      return jsonError(502, "Upstream fetch failed");
    }

    if (upstream.status !== 200) {
      const errTtl = Number(env.ERROR_CACHE_TTL_SECONDS || 30);
      return new Response(JSON.stringify({ ok: false, message: "Upstream error", status: upstream.status }), {
        status: 502,
        headers: {
          "Content-Type": "application/json; charset=utf-8",
          "Cache-Control": `public, max-age=${Math.max(0, errTtl)}`,
          "X-Upstream-Status": String(upstream.status),
          "X-Edge-Cache": "MISS",
          ...corsHeaders(),
        },
      });
    }

    const originHost = String(env.ORIGIN_HOST || DEFAULT_ORIGIN).trim() || DEFAULT_ORIGIN;
    const ttl = Number(env.CACHE_TTL_SECONDS || 604800);
    const headers = new Headers();
    const contentType = upstream.headers.get("content-type") || "application/octet-stream";
    headers.set("Content-Type", contentType);
    const contentLength = upstream.headers.get("content-length");
    if (contentLength) headers.set("Content-Length", contentLength);
    const etag = upstream.headers.get("etag");
    if (etag) headers.set("ETag", etag);
    const lastModified = upstream.headers.get("last-modified");
    if (lastModified) headers.set("Last-Modified", lastModified);
    headers.set("Cache-Control", `public, max-age=${Math.max(60, ttl)}, immutable`);
    headers.set("Access-Control-Allow-Origin", "*");
    headers.set("X-Content-Type-Options", "nosniff");
    headers.set("Cross-Origin-Resource-Policy", "cross-origin");
    headers.set("X-Edge-Cache", "MISS");
    headers.set("X-Edge-Origin", originHost);
    headers.set("X-Edge-Via", String(fetched.via || originHost));
    headers.set("X-Proxied-By", "random-image-edge");

    const body = request.method === "HEAD" ? null : upstream.body;
    const out = new Response(body, { status: 200, headers });

    // Cache full GET responses only (Cache API rejects 206).
    if (request.method === "GET") {
      ctx.waitUntil(cache.put(cacheKey, out.clone()));
    }
    return out;
  },
};
