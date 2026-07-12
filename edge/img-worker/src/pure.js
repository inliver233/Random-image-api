/**
 * Pure helpers for random-image-edge Worker (no Request/fetch/Cache).
 * Shared by src/index.js and offline Node tests.
 */

export const ALLOWED_PREFIXES = ["/img-original/", "/img-master/", "/img-/", "/c/"];
export const ALLOWED_EXT = new Set(["jpg", "jpeg", "png", "gif", "webp"]);
export const BUILTIN_MIRRORS = new Set(["i.pixiv.cat", "i.pixiv.re", "i.pixiv.nl"]);

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
