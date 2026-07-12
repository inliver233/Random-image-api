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
