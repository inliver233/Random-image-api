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
