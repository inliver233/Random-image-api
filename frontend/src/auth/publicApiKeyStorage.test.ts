import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  getPublicDebugApiKey,
  publicApiKeyHeaders,
  setPublicDebugApiKey,
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
});
