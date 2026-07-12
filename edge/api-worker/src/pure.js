/**
 * Pure helpers for random-image-api-proxy Worker (no Request/fetch).
 * Shared by src/index.js and offline Node tests.
 */

export const DEFAULT_ALLOWED = [
  "oauth.secure.pixiv.net",
  "app-api.pixiv.net",
  "public-api.secure.pixiv.net",
];

export const STRIP_REQ_HEADERS = new Set([
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

export function timingSafeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export function parseAllowedHosts(env) {
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

export function authorizeSecret(expected, got) {
  // Fail closed: empty PROXY_SECRET must not open the allowlisted Pixiv egress.
  const e = String(expected || "").trim();
  if (!e) return false;
  return timingSafeEqual(String(got || "").trim(), e);
}

/**
 * Parse isolate-local rate limit from env.
 * RATE_LIMIT_RPM=0 or empty → disabled (unlimited).
 * Defaults: 600 rpm, burst = max(rpm/10, 20) when burst unset.
 */
export function parseRateLimitConfig(env) {
  const rawRpm = String(env?.RATE_LIMIT_RPM ?? "").trim();
  if (!rawRpm) {
    return { enabled: true, rpm: 600, burst: 60 };
  }
  const rpm = Number(rawRpm);
  if (!Number.isFinite(rpm) || rpm <= 0) {
    return { enabled: false, rpm: 0, burst: 0 };
  }
  const clampedRpm = Math.min(Math.floor(rpm), 100_000);
  const rawBurst = String(env?.RATE_LIMIT_BURST ?? "").trim();
  let burst = rawBurst ? Number(rawBurst) : Math.max(Math.floor(clampedRpm / 10), 20);
  if (!Number.isFinite(burst) || burst <= 0) {
    burst = Math.max(Math.floor(clampedRpm / 10), 20);
  }
  burst = Math.min(Math.floor(burst), clampedRpm);
  return { enabled: true, rpm: clampedRpm, burst };
}

/**
 * Token-bucket allow decision (pure; caller mutates bucket).
 * @param {{ tokens: number, updatedAtMs: number }} bucket
 * @param {{ rpm: number, burst: number }} cfg
 * @param {number} nowMs
 * @returns {{ allow: boolean, retryAfterS: number, bucket: { tokens: number, updatedAtMs: number } }}
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
 * Parse /p/{host}/{path...}
 * Returns { host, pathWithQuery } or null.
 * @param {{ pathname?: string, search?: string }} url
 */
export function parseProxyPath(url) {
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

/** Whether host is in allowlist (exact). */
export function hostAllowed(host, allowed) {
  return Array.isArray(allowed) && allowed.includes(String(host || "").toLowerCase());
}

/**
 * Build upstream request headers: strip CF/client identity + cookie + gate secret.
 * @param {Iterable<[string, string]>} headerEntries request.headers.entries()
 * @param {string} host upstream Host
 * @returns {Map<string, string>} lower-case keys not used; preserves original names for kept headers
 */
export function buildUpstreamHeaderPairs(headerEntries, host) {
  const out = [];
  for (const [k, v] of headerEntries) {
    const key = String(k || "").toLowerCase();
    if (STRIP_REQ_HEADERS.has(key)) continue;
    // Avoid leaking browser cookies from random clients if worker URL is guessed.
    if (key === "cookie") continue;
    out.push([k, v]);
  }
  out.push(["Host", host]);
  return out;
}
