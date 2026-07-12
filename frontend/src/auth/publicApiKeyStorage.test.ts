import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  getPublicDebugApiKey,
  isExternalEdgeOrCdnUrl,
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

  it("never attaches api_key to CF image-edge / pximg absolute URLs", () => {
    const signed =
      "https://img.example.com/i/img-original/img/2020/01/01/00/00/00/1_p0.jpg?exp=1&sig=abc";
    expect(isExternalEdgeOrCdnUrl(signed)).toBe(true);
    expect(withPublicApiKeyQuery(signed, "k1")).toBe(signed);

    const pximg = "https://i.pximg.net/img-original/img/2020/01/01/00/00/00/1_p0.jpg";
    expect(isExternalEdgeOrCdnUrl(pximg)).toBe(true);
    expect(withPublicApiKeyQuery(pximg, "k1")).toBe(pximg);

    const workers = "https://img-edge.example.workers.dev/i/x.jpg?exp=9&sig=z";
    expect(withPublicApiKeyQuery(workers, "k1")).toBe(workers);

    // Own API absolute path still gets the key (browser open of /i via BFF).
    expect(withPublicApiKeyQuery("https://api.example.com/i/1.jpg", "k1")).toContain("api_key=k1");
  });
});
