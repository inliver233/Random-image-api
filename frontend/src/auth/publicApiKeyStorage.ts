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
 * Append ``api_key`` query when a public debug key is available.
 * For browser navigations (window.open) that cannot send X-API-Key headers.
 * Does not overwrite an existing api_key query param.
 */
export function withPublicApiKeyQuery(pathOrUrl: string, apiKey?: string | null): string {
  const key = String(apiKey ?? getPublicDebugApiKey() ?? "").trim();
  const raw = String(pathOrUrl || "");
  if (!key || !raw) return raw;

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
