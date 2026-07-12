import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  getPublicDebugApiKey,
  publicApiKeyHeaders,
  setPublicDebugApiKey,
  withPublicApiKeyQuery,
} from "./publicApiKeyStorage";

function clearPublicDebugKeyStorage() {
  try {
    sessionStorage.clear();
  } catch {
    // ignore
  }
}

describe("publicApiKeyStorage", () => {
  // beforeEach: other suites (Playground) may leave keys in shared jsdom sessionStorage
  beforeEach(clearPublicDebugKeyStorage);
  afterEach(clearPublicDebugKeyStorage);

  it("round-trips sessionStorage key", () => {
    expect(getPublicDebugApiKey()).toBe("");
    setPublicDebugApiKey("  abc  ");
    expect(getPublicDebugApiKey()).toBe("abc");
    setPublicDebugApiKey("");
    expect(getPublicDebugApiKey()).toBe("");
  });

  it("builds X-API-Key header only when set", () => {
    expect(publicApiKeyHeaders("")).toEqual({});
    expect(publicApiKeyHeaders("k")).toEqual({ "X-API-Key": "k" });
    setPublicDebugApiKey("from-storage");
    expect(publicApiKeyHeaders()).toEqual({ "X-API-Key": "from-storage" });
  });

  it("appends api_key query for browser navigations", () => {
    expect(withPublicApiKeyQuery("/i/1.jpg", "")).toBe("/i/1.jpg");
    expect(withPublicApiKeyQuery("/i/1.jpg", "k1")).toBe("/i/1.jpg?api_key=k1");
    expect(withPublicApiKeyQuery("/i/1.jpg?x=1", "k1")).toBe("/i/1.jpg?x=1&api_key=k1");
    expect(withPublicApiKeyQuery("/i/1.jpg?api_key=existing", "k1")).toBe("/i/1.jpg?api_key=existing");
    expect(withPublicApiKeyQuery("https://api.example.com/i/1.jpg", "k1")).toBe(
      "https://api.example.com/i/1.jpg?api_key=k1",
    );
    setPublicDebugApiKey("from-storage");
    expect(withPublicApiKeyQuery("/random?format=image")).toBe("/random?format=image&api_key=from-storage");
  });
});
