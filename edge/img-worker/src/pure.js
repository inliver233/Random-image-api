/**
 * Pure helpers for random-image-edge Worker (no Request/fetch/Cache).
 * Shared by src/index.js and offline Node tests.
 */

export const ALLOWED_PREFIXES = ["/img-original/", "/img-master/", "/img-/", "/c/"];
export const ALLOWED_EXT = new Set(["jpg", "jpeg", "png", "gif", "webp"]);
export const BUILTIN_MIRRORS = new Set(["i.pixiv.cat", "i.pixiv.re", "i.pixiv.nl"]);
export const MAX_UPSTREAM_REDIRECTS = 3;
export const DEFAULT_MAX_IMAGE_BYTES = 32 * 1024 * 1024;
export const DEFAULT_CACHE_WRITE_MAX_BYTES = 8 * 1024 * 1024;
export const DEFAULT_MAX_CONCURRENT_COLD_FILLS = 4;
export const DEFAULT_COLD_FLIGHT_WAIT_MS = 1500;
export const DEFAULT_COLD_STREAM_IDLE_TIMEOUT_MS = 15000;
export const DEFAULT_COLD_STREAM_TOTAL_TIMEOUT_MS = 120000;
export const DEFAULT_STORAGE_WRITE_TIMEOUT_MS = 5000;
export const DEFAULT_MAX_IMAGE_WIDTH = 16384;
export const DEFAULT_MAX_IMAGE_HEIGHT = 16384;
export const DEFAULT_MAX_IMAGE_PIXELS = 100_000_000;

const MIME_BY_EXT = {
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  gif: "image/gif",
  webp: "image/webp",
};

export function maxImageBytes(env) {
  const parsed = Number(env?.MAX_IMAGE_BYTES || DEFAULT_MAX_IMAGE_BYTES);
  if (!Number.isFinite(parsed) || parsed <= 0) return DEFAULT_MAX_IMAGE_BYTES;
  return Math.max(1024 * 1024, Math.min(Math.floor(parsed), 100 * 1024 * 1024));
}

export function cacheWriteMaxBytes(env) {
  const maxImage = maxImageBytes(env);
  const parsed = Number(env?.CACHE_WRITE_MAX_BYTES || DEFAULT_CACHE_WRITE_MAX_BYTES);
  if (!Number.isFinite(parsed) || parsed <= 0) return Math.min(DEFAULT_CACHE_WRITE_MAX_BYTES, maxImage);
  return Math.max(1024 * 1024, Math.min(Math.floor(parsed), maxImage));
}

export function maxConcurrentColdFills(env) {
  const parsed = Number(env?.MAX_CONCURRENT_COLD_FILLS || DEFAULT_MAX_CONCURRENT_COLD_FILLS);
  if (!Number.isFinite(parsed) || parsed <= 0) return DEFAULT_MAX_CONCURRENT_COLD_FILLS;
  return Math.max(1, Math.min(Math.floor(parsed), 64));
}

export function coldFlightWaitMs(env) {
  const parsed = Number(env?.COLD_FLIGHT_WAIT_MS || DEFAULT_COLD_FLIGHT_WAIT_MS);
  if (!Number.isFinite(parsed) || parsed < 0) return DEFAULT_COLD_FLIGHT_WAIT_MS;
  return Math.max(0, Math.min(Math.floor(parsed), 5000));
}

export function coldStreamTimeouts(env) {
  const idleRaw = Number(env?.COLD_STREAM_IDLE_TIMEOUT_MS || DEFAULT_COLD_STREAM_IDLE_TIMEOUT_MS);
  const totalRaw = Number(env?.COLD_STREAM_TOTAL_TIMEOUT_MS || DEFAULT_COLD_STREAM_TOTAL_TIMEOUT_MS);
  const idleMs = Number.isFinite(idleRaw)
    ? Math.max(1000, Math.min(Math.floor(idleRaw), 60000))
    : DEFAULT_COLD_STREAM_IDLE_TIMEOUT_MS;
  const totalMs = Number.isFinite(totalRaw)
    ? Math.max(idleMs, Math.min(Math.floor(totalRaw), 300000))
    : DEFAULT_COLD_STREAM_TOTAL_TIMEOUT_MS;
  return { idleMs, totalMs };
}

export function storageWriteTimeoutMs(env) {
  const parsed = Number(env?.STORAGE_WRITE_TIMEOUT_MS || DEFAULT_STORAGE_WRITE_TIMEOUT_MS);
  if (!Number.isFinite(parsed) || parsed <= 0) return DEFAULT_STORAGE_WRITE_TIMEOUT_MS;
  return Math.max(500, Math.min(Math.floor(parsed), 30000));
}

export function imageDimensionLimits(env) {
  const clamp = (raw, fallback, max) => {
    const parsed = Number(raw || fallback);
    if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
    return Math.max(1, Math.min(Math.floor(parsed), max));
  };
  return {
    maxWidth: clamp(env?.MAX_IMAGE_WIDTH, DEFAULT_MAX_IMAGE_WIDTH, 65535),
    maxHeight: clamp(env?.MAX_IMAGE_HEIGHT, DEFAULT_MAX_IMAGE_HEIGHT, 65535),
    maxPixels: clamp(env?.MAX_IMAGE_PIXELS, DEFAULT_MAX_IMAGE_PIXELS, 4_000_000_000),
  };
}

export function hasExpectedImageMagic(path, bytes) {
  const data = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes || []);
  const ext = String(path || "").split(".").pop()?.toLowerCase() || "";
  if ((ext === "jpg" || ext === "jpeg") && data.length >= 3) {
    return data[0] === 0xff && data[1] === 0xd8 && data[2] === 0xff;
  }
  if (ext === "png" && data.length >= 8) {
    return [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a].every((value, i) => data[i] === value);
  }
  if (ext === "gif" && data.length >= 6) {
    const sig = String.fromCharCode(...data.slice(0, 6));
    return sig === "GIF87a" || sig === "GIF89a";
  }
  if (ext === "webp" && data.length >= 12) {
    return (
      String.fromCharCode(...data.slice(0, 4)) === "RIFF" &&
      String.fromCharCode(...data.slice(8, 12)) === "WEBP"
    );
  }
  return false;
}

export function parseImageDimensions(path, bytes) {
  const data = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes || []);
  const ext = String(path || "").split(".").pop()?.toLowerCase() || "";
  const ok = (width, height) =>
    width > 0 && height > 0 ? { status: "ok", width, height } : { status: "invalid" };
  if (ext === "png") {
    if (data.length < 24) return { status: "need-more" };
    if (!hasExpectedImageMagic(path, data)) return { status: "invalid" };
    if (String.fromCharCode(...data.slice(12, 16)) !== "IHDR") return { status: "invalid" };
    const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
    return ok(view.getUint32(16), view.getUint32(20));
  }
  if (ext === "gif") {
    if (data.length < 10) return { status: "need-more" };
    if (!hasExpectedImageMagic(path, data)) return { status: "invalid" };
    return ok(data[6] | (data[7] << 8), data[8] | (data[9] << 8));
  }
  if (ext === "webp") {
    if (data.length < 30) return { status: "need-more" };
    if (!hasExpectedImageMagic(path, data)) return { status: "invalid" };
    const kind = String.fromCharCode(...data.slice(12, 16));
    if (kind === "VP8X") {
      const width = 1 + data[24] + (data[25] << 8) + (data[26] << 16);
      const height = 1 + data[27] + (data[28] << 8) + (data[29] << 16);
      return ok(width, height);
    }
    if (kind === "VP8L") {
      if (data[20] !== 0x2f) return { status: "invalid" };
      const width = 1 + data[21] + ((data[22] & 0x3f) << 8);
      const height = 1 + (data[22] >> 6) + (data[23] << 2) + ((data[24] & 0x0f) << 10);
      return ok(width, height);
    }
    if (kind === "VP8 ") {
      if (data[23] !== 0x9d || data[24] !== 0x01 || data[25] !== 0x2a) return { status: "invalid" };
      const width = (data[26] | (data[27] << 8)) & 0x3fff;
      const height = (data[28] | (data[29] << 8)) & 0x3fff;
      return ok(width, height);
    }
    return { status: "invalid" };
  }
  if (ext === "jpg" || ext === "jpeg") {
    if (data.length < 4) return { status: "need-more" };
    if (!hasExpectedImageMagic(path, data)) return { status: "invalid" };
    const sofMarkers = new Set([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf]);
    let offset = 2;
    while (offset < data.length) {
      while (offset < data.length && data[offset] !== 0xff) offset += 1;
      if (offset >= data.length) return { status: "need-more" };
      while (offset < data.length && data[offset] === 0xff) offset += 1;
      if (offset >= data.length) return { status: "need-more" };
      const marker = data[offset++];
      if (marker === 0xd8 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue;
      if (marker === 0xd9 || marker === 0xda) return { status: "invalid" };
      if (offset + 2 > data.length) return { status: "need-more" };
      const length = (data[offset] << 8) | data[offset + 1];
      if (length < 2) return { status: "invalid" };
      if (sofMarkers.has(marker)) {
        if (length < 7) return { status: "invalid" };
        if (offset + 7 > data.length) return { status: "need-more" };
        const height = (data[offset + 3] << 8) | data[offset + 4];
        const width = (data[offset + 5] << 8) | data[offset + 6];
        return ok(width, height);
      }
      if (offset + length > data.length) return { status: "need-more" };
      offset += length;
    }
    return { status: "need-more" };
  }
  return { status: "invalid" };
}

export function dimensionsWithinLimit(dimensions, limits) {
  if (!dimensions || dimensions.status !== "ok") return false;
  return (
    dimensions.width <= limits.maxWidth &&
    dimensions.height <= limits.maxHeight &&
    dimensions.width * dimensions.height <= limits.maxPixels
  );
}

export function validatedImageContentType(path, rawContentType) {
  const ext = String(path || "").split(".").pop()?.toLowerCase() || "";
  const expected = MIME_BY_EXT[ext];
  if (!expected) return null;
  const actual = String(rawContentType || "").split(";", 1)[0].trim().toLowerCase();
  if (!actual || actual === "application/octet-stream") return expected;
  if (ext === "jpg" || ext === "jpeg") {
    return actual === "image/jpeg" || actual === "image/jpg" ? "image/jpeg" : null;
  }
  return actual === expected ? expected : null;
}

export function contentLengthWithinLimit(rawContentLength, maxBytes) {
  const raw = String(rawContentLength ?? "").trim();
  if (!raw) return true;
  if (!/^\d+$/.test(raw)) return false;
  const size = Number(raw);
  return Number.isSafeInteger(size) && size >= 0 && size <= Number(maxBytes);
}

export function timingSafeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/** Primary secret first, then IMAGE_EDGE_SECRET_PREVIOUS for rotation window. */
export function resolveVerifySecrets(env) {
  const primary = String(env.IMAGE_EDGE_SECRET || "").trim();
  const previous = String(env.IMAGE_EDGE_SECRET_PREVIOUS || "").trim();
  const out = [];
  if (primary) out.push(primary);
  if (previous && previous !== primary) out.push(previous);
  return out;
}

export function parseSignedPath(pathname) {
  // /u/{exp}/{sig}/{b64path}
  const m = String(pathname || "").match(/^\/u\/(\d+)\/([A-Za-z0-9_-]+)\/([A-Za-z0-9_-]+)$/);
  if (!m) return null;
  return { exp: Number(m[1]), sig: m[2], b64path: m[3] };
}

export function validPath(path) {
  if (typeof path !== "string" || !path.startsWith("/") || path.includes("..") || path.includes("\\")) {
    return false;
  }
  // Match BFF is_edge_allowed_path: never accept double-slash (//) paths.
  if (path.includes("//")) return false;
  if (path.includes("://") || path.includes("@") || path.includes("?")) return false;
  // Contract allowlist only — do not accept arbitrary /img-* beyond documented prefixes.
  const okPrefix = ALLOWED_PREFIXES.some((p) => path.startsWith(p));
  if (!okPrefix) return false;
  const ext = path.split(".").pop()?.toLowerCase() || "";
  return ALLOWED_EXT.has(ext);
}

export function isAllowedMirrorHost(host) {
  const h = String(host || "").trim().toLowerCase();
  if (!h) return false;
  if (BUILTIN_MIRRORS.has(h)) return true;
  // Allow same-org worker emergency origins only (not open proxy).
  if (h.endsWith(".workers.dev")) return true;
  return false;
}

function isIpLiteral(hostname) {
  const host = String(hostname || "")
    .trim()
    .toLowerCase()
    .replace(/^\[|\]$/g, "");
  if (!host) return false;
  if (host.includes(":")) return true;
  const parts = host.split(".");
  return (
    parts.length === 4 &&
    parts.every((part) => /^\d{1,3}$/.test(part) && Number(part) >= 0 && Number(part) <= 255)
  );
}

function isPximgHost(hostname) {
  const host = String(hostname || "").trim().toLowerCase();
  return host === "pximg.net" || host.endsWith(".pximg.net");
}

export function canFollowUpstreamRedirect(completedRedirects) {
  const count = Number(completedRedirects);
  return Number.isInteger(count) && count >= 0 && count < MAX_UPSTREAM_REDIRECTS;
}

/**
 * Resolve one upstream redirect without widening its trust boundary.
 * `pximg` permits redirects between strict pximg.net subdomains. `same-host`
 * pins mirrors and worker fallbacks to the host selected by configuration.
 */
export function resolveSafeRedirectUrl(currentUrl, location, policy) {
  let target;
  try {
    target = new URL(String(location || ""), String(currentUrl || ""));
  } catch {
    return null;
  }

  if (target.protocol !== "https:") return null;
  if (target.username || target.password) return null;
  if (target.port && target.port !== "443") return null;

  const host = target.hostname.toLowerCase().replace(/\.$/, "");
  if (!host || isIpLiteral(host) || host === "localhost" || host.endsWith(".localhost")) {
    return null;
  }

  const mode = String(policy?.mode || "");
  if (mode === "pximg") {
    if (!isPximgHost(host)) return null;
  } else if (mode === "same-host") {
    const allowedHost = String(policy?.allowedHost || "")
      .trim()
      .toLowerCase()
      .replace(/\.$/, "");
    if (!allowedHost || host !== allowedHost) return null;
  } else {
    return null;
  }

  return target.toString();
}

/** Ordered unique hosts from FALLBACK_MIRROR_HOSTS (csv) + legacy FALLBACK_MIRROR_HOST. */
export function resolveFallbackHosts(env) {
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

/**
 * R2 modes (only when env.R2 binding is present):
 *   off | read_through | r2_only
 */
export function resolveR2Mode(env) {
  if (!env || !env.R2) return "off";
  const raw = String(env.R2_MODE || "read_through")
    .trim()
    .toLowerCase()
    .replace(/-/g, "_");
  if (raw === "off" || raw === "0" || raw === "false" || raw === "disabled") return "off";
  if (raw === "r2_only" || raw === "r2only" || raw === "only") return "r2_only";
  return "read_through";
}

/** Stable object key: pximg + path (same shape as Cache API key path). */
export function r2ObjectKey(path) {
  return `pximg${path}`;
}

/** Pure prewarm auth: expected secret + presented header value. */
export function authorizePrewarmSecrets(expected, got) {
  const e = String(expected || "").trim();
  if (!e) return false;
  return timingSafeEqual(String(got || "").trim(), e);
}

export function filterPrewarmPaths(rawPaths, max = 50) {
  const paths = [];
  const seen = new Set();
  const list = Array.isArray(rawPaths) ? rawPaths : [];
  for (const p of list) {
    const path = String(p || "");
    if (!validPath(path) || seen.has(path)) continue;
    seen.add(path);
    paths.push(path);
    if (paths.length >= max) break;
  }
  return paths;
}

/** Parse origin soft-circuit env knobs (defaults match Worker). */
export function originCircuitConfig(env) {
  const threshold = Math.max(1, Number(env?.ORIGIN_403_CIRCUIT_THRESHOLD || 8) || 8);
  const windowMs = Math.max(1000, Number(env?.ORIGIN_403_CIRCUIT_WINDOW_MS || 60000) || 60000);
  const openMs = Math.max(1000, Number(env?.ORIGIN_403_CIRCUIT_OPEN_MS || 30000) || 30000);
  return { threshold, windowMs, openMs };
}

export function isOriginCircuitOpen(state, nowMs) {
  return Number(state?.openUntilMs || 0) > nowMs;
}

/**
 * Pure origin circuit transition (no Date.now).
 * @param {{windowStartMs:number,samples:number,forbidden:number,openUntilMs:number}} state
 * @param {number} status HTTP status from origin
 * @param {{threshold:number,windowMs:number,openMs:number}} cfg
 * @param {number} nowMs
 */
export function noteOriginSample(state, status, cfg, nowMs) {
  const next = {
    windowStartMs: Number(state?.windowStartMs || 0),
    samples: Number(state?.samples || 0),
    forbidden: Number(state?.forbidden || 0),
    openUntilMs: Number(state?.openUntilMs || 0),
  };
  const threshold = Number(cfg.threshold);
  const windowMs = Number(cfg.windowMs);
  const openMs = Number(cfg.openMs);
  if (!next.windowStartMs || nowMs - next.windowStartMs > windowMs) {
    next.windowStartMs = nowMs;
    next.samples = 0;
    next.forbidden = 0;
  }
  next.samples += 1;
  if (Number(status) === 403) {
    next.forbidden += 1;
  }
  if (next.samples >= threshold && next.forbidden >= threshold) {
    next.openUntilMs = nowMs + openMs;
  }
  if (Number(status) === 200 && next.forbidden > 0) {
    next.forbidden = Math.max(0, next.forbidden - 1);
  }
  return next;
}

/** exp is inclusive-until: reject when nowSec >= exp. */
export function isSignedUrlExpired(exp, nowSec) {
  const e = Number(exp);
  const n = Number(nowSec);
  if (!Number.isFinite(e) || !Number.isFinite(n)) return true;
  return n >= e;
}

/**
 * Parse isolate-local rate limit from env (parity with api-worker).
 * RATE_LIMIT_RPM=0 → disabled. Empty → default 3000 rpm (image traffic).
 * Burst defaults to max(rpm/10, 50) when unset.
 */
export function parseRateLimitConfig(env) {
  const rawRpm = String(env?.RATE_LIMIT_RPM ?? "").trim();
  if (rawRpm === "0") {
    return { enabled: false, rpm: 0, burst: 0 };
  }
  if (!rawRpm) {
    return { enabled: true, rpm: 3000, burst: 300 };
  }
  const rpm = Number(rawRpm);
  if (!Number.isFinite(rpm) || rpm <= 0) {
    return { enabled: false, rpm: 0, burst: 0 };
  }
  const clampedRpm = Math.min(Math.floor(rpm), 1_000_000);
  const rawBurst = String(env?.RATE_LIMIT_BURST ?? "").trim();
  let burst = rawBurst ? Number(rawBurst) : Math.max(Math.floor(clampedRpm / 10), 50);
  if (!Number.isFinite(burst) || burst <= 0) {
    burst = Math.max(Math.floor(clampedRpm / 10), 50);
  }
  burst = Math.min(Math.floor(burst), clampedRpm);
  return { enabled: true, rpm: clampedRpm, burst };
}

/**
 * Token-bucket allow decision (pure; caller mutates bucket).
 * @param {{ tokens: number, updatedAtMs: number }} bucket
 * @param {{ rpm: number, burst: number }} cfg
 * @param {number} nowMs
 */
export function takeRateLimitToken(bucket, cfg, nowMs) {
  const rpm = Math.max(1, Number(cfg?.rpm) || 1);
  const burst = Math.max(1, Number(cfg?.burst) || 1);
  const now = Number.isFinite(Number(nowMs)) ? Number(nowMs) : 0;
  const prev = bucket && typeof bucket === "object" ? bucket : { tokens: burst, updatedAtMs: now };
  const prevAt = Number(prev.updatedAtMs);
  const updatedAtMs = Number.isFinite(prevAt) ? prevAt : now;
  const elapsedS = Math.max(0, (now - updatedAtMs) / 1000);
  const refill = (rpm / 60) * elapsedS;
  const prevTokens = Number(prev.tokens);
  let tokens = Math.min(burst, (Number.isFinite(prevTokens) ? prevTokens : burst) + refill);
  if (tokens >= 1) {
    tokens -= 1;
    return {
      allow: true,
      retryAfterS: 0,
      bucket: { tokens, updatedAtMs: now },
    };
  }
  const need = 1 - tokens;
  const retryAfterS = Math.max(1, Math.ceil((need * 60) / rpm));
  return {
    allow: false,
    retryAfterS,
    bucket: { tokens, updatedAtMs: now },
  };
}

/**
 * /healthz JSON body (no Request).
 * @param {{secrets:string[], circuitOpen:boolean, r2Mode:string, rateLimit?:{enabled:boolean,rpm?:number,burst?:number}}} input
 */
export function buildHealthzBody({ secrets, circuitOpen, r2Mode, rateLimit }) {
  const mode = String(r2Mode || "off");
  const rl =
    rateLimit && rateLimit.enabled
      ? { enabled: true, rpm: Number(rateLimit.rpm) || 0, burst: Number(rateLimit.burst) || 0 }
      : { enabled: false };
  const secretList = Array.isArray(secrets) ? secrets.filter((s) => String(s || "").trim()) : [];
  return {
    ok: true,
    service: "random-image-edge",
    // Parity with api-worker: empty IMAGE_EDGE_SECRET → not cutover-ready (signed /u will 500).
    secret_configured: secretList.length > 0,
    dual_secret: secretList.length > 1,
    origin_circuit_open: Boolean(circuitOpen),
    r2: mode !== "off",
    r2_mode: mode,
    rate_limit: rl,
  };
}

/** Cache API / R2 key path shape: GET {origin}/pximg{path} — path portion only. */
export function cachePathKey(path) {
  return r2ObjectKey(path);
}
