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
  cacheWriteMaxBytes,
  canFollowUpstreamRedirect,
  coldFlightWaitMs,
  coldStreamTimeouts,
  contentLengthWithinLimit,
  filterPrewarmPaths,
  imageDimensionLimits,
  isOriginCircuitOpen as pureIsOriginCircuitOpen,
  isSignedUrlExpired,
  maxConcurrentColdFills,
  maxImageBytes,
  noteOriginSample as pureNoteOriginSample,
  originCircuitConfig,
  parseRateLimitConfig,
  parseSignedPath,
  resolveFallbackHosts,
  resolveR2Mode,
  resolveSafeRedirectUrl,
  resolveVerifySecrets,
  r2ObjectKey,
  storageWriteTimeoutMs,
  takeRateLimitToken,
  timingSafeEqual,
  validPath,
  validatedImageContentType,
} from "./pure.js";
import { createValidatedImageStream, ImageBodyError, readValidatedImageBuffer } from "./image_stream.js";

/** Per-isolate token bucket for origin/R2 miss path (resets on cold start). */
let _rateBucket = null;
/** Per-isolate cold-fill coordination. It deliberately does not claim cross-POP deduplication. */
const _coldFlights = new Map();
let _activeColdFills = 0;

const DEFAULT_ORIGIN = "i.pximg.net";
const REFERER = "https://www.pixiv.net/";
const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36";
const REDIRECT_STATUSES = new Set([301, 302, 303, 307, 308]);
const IMAGE_VALIDATION_VERSION = "v1";
const IMAGE_VALIDATION_HEADER = "X-Edge-Validation";

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
    // Prewarm clients may send X-Prewarm-Secret; Range for partial GETs (parity with api-worker openness).
    "Access-Control-Allow-Headers": "*",
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

function waitUntilBestEffort(ctx, promise) {
  ctx.waitUntil(Promise.resolve(promise).catch(() => undefined));
}

function withTimeout(promise, timeoutMs, message) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(message)), timeoutMs);
    Promise.resolve(promise).then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

function originFetchInit(env, method = "GET") {
  return {
    method,
    headers: {
      Referer: REFERER,
      "User-Agent": UA,
      Accept: "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    },
    redirect: "manual",
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

async function fetchWithSafeRedirects(initialUrl, init, policy) {
  let url = resolveSafeRedirectUrl(initialUrl, initialUrl, policy);
  if (!url) throw new Error("Upstream URL is outside the allowed trust boundary");

  for (let redirects = 0; ; redirects += 1) {
    const response = await fetch(url, { ...init, redirect: "manual" });
    if (!REDIRECT_STATUSES.has(response.status)) return response;

    const location = response.headers.get("Location");
    if (!location) return response;
    if (response.body) await response.body.cancel();
    if (!canFollowUpstreamRedirect(redirects)) {
      throw new Error("Upstream redirect limit exceeded");
    }

    const nextUrl = resolveSafeRedirectUrl(url, location, policy);
    if (!nextUrl) throw new Error("Upstream redirect target is not allowed");
    url = nextUrl;
  }
}

async function fetchOrigin(path, env, method = "GET") {
  const originHost = String(env.ORIGIN_HOST || DEFAULT_ORIGIN).trim() || DEFAULT_ORIGIN;
  const url = `https://${originHost}${path}`;
  const init = originFetchInit(env, method);
  try {
    return await fetchWithSafeRedirects(url, init, { mode: "pximg" });
  } catch {
    // One retry for transient network errors on cold POP / origin blip.
    return await fetchWithSafeRedirects(url, init, { mode: "pximg" });
  }
}

async function fetchMirrorHost(path, host, method = "GET") {
  const url = `https://${host}${path}`;
  return fetchWithSafeRedirects(
    url,
    {
      method,
      headers: {
        Referer: REFERER,
        "User-Agent": UA,
        Accept: "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
      },
      redirect: "manual",
    },
    { mode: "same-host", allowedHost: host },
  );
}

/**
 * Primary origin, then ordered emergency mirrors. Returns { response, via }.
 * via is origin host or mirror host that produced HTTP 200.
 * Soft circuit: after repeated origin 403s, skip origin for ORIGIN_403_CIRCUIT_OPEN_MS.
 */
async function fetchUpstreamWithFallback(path, env, method = "GET") {
  const originHost = String(env.ORIGIN_HOST || DEFAULT_ORIGIN).trim() || DEFAULT_ORIGIN;
  const nowMs = Date.now();
  const skipOrigin = isOriginCircuitOpen(nowMs);
  let upstream = null;
  let primaryStatus = 0;

  if (!skipOrigin) {
    try {
      upstream = await fetchOrigin(path, env, method);
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
    if (upstream?.body) await upstream.body.cancel();
  } else {
    primaryStatus = 403;
  }

  for (const host of resolveFallbackHosts(env)) {
    try {
      const fb = await fetchMirrorHost(path, host, method);
      if (fb && fb.status === 200) {
        return {
          response: fb,
          via: host,
          primaryStatus,
          circuitOpen: skipOrigin,
        };
      }
      if (fb?.body) await fb.body.cancel();
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

async function readR2(env, path, method = "GET") {
  if (resolveR2Mode(env) === "off" || !env.R2) return { status: "miss", object: null };
  try {
    if (method === "HEAD" && typeof env.R2.head !== "function") {
      return { status: "error", object: null, error: new Error("R2 HEAD is unavailable") };
    }
    const operation = method === "HEAD" ? "head" : "get";
    const object = await env.R2[operation](r2ObjectKey(path));
    return object ? { status: "hit", object } : { status: "miss", object: null };
  } catch (error) {
    return { status: "error", object: null, error };
  }
}

function isValidatedR2Object(object) {
  return object?.customMetadata?.edgeValidation === IMAGE_VALIDATION_VERSION;
}

function isValidatedCacheResponse(response) {
  return response?.headers?.get(IMAGE_VALIDATION_HEADER) === IMAGE_VALIDATION_VERSION;
}

async function persistValidatedBuffer({ env, cache, cacheKey, path, bytes, headers, contentType, writeR2 }) {
  const storedHeaders = new Headers(headers);
  storedHeaders.set("Content-Length", String(bytes.byteLength));
  storedHeaders.set(IMAGE_VALIDATION_HEADER, IMAGE_VALIDATION_VERSION);
  await cache.put(cacheKey, new Response(bytes, { status: 200, headers: storedHeaders }));
  if (writeR2 && env.R2) {
    try {
      await env.R2.put(r2ObjectKey(path), bytes, {
        httpMetadata: { contentType },
        customMetadata: { edgeValidation: IMAGE_VALIDATION_VERSION },
      });
    } catch {
      // R2 is optional on the public read-through path; Cache API success remains useful.
    }
  }
}

function waitForFlight(promise, timeoutMs) {
  if (timeoutMs <= 0) return Promise.resolve(false);
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), timeoutMs);
    promise.then(() => {
      clearTimeout(timer);
      resolve(true);
    });
  });
}

function beginColdFlight(key, maxConcurrent) {
  const existing = _coldFlights.get(key);
  if (existing) return { role: "follower", promise: existing };
  if (_activeColdFills >= maxConcurrent) return { role: "saturated", promise: null };
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  _coldFlights.set(key, promise);
  _activeColdFills += 1;
  let finished = false;
  return {
    role: "leader",
    promise,
    finish() {
      if (finished) return;
      finished = true;
      if (_coldFlights.get(key) === promise) _coldFlights.delete(key);
      _activeColdFills = Math.max(0, _activeColdFills - 1);
      resolve();
    },
  };
}

async function matchValidatedCache({ cache, cacheKey, path, maxBytes, method, ctx }) {
  const cached = await cache.match(cacheKey);
  if (!cached) return null;
  const headers = new Headers(cached.headers);
  const rawLength = headers.get("content-length");
  const cachedType = validatedImageContentType(path, headers.get("content-type"));
  const valid =
    isValidatedCacheResponse(cached) &&
    Boolean(rawLength) &&
    Boolean(cachedType) &&
    contentLengthWithinLimit(rawLength, maxBytes);
  if (!valid) {
    if (cached.body) await cached.body.cancel();
    waitUntilBestEffort(ctx, cache.delete(cacheKey));
    return null;
  }
  headers.set("Content-Type", cachedType);
  headers.set("X-Edge-Cache", "HIT");
  headers.set("Access-Control-Allow-Origin", "*");
  if (method === "HEAD") {
    if (cached.body) await cached.body.cancel();
    return new Response(null, { status: cached.status, headers });
  }
  return new Response(cached.body, { status: cached.status, headers });
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
  const ttl = Number(env.CACHE_TTL_SECONDS || 604800);
  const originHost = String(env.ORIGIN_HOST || DEFAULT_ORIGIN).trim() || DEFAULT_ORIGIN;
  const persistMaxBytes = cacheWriteMaxBytes(env);
  const dimensionLimits = imageDimensionLimits(env);
  const streamTimeouts = coldStreamTimeouts(env);
  const writeTimeoutMs = storageWriteTimeoutMs(env);
  let ok = 0;
  let failed = 0;
  let cacheWarmed = 0;
  let alreadyR2 = 0;
  for (const path of paths) {
    try {
      const cacheKey = new Request(new URL(`/pximg${path}`, new URL(request.url).origin), {
        method: "GET",
      });
      // R2 hit: still warm this POP's Cache API (contract: prewarm warms Cache).
      const r2Read = await readR2(env, path);
      if (r2Read.status === "error") {
        failed += 1;
        continue;
      }
      const existing = r2Read.object;
      if (existing && isValidatedR2Object(existing)) {
        const contentType = validatedImageContentType(path, existing.httpMetadata?.contentType);
        if (!contentType || !contentLengthWithinLimit(existing.size, persistMaxBytes)) {
          if (existing.body) await existing.body.cancel();
          if (typeof env.R2?.delete === "function") {
            await env.R2.delete(r2ObjectKey(path)).catch(() => undefined);
          }
          failed += 1;
          continue;
        }
        const headers = successHeaders({
          contentType,
          contentLength: existing.size,
          etag: existing.httpEtag,
          lastModified: existing.uploaded ? new Date(existing.uploaded).toUTCString() : null,
          ttl,
          via: "r2",
          originHost,
          circuitOpen: false,
          edgeVia: "r2-prewarm",
        });
        headers.set("X-Edge-Cache", "MISS");
        headers.set(IMAGE_VALIDATION_HEADER, IMAGE_VALIDATION_VERSION);
        let buf;
        try {
          buf = await readValidatedImageBuffer(existing.body, path, {
            maxBytes: persistMaxBytes,
            expectedLength: Number(existing.size),
            limits: dimensionLimits,
            timeouts: streamTimeouts,
            readErrorStatus: 503,
          });
        } catch (error) {
          if (error?.deleteStored && typeof env.R2?.delete === "function") {
            await env.R2.delete(r2ObjectKey(path)).catch(() => undefined);
          }
          failed += 1;
          continue;
        }
        await withTimeout(
          cache.put(cacheKey, new Response(buf, { status: 200, headers })),
          writeTimeoutMs,
          "Cache prewarm write timed out",
        );
        alreadyR2 += 1;
        cacheWarmed += 1;
        ok += 1;
        continue;
      }
      if (existing?.body) await existing.body.cancel();
      const fetched = await fetchUpstreamWithFallback(path, env);
      if (!fetched.response || fetched.response.status !== 200) {
        if (fetched.response?.body) await fetched.response.body.cancel();
        failed += 1;
        continue;
      }
      const contentType = validatedImageContentType(path, fetched.response.headers.get("content-type"));
      const declaredLength = fetched.response.headers.get("content-length");
      if (
        !contentType ||
        !/^\d+$/.test(String(declaredLength || "")) ||
        !contentLengthWithinLimit(declaredLength, persistMaxBytes)
      ) {
        if (fetched.response.body) await fetched.response.body.cancel();
        failed += 1;
        continue;
      }
      const buf = await readValidatedImageBuffer(
        fetched.response.body,
        path,
        {
          maxBytes: persistMaxBytes,
          expectedLength: Number(declaredLength),
          limits: dimensionLimits,
          timeouts: streamTimeouts,
          readErrorStatus: 502,
        },
      );
      await withTimeout(
        env.R2.put(r2ObjectKey(path), buf, {
          httpMetadata: { contentType },
          customMetadata: { edgeValidation: IMAGE_VALIDATION_VERSION },
        }),
        writeTimeoutMs,
        "R2 prewarm write timed out",
      );
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
      headers.set(IMAGE_VALIDATION_HEADER, IMAGE_VALIDATION_VERSION);
      await withTimeout(
        cache.put(cacheKey, new Response(buf, { status: 200, headers })),
        writeTimeoutMs,
        "Cache prewarm write timed out",
      );
      cacheWarmed += 1;
      ok += 1;
    } catch {
      failed += 1;
    }
  }

  return new Response(
    JSON.stringify({
      ok: true,
      prewarmed: ok,
      failed,
      total: paths.length,
      cache_warmed: cacheWarmed,
      already_r2: alreadyR2,
    }),
    {
      status: 200,
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
        ...corsHeaders(),
      },
    },
  );
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
      const rateCfg = parseRateLimitConfig(env);
      return new Response(
        JSON.stringify(
          buildHealthzBody({
            secrets,
            circuitOpen,
            r2Mode,
            rateLimit: rateCfg.enabled
              ? { enabled: true, rpm: rateCfg.rpm, burst: rateCfg.burst }
              : { enabled: false },
          }),
        ),
        {
          status: 200,
          headers: { "Content-Type": "application/json; charset=utf-8", ...corsHeaders() },
        },
      );
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
    const maxBytes = maxImageBytes(env);
    const persistMaxBytes = cacheWriteMaxBytes(env);
    const dimensionLimits = imageDimensionLimits(env);
    const streamTimeouts = coldStreamTimeouts(env);
    const writeTimeoutMs = storageWriteTimeoutMs(env);

    // Path-only cache key: ignore exp/sig so re-signed URLs share cache.
    const cache = caches.default;
    const cacheKey = new Request(new URL(`/pximg${path}`, url.origin), { method: "GET" });
    const cached = await matchValidatedCache({ cache, cacheKey, path, maxBytes, method: request.method, ctx });
    if (cached) return cached;

    // Isolate rate limit only on Cache MISS (origin/R2 work). Cache HIT stays free.
    const rateCfg = parseRateLimitConfig(env);
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

    // Optional R2 read (Mode B2 / read_through).
    if (r2Mode !== "off") {
      const r2Read = await readR2(env, path, request.method);
      if (r2Read.status === "error" && r2Mode === "r2_only") {
        return jsonError(503, "R2 unavailable", { "X-Edge-Storage": "r2_only", "Retry-After": "1" });
      }
      const obj = r2Read.object;
      if (obj && !isValidatedR2Object(obj)) {
        if (obj.body) await obj.body.cancel();
        if (env.R2 && typeof env.R2.delete === "function") {
          waitUntilBestEffort(ctx, env.R2.delete(r2ObjectKey(path)));
        }
      } else if (obj) {
        const contentType = validatedImageContentType(path, obj.httpMetadata?.contentType);
        if (!contentType || !contentLengthWithinLimit(obj.size, maxBytes)) {
          if (obj.body) await obj.body.cancel();
          if (typeof env.R2?.delete === "function") {
            waitUntilBestEffort(ctx, env.R2.delete(r2ObjectKey(path)));
          }
          return jsonError(contentType ? 413 : 415, contentType ? "R2 object too large" : "Unsupported R2 content type");
        }
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
        if (request.method === "GET") {
          try {
            const collect = Number(obj.size) <= persistMaxBytes;
            const opened = await createValidatedImageStream(obj.body, path, {
              maxBytes,
              collect,
              expectedLength: Number(obj.size),
              limits: dimensionLimits,
              timeouts: streamTimeouts,
              readErrorStatus: 503,
            });
            const persistence = opened.completion.then(async (result) => {
              if (!result.complete || !result.bytes) {
                if (result.error?.deleteStored && typeof env.R2?.delete === "function") {
                  await env.R2.delete(r2ObjectKey(path)).catch(() => undefined);
                }
                return;
              }
              const storedHeaders = new Headers(headers);
              storedHeaders.set(IMAGE_VALIDATION_HEADER, IMAGE_VALIDATION_VERSION);
              storedHeaders.set("Content-Length", String(result.totalBytes));
              await withTimeout(
                cache.put(cacheKey, new Response(result.bytes, { status: 200, headers: storedHeaders })),
                writeTimeoutMs,
                "Cache write timed out",
              );
            });
            ctx.waitUntil(persistence.catch(() => undefined));
            return new Response(opened.stream, { status: 200, headers });
          } catch (error) {
            if (error?.deleteStored && typeof env.R2?.delete === "function") {
              waitUntilBestEffort(ctx, env.R2.delete(r2ObjectKey(path)));
            }
            const status = error instanceof ImageBodyError ? error.status : 503;
            if (r2Mode === "r2_only") {
              const message =
                status === 413
                  ? "R2 object too large"
                  : status === 415
                    ? "Invalid R2 image body"
                    : "R2 unavailable";
              return jsonError(status, message);
            }
          }
        }
        if (request.method === "HEAD") {
          // HEAD is metadata-only. Never read the object body or warm a full GET entry.
          return new Response(null, { status: 200, headers });
        }
      }
      if (r2Mode === "r2_only") {
        return jsonError(404, "Not in R2", { "X-Edge-Storage": "r2_only" });
      }
    }

    let coldFlight = null;
    if (request.method === "GET") {
      coldFlight = beginColdFlight(cacheKey.url, maxConcurrentColdFills(env));
      if (coldFlight.role === "follower") {
        await waitForFlight(coldFlight.promise, coldFlightWaitMs(env));
        const filled = await matchValidatedCache({
          cache,
          cacheKey,
          path,
          maxBytes,
          method: request.method,
          ctx,
        });
        if (filled) return filled;
        return jsonError(503, "Image fill already in progress", { "Retry-After": "1" });
      }
      if (coldFlight.role === "saturated") {
        return jsonError(503, "Image fill capacity reached", { "Retry-After": "1" });
      }
    }

    let fetched;
    try {
      fetched = await fetchUpstreamWithFallback(path, env, request.method);
    } catch {
      coldFlight?.finish?.();
      return jsonError(502, "Upstream fetch failed");
    }
    const upstream = fetched.response;
    if (!upstream) {
      coldFlight?.finish?.();
      return jsonError(502, "Upstream fetch failed");
    }

    if (upstream.status !== 200) {
      if (upstream.body) await upstream.body.cancel();
      coldFlight?.finish?.();
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

    const contentType = validatedImageContentType(path, upstream.headers.get("content-type"));
    const contentLength = upstream.headers.get("content-length");
    if (!contentType) {
      if (upstream.body) await upstream.body.cancel();
      coldFlight?.finish?.();
      return jsonError(415, "Unsupported upstream content type");
    }
    if (!contentLengthWithinLimit(contentLength, maxBytes)) {
      if (upstream.body) await upstream.body.cancel();
      coldFlight?.finish?.();
      return jsonError(413, "Image too large");
    }
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

    if (request.method === "HEAD") {
      return new Response(null, { status: 200, headers });
    }

    try {
      const declaredLength = /^\d+$/.test(String(contentLength || "")) ? Number(contentLength) : null;
      const collect = declaredLength !== null && declaredLength <= persistMaxBytes;
      const opened = await createValidatedImageStream(
        upstream.body,
        path,
        {
          maxBytes,
          collect,
          expectedLength: declaredLength,
          limits: dimensionLimits,
          timeouts: streamTimeouts,
          readErrorStatus: 502,
        },
      );
      const persistence = opened.completion
        .then(async (result) => {
          if (result.complete && result.bytes) {
            await withTimeout(
              persistValidatedBuffer({
                env,
                cache,
                cacheKey,
                path,
                bytes: result.bytes,
                headers,
                contentType,
                writeR2: r2Mode !== "off",
              }),
              writeTimeoutMs,
              "Image persistence timed out",
            );
          }
        })
        .finally(() => coldFlight.finish());
      ctx.waitUntil(persistence.catch(() => undefined));
      return new Response(opened.stream, { status: 200, headers });
    } catch (error) {
      coldFlight.finish();
      const status = error instanceof ImageBodyError ? error.status : 502;
      const message =
        status === 413
          ? "Image too large"
          : status === 415
            ? "Invalid upstream image body"
            : status === 504
              ? "Upstream image timeout"
              : "Upstream image stream failed";
      return jsonError(status, message);
    }
  },
};
