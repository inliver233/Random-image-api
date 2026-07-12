const STORAGE_KEY = "admin_public_debug_api_key";

/**
 * Optional public API key for admin Playground / Recommendation preview.
 * Used only when PUBLIC_API_KEY_REQUIRED is on; stored in sessionStorage (tab-scoped).
 */
export function getPublicDebugApiKey(): string {
  try {
    return String(sessionStorage.getItem(STORAGE_KEY) || "").trim();
  } catch {
    return "";
  }
}

export function setPublicDebugApiKey(value: string): void {
  try {
    const next = String(value || "").trim();
    if (!next) sessionStorage.removeItem(STORAGE_KEY);
    else sessionStorage.setItem(STORAGE_KEY, next);
  } catch {
    // ignore storage failures
  }
}

export function publicApiKeyHeaders(apiKey?: string | null): Record<string, string> {
  const key = String(apiKey ?? getPublicDebugApiKey() ?? "").trim();
  return key ? { "X-API-Key": key } : {};
}

/**
 * Absolute CF image-edge / foreign CDN URLs must never receive ?api_key=.
 * Edge workers authenticate via HMAC path signature, not the public API key;
 * attaching the key leaks it into CDN logs and third-party URLs.
 */
export function isExternalEdgeOrCdnUrl(pathOrUrl: string): boolean {
  const raw = String(pathOrUrl || "").trim();
  if (!/^https?:\/\//i.test(raw)) return false;
  try {
    const u = new URL(raw);
    const host = u.hostname.toLowerCase();
    // Relative-to-API paths are handled as non-absolute above.
    // Treat signed edge paths and common pximg hosts as external.
    if (u.pathname.includes("/i/") && (u.searchParams.has("exp") || u.searchParams.has("sig"))) {
      return true;
    }
    if (host.endsWith(".pximg.net") || host === "i.pximg.net") return true;
    if (host.includes("workers.dev") || host.includes("r2.dev")) return true;
    // Heuristic: host looks like dedicated image edge (img. / edge. / cdn.)
    if (/^(img|image|edge|cdn)\./i.test(host)) return true;
    return false;
  } catch {
    return false;
  }
}

/**
 * Append ``api_key`` query when a public debug key is available.
 * For browser navigations (window.open) that cannot send X-API-Key headers.
 * Does not overwrite an existing api_key query param.
 * Never attaches the key to absolute CF image-edge / pximg URLs (P0).
 */
export function withPublicApiKeyQuery(pathOrUrl: string, apiKey?: string | null): string {
  const key = String(apiKey ?? getPublicDebugApiKey() ?? "").trim();
  const raw = String(pathOrUrl || "");
  if (!key || !raw) return raw;
  if (isExternalEdgeOrCdnUrl(raw)) return raw;

  try {
    if (/^https?:\/\//i.test(raw)) {
      const u = new URL(raw);
      if (!u.searchParams.get("api_key")) u.searchParams.set("api_key", key);
      return u.toString();
    }
    const hashIdx = raw.indexOf("#");
    const beforeHash = hashIdx >= 0 ? raw.slice(0, hashIdx) : raw;
    const hash = hashIdx >= 0 ? raw.slice(hashIdx) : "";
    const qIdx = beforeHash.indexOf("?");
    const path = qIdx >= 0 ? beforeHash.slice(0, qIdx) : beforeHash;
    const sp = new URLSearchParams(qIdx >= 0 ? beforeHash.slice(qIdx + 1) : "");
    if (!sp.get("api_key")) sp.set("api_key", key);
    const qs = sp.toString();
    return `${path}${qs ? `?${qs}` : ""}${hash}`;
  } catch {
    return raw;
  }
}
