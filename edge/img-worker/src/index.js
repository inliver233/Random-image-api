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
 *   verify HMAC + expiry
 *   → Cache API (path-keyed)
 *   → optional R2 binding (persistent cross-POP)
 *   → fetch i.pximg.net (+ emergency mirror chain)
 *   → async R2 put on origin/mirror 200 (when binding present)
 *
 * Design notes (ds2api + CF docs):
 * - Never accept raw target URLs (no open proxy).
 * - Strip client identity headers; never forward cookies/auth.
 * - Always send Referer: https://www.pixiv.net/
 * - Cache key ignores exp/sig so re-signed URLs share cache.
 * - R2 is optional Mode B2; binding absent = direct+Cache only.
 */

import {
  authorizePrewarmSecrets,
  buildHealthzBody,
  filterPrewarmPaths,
  isOriginCircuitOpen as pureIsOriginCircuitOpen,
  isSignedUrlExpired,
  noteOriginSample as pureNoteOriginSample,
  originCircuitConfig,
  parseSignedPath,
  resolveFallbackHosts,
  resolveR2Mode,
  resolveVerifySecrets,
  r2ObjectKey,
  timingSafeEqual,
  validPath,
} from "./pure.js";

const DEFAULT_ORIGIN = "i.pximg.net";
const REFERER = "https://www.pixiv.net/";
const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36";

// Soft circuit breaker for origin 403 storms (isolate per isolate; best-effort).
// When open, skip origin and go straight to emergency mirrors if configured.
const ORIGIN_CB = {
  windowStartMs: 0,
  samples: 0,
  forbidden: 0,
  openUntilMs: 0,
};

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

async function verifySignature(secrets, exp, path, sig) {
  if (!Array.isArray(secrets) || secrets.length === 0) return false;
  const msg = `${exp}\n${path}`;
  for (const secret of secrets) {
    try {
      const expect = await hmacSign(secret, msg);
      if (timingSafeEqual(expect, sig)) return true;
    } catch {
      // try next secret
    }
  }
  return false;
}

function isOriginCircuitOpen(nowMs) {
  return pureIsOriginCircuitOpen(ORIGIN_CB, nowMs);
}

function noteOriginSample(status, env, nowMs = Date.now()) {
  const next = pureNoteOriginSample(ORIGIN_CB, status, originCircuitConfig(env), nowMs);
  ORIGIN_CB.windowStartMs = next.windowStartMs;
  ORIGIN_CB.samples = next.samples;
  ORIGIN_CB.forbidden = next.forbidden;
  ORIGIN_CB.openUntilMs = next.openUntilMs;
}

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,HEAD,OPTIONS,POST",
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
 * Soft circuit: after repeated origin 403s, skip origin for ORIGIN_403_CIRCUIT_OPEN_MS.
 */
async function fetchUpstreamWithFallback(path, env) {
  const originHost = String(env.ORIGIN_HOST || DEFAULT_ORIGIN).trim() || DEFAULT_ORIGIN;
  const nowMs = Date.now();
  const skipOrigin = isOriginCircuitOpen(nowMs);
  let upstream = null;
  let primaryStatus = 0;

  if (!skipOrigin) {
    try {
      upstream = await fetchOrigin(path, env);
    } catch {
      upstream = null;
    }
    if (upstream) {
      primaryStatus = upstream.status;
      noteOriginSample(upstream.status, env, nowMs);
    }
    if (upstream && upstream.status === 200) {
      return { response: upstream, via: originHost, circuitOpen: false };
    }
  } else {
    primaryStatus = 403;
  }

  for (const host of resolveFallbackHosts(env)) {
    try {
      const fb = await fetchMirrorHost(path, host);
      if (fb && fb.status === 200) {
        return {
          response: fb,
          via: host,
          primaryStatus,
          circuitOpen: skipOrigin,
        };
      }
    } catch {
      // try next host
    }
  }
  return { response: upstream, via: null, primaryStatus, circuitOpen: skipOrigin };
}

function successHeaders({ contentType, contentLength, etag, lastModified, ttl, via, originHost, circuitOpen, edgeVia }) {
  const headers = new Headers();
  headers.set("Content-Type", contentType || "application/octet-stream");
  if (contentLength) headers.set("Content-Length", String(contentLength));
  if (etag) headers.set("ETag", etag);
  if (lastModified) headers.set("Last-Modified", lastModified);
  headers.set("Cache-Control", `public, max-age=${Math.max(60, ttl)}, immutable`);
  headers.set("Access-Control-Allow-Origin", "*");
  headers.set("X-Content-Type-Options", "nosniff");
  headers.set("Cross-Origin-Resource-Policy", "cross-origin");
  headers.set("X-Edge-Cache", "MISS");
  headers.set("X-Edge-Origin", originHost);
  headers.set("X-Edge-Via", String(via || originHost));
  if (edgeVia) headers.set("X-Edge-Storage", edgeVia);
  if (circuitOpen) headers.set("X-Edge-Circuit", "origin-open");
  headers.set("X-Proxied-By", "random-image-edge");
  return headers;
}

async function tryR2Get(env, path) {
  if (resolveR2Mode(env) === "off" || !env.R2) return null;
  try {
    return await env.R2.get(r2ObjectKey(path));
  } catch {
    return null;
  }
}

function scheduleR2Put(env, ctx, path, body, contentType) {
  if (resolveR2Mode(env) === "off" || !env.R2 || !body) return;
  const key = r2ObjectKey(path);
  ctx.waitUntil(
    (async () => {
      try {
        await env.R2.put(key, body, {
          httpMetadata: { contentType: contentType || "application/octet-stream" },
        });
      } catch {
        // best-effort only
      }
    })(),
  );
}

function authorizePrewarm(request, env) {
  const expected = String(env.PREWARM_SECRET || env.IMAGE_EDGE_SECRET || "").trim();
  const got = String(request.headers.get("X-Prewarm-Secret") || "").trim();
  return authorizePrewarmSecrets(expected, got);
}

/**
 * Ops prewarm: fetch path(s) into R2 (+ Cache) using the same origin path as public edge.
 * Body: { "paths": ["/img-original/..."] }  (max 50)
 * Auth: X-Prewarm-Secret == PREWARM_SECRET or IMAGE_EDGE_SECRET
 *
 * BFF r2_prewarm.py maps catalog image_ids → paths and POSTs this shape when R2_PREWARM_* is on.
 */
async function handlePrewarm(request, env, ctx) {
  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders() });
  }
  if (request.method !== "POST") {
    return jsonError(405, "Method Not Allowed");
  }
  if (resolveR2Mode(env) === "off" || !env.R2) {
    return jsonError(503, "R2 not configured");
  }
  if (!authorizePrewarm(request, env)) {
    return jsonError(403, "Forbidden");
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return jsonError(400, "Invalid JSON");
  }
  const paths = filterPrewarmPaths(Array.isArray(body?.paths) ? body.paths : [], 50);
  if (!paths.length) {
    return jsonError(400, "No valid paths");
  }

  const cache = caches.default;
  let ok = 0;
  let failed = 0;
  for (const path of paths) {
    try {
      const existing = await env.R2.head(r2ObjectKey(path));
      if (existing) {
        ok += 1;
        continue;
      }
      const fetched = await fetchUpstreamWithFallback(path, env);
      if (!fetched.response || fetched.response.status !== 200) {
        failed += 1;
        continue;
      }
      const buf = await fetched.response.arrayBuffer();
      const contentType =
        fetched.response.headers.get("content-type") || "application/octet-stream";
      await env.R2.put(r2ObjectKey(path), buf, {
        httpMetadata: { contentType },
      });
      const ttl = Number(env.CACHE_TTL_SECONDS || 604800);
      const originHost = String(env.ORIGIN_HOST || DEFAULT_ORIGIN).trim() || DEFAULT_ORIGIN;
      const headers = successHeaders({
        contentType,
        contentLength: buf.byteLength,
        etag: fetched.response.headers.get("etag"),
        lastModified: fetched.response.headers.get("last-modified"),
        ttl,
        via: fetched.via || originHost,
        originHost,
        circuitOpen: Boolean(fetched.circuitOpen),
        edgeVia: "r2-prewarm",
      });
      headers.set("X-Edge-Cache", "MISS");
      const cacheKey = new Request(new URL(`/pximg${path}`, new URL(request.url).origin), {
        method: "GET",
      });
      ctx.waitUntil(cache.put(cacheKey, new Response(buf, { status: 200, headers })));
      ok += 1;
    } catch {
      failed += 1;
    }
  }

  return new Response(JSON.stringify({ ok: true, prewarmed: ok, failed, total: paths.length }), {
    status: 200,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      ...corsHeaders(),
    },
  });
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }

    const url = new URL(request.url);
    if (url.pathname === "/v1/prewarm") {
      return handlePrewarm(request, env, ctx);
    }

    if (request.method !== "GET" && request.method !== "HEAD") {
      return jsonError(405, "Method Not Allowed");
    }

    const secrets = resolveVerifySecrets(env);
    if (!secrets.length) {
      return jsonError(500, "IMAGE_EDGE_SECRET not configured");
    }

    if (url.pathname === "/healthz" || url.pathname === "/") {
      const circuitOpen = isOriginCircuitOpen(Date.now());
      const r2Mode = resolveR2Mode(env);
      return new Response(JSON.stringify(buildHealthzBody({ secrets, circuitOpen, r2Mode })), {
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
    if (isSignedUrlExpired(parsed.exp, now)) {
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

    const okSig = await verifySignature(secrets, parsed.exp, path, parsed.sig);
    if (!okSig) {
      return jsonError(403, "Invalid signature");
    }

    const originHost = String(env.ORIGIN_HOST || DEFAULT_ORIGIN).trim() || DEFAULT_ORIGIN;
    const ttl = Number(env.CACHE_TTL_SECONDS || 604800);
    const r2Mode = resolveR2Mode(env);

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

    // Optional R2 read (Mode B2 / read_through).
    if (r2Mode !== "off") {
      const obj = await tryR2Get(env, path);
      if (obj) {
        const contentType = obj.httpMetadata?.contentType || "application/octet-stream";
        const headers = successHeaders({
          contentType,
          contentLength: obj.size,
          etag: obj.httpEtag,
          lastModified: obj.uploaded ? new Date(obj.uploaded).toUTCString() : null,
          ttl,
          via: "r2",
          originHost,
          circuitOpen: false,
          edgeVia: "r2",
        });
        headers.set("X-Edge-Cache", "MISS");
        headers.set("X-Edge-Via", "r2");
        // Warm Cache API from R2 for this POP.
        if (request.method === "GET") {
          const body = obj.body;
          const out = new Response(body, { status: 200, headers });
          ctx.waitUntil(cache.put(cacheKey, out.clone()));
          return out;
        }
        // HEAD: still warm cache with full object when possible.
        const buf = await obj.arrayBuffer();
        const getRes = new Response(buf, { status: 200, headers });
        ctx.waitUntil(cache.put(cacheKey, getRes.clone()));
        return new Response(null, { status: 200, headers });
      }
      if (r2Mode === "r2_only") {
        return jsonError(404, "Not in R2", { "X-Edge-Storage": "r2_only" });
      }
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

    const contentType = upstream.headers.get("content-type") || "application/octet-stream";
    const contentLength = upstream.headers.get("content-length");
    const etag = upstream.headers.get("etag");
    const lastModified = upstream.headers.get("last-modified");
    const headers = successHeaders({
      contentType,
      contentLength,
      etag,
      lastModified,
      ttl,
      via: fetched.via || originHost,
      originHost,
      circuitOpen: Boolean(fetched.circuitOpen),
    });

    // Buffer once so we can (1) respond, (2) Cache API put, (3) optional R2 put — including HEAD warm.
    const buf = await upstream.arrayBuffer();
    if (contentLength == null || contentLength === "") {
      headers.set("Content-Length", String(buf.byteLength));
    }

    const getRes = new Response(buf, { status: 200, headers });
    ctx.waitUntil(cache.put(cacheKey, getRes.clone()));
    scheduleR2Put(env, ctx, path, buf, contentType);

    if (request.method === "HEAD") {
      return new Response(null, { status: 200, headers });
    }
    return new Response(buf, { status: 200, headers });
  },
};
