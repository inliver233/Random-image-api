/**
 * Offline pure-function suite for image edge Worker.
 * Run: node --test test/pure.test.mjs  (from edge/img-worker)
 */
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, it } from "node:test";

import {
  authorizePrewarmSecrets,
  buildHealthzBody,
  cacheWriteMaxBytes,
  cachePathKey,
  canFollowUpstreamRedirect,
  coldFlightWaitMs,
  coldStreamTimeouts,
  contentLengthWithinLimit,
  dimensionsWithinLimit,
  filterPrewarmPaths,
  isAllowedMirrorHost,
  isOriginCircuitOpen,
  isSignedUrlExpired,
  hasExpectedImageMagic,
  imageDimensionLimits,
  maxConcurrentColdFills,
  maxImageBytes,
  noteOriginSample,
  originCircuitConfig,
  parseRateLimitConfig,
  parseImageDimensions,
  parseSignedPath,
  resolveSafeRedirectUrl,
  resolveFallbackHosts,
  resolveR2Mode,
  resolveVerifySecrets,
  r2ObjectKey,
  storageWriteTimeoutMs,
  takeRateLimitToken,
  validPath,
  validatedImageContentType,
} from "../src/pure.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

function b64url(buf) {
  return Buffer.from(buf)
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function hmacSig(secret, msg) {
  return b64url(createHmac("sha256", secret).update(msg, "utf8").digest());
}

describe("validPath", () => {
  const vectors = JSON.parse(readFileSync(join(__dirname, "path_vectors.json"), "utf8"));

  for (const path of vectors.accept) {
    it(`accepts ${path}`, () => {
      assert.equal(validPath(path), true);
    });
  }

  for (const row of vectors.reject) {
    it(`rejects ${row.reason}: ${JSON.stringify(row.path)}`, () => {
      assert.equal(validPath(row.path), false);
    });
  }
});

describe("streaming response guards", () => {
  it("normalizes expected image MIME without accepting mismatches", () => {
    assert.equal(validatedImageContentType("/img-original/a.jpg", "image/jpeg; charset=binary"), "image/jpeg");
    assert.equal(validatedImageContentType("/img-original/a.jpg", "application/octet-stream"), "image/jpeg");
    assert.equal(validatedImageContentType("/img-original/a.png", "image/jpeg"), null);
    assert.equal(validatedImageContentType("/img-original/a.exe", "image/png"), null);
  });

  it("enforces bounded content length and max env clamp", () => {
    assert.equal(contentLengthWithinLimit("1024", 2048), true);
    assert.equal(contentLengthWithinLimit("2049", 2048), false);
    assert.equal(contentLengthWithinLimit("invalid", 2048), false);
    assert.equal(contentLengthWithinLimit(null, 2048), true);
    assert.equal(maxImageBytes({ MAX_IMAGE_BYTES: "2097152" }), 2097152);
    assert.equal(maxImageBytes({ MAX_IMAGE_BYTES: "1" }), 1024 * 1024);
    assert.equal(cacheWriteMaxBytes({ MAX_IMAGE_BYTES: "2097152", CACHE_WRITE_MAX_BYTES: "99999999" }), 2097152);
    assert.equal(maxConcurrentColdFills({ MAX_CONCURRENT_COLD_FILLS: "999" }), 64);
    assert.equal(coldFlightWaitMs({ COLD_FLIGHT_WAIT_MS: "99999" }), 5000);
    assert.deepEqual(coldStreamTimeouts({ COLD_STREAM_IDLE_TIMEOUT_MS: "2000", COLD_STREAM_TOTAL_TIMEOUT_MS: "5000" }), {
      idleMs: 2000,
      totalMs: 5000,
    });
    assert.equal(storageWriteTimeoutMs({ STORAGE_WRITE_TIMEOUT_MS: "99999" }), 30000);
  });

  it("requires path-specific image signatures", () => {
    assert.equal(hasExpectedImageMagic("/img-original/a.jpg", Uint8Array.from([0xff, 0xd8, 0xff])), true);
    assert.equal(
      hasExpectedImageMagic(
        "/img-original/a.png",
        Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
      ),
      true,
    );
    assert.equal(hasExpectedImageMagic("/img-original/a.gif", new TextEncoder().encode("GIF89a")), true);
    assert.equal(
      hasExpectedImageMagic(
        "/img-original/a.webp",
        Uint8Array.from([0x52, 0x49, 0x46, 0x46, 0, 0, 0, 0, 0x57, 0x45, 0x42, 0x50]),
      ),
      true,
    );
    assert.equal(hasExpectedImageMagic("/img-original/a.jpg", new TextEncoder().encode("<html>")), false);
  });

  it("parses image dimensions and applies pixel limits", () => {
    const jpeg = Uint8Array.from([
      0xff, 0xd8, 0xff, 0xc0, 0x00, 0x11, 0x08, 0x04, 0x00, 0x08, 0x00,
    ]);
    assert.deepEqual(parseImageDimensions("/img-original/a.jpg", jpeg), {
      status: "ok",
      width: 2048,
      height: 1024,
    });
    const png = new Uint8Array(24);
    png.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
    png.set(new TextEncoder().encode("IHDR"), 12);
    new DataView(png.buffer).setUint32(16, 640);
    new DataView(png.buffer).setUint32(20, 480);
    assert.deepEqual(parseImageDimensions("/img-original/a.png", png), {
      status: "ok",
      width: 640,
      height: 480,
    });
    const gif = new Uint8Array(10);
    gif.set(new TextEncoder().encode("GIF89a"));
    gif.set([0x40, 0x01, 0xf0, 0x00], 6);
    assert.deepEqual(parseImageDimensions("/img-original/a.gif", gif), {
      status: "ok",
      width: 320,
      height: 240,
    });
    const webp = new Uint8Array(30);
    webp.set(new TextEncoder().encode("RIFF"), 0);
    webp.set(new TextEncoder().encode("WEBPVP8X"), 8);
    webp.set([0x7f, 0x02, 0x00, 0xdf, 0x01, 0x00], 24);
    assert.deepEqual(parseImageDimensions("/img-original/a.webp", webp), {
      status: "ok",
      width: 640,
      height: 480,
    });
    const limits = imageDimensionLimits({ MAX_IMAGE_WIDTH: "1000", MAX_IMAGE_PIXELS: "1000000" });
    assert.equal(dimensionsWithinLimit({ status: "ok", width: 640, height: 480 }, limits), true);
    assert.equal(dimensionsWithinLimit({ status: "ok", width: 1200, height: 480 }, limits), false);
  });
});

describe("sign_vectors HMAC", () => {
  const doc = JSON.parse(readFileSync(join(__dirname, "sign_vectors.json"), "utf8"));
  for (const v of doc.vectors) {
    it(v.note || v.path, () => {
      assert.equal(hmacSig(doc.secret, v.msg), v.sig);
      assert.equal(validPath(v.path), true);
    });
  }
});

describe("resolveVerifySecrets", () => {
  it("primary only", () => {
    assert.deepEqual(resolveVerifySecrets({ IMAGE_EDGE_SECRET: "a" }), ["a"]);
  });
  it("dual secret rotation", () => {
    assert.deepEqual(
      resolveVerifySecrets({ IMAGE_EDGE_SECRET: "new", IMAGE_EDGE_SECRET_PREVIOUS: "old" }),
      ["new", "old"],
    );
  });
  it("dedupes identical previous", () => {
    assert.deepEqual(
      resolveVerifySecrets({ IMAGE_EDGE_SECRET: "same", IMAGE_EDGE_SECRET_PREVIOUS: "same" }),
      ["same"],
    );
  });
  it("empty when unset", () => {
    assert.deepEqual(resolveVerifySecrets({}), []);
  });
});

describe("parseSignedPath", () => {
  it("parses /u/exp/sig/b64", () => {
    assert.deepEqual(parseSignedPath("/u/1700003600/abc_SIG/b64pathHERE"), {
      exp: 1700003600,
      sig: "abc_SIG",
      b64path: "b64pathHERE",
    });
  });
  it("rejects garbage", () => {
    assert.equal(parseSignedPath("/healthz"), null);
    assert.equal(parseSignedPath("/u/x/y/z"), null);
  });
});

describe("resolveR2Mode", () => {
  it("off without binding", () => {
    assert.equal(resolveR2Mode({}), "off");
    assert.equal(resolveR2Mode({ R2_MODE: "read_through" }), "off");
  });
  it("read_through default with binding", () => {
    assert.equal(resolveR2Mode({ R2: {} }), "read_through");
  });
  it("r2_only", () => {
    assert.equal(resolveR2Mode({ R2: {}, R2_MODE: "r2_only" }), "r2_only");
  });
  it("explicit off", () => {
    assert.equal(resolveR2Mode({ R2: {}, R2_MODE: "off" }), "off");
  });
});

describe("mirrors + r2 key", () => {
  it("allows builtin and workers.dev", () => {
    assert.equal(isAllowedMirrorHost("i.pixiv.cat"), true);
    assert.equal(isAllowedMirrorHost("foo.workers.dev"), true);
    assert.equal(isAllowedMirrorHost("evil.com"), false);
  });
  it("orders fallback hosts unique", () => {
    assert.deepEqual(
      resolveFallbackHosts({
        FALLBACK_MIRROR_HOSTS: "i.pixiv.re,i.pixiv.cat,evil.com",
        FALLBACK_MIRROR_HOST: "i.pixiv.cat",
      }),
      ["i.pixiv.re", "i.pixiv.cat"],
    );
  });
  it("r2ObjectKey prefixes pximg", () => {
    assert.equal(r2ObjectKey("/img-original/x.jpg"), "pximg/img-original/x.jpg");
  });
});

describe("upstream redirect boundaries", () => {
  it("allows at most three redirect hops", () => {
    assert.equal(canFollowUpstreamRedirect(0), true);
    assert.equal(canFollowUpstreamRedirect(1), true);
    assert.equal(canFollowUpstreamRedirect(2), true);
    assert.equal(canFollowUpstreamRedirect(3), false);
    assert.equal(canFollowUpstreamRedirect(-1), false);
  });

  it("allows HTTPS pximg redirects within the strict pximg.net boundary", () => {
    assert.equal(
      resolveSafeRedirectUrl(
        "https://i.pximg.net/img-original/a.jpg",
        "https://sub.pximg.net/img-original/b.jpg",
        { mode: "pximg" },
      ),
      "https://sub.pximg.net/img-original/b.jpg",
    );
    assert.equal(
      resolveSafeRedirectUrl(
        "https://i.pximg.net/img-original/a.jpg",
        "/img-original/b.jpg",
        { mode: "pximg" },
      ),
      "https://i.pximg.net/img-original/b.jpg",
    );
  });

  it("rejects scheme, authority, port, IP, localhost, and suffix-confusion escapes", () => {
    const current = "https://i.pximg.net/img-original/a.jpg";
    for (const target of [
      "http://i.pximg.net/a.jpg",
      "https://user:pass@i.pximg.net/a.jpg",
      "https://i.pximg.net:8443/a.jpg",
      "https://127.0.0.1/a.jpg",
      "https://[::1]/a.jpg",
      "https://localhost/a.jpg",
      "https://localhost./a.jpg",
      "https://service.localhost/a.jpg",
      "https://evilpximg.net/a.jpg",
      "https://pximg.net.evil.example/a.jpg",
    ]) {
      assert.equal(resolveSafeRedirectUrl(current, target, { mode: "pximg" }), null, target);
    }
  });

  it("keeps mirror and worker redirects on the original host", () => {
    const current = "https://i.pixiv.cat/img-original/a.jpg";
    assert.equal(
      resolveSafeRedirectUrl(current, "/img-original/b.jpg", {
        mode: "same-host",
        allowedHost: "i.pixiv.cat",
      }),
      "https://i.pixiv.cat/img-original/b.jpg",
    );
    assert.equal(
      resolveSafeRedirectUrl(current, "https://other.workers.dev/a.jpg", {
        mode: "same-host",
        allowedHost: "i.pixiv.cat",
      }),
      null,
    );
  });

  it("rejects invalid locations and unknown policies", () => {
    assert.equal(
      resolveSafeRedirectUrl("https://i.pximg.net/a.jpg", "http://[", { mode: "pximg" }),
      null,
    );
    assert.equal(
      resolveSafeRedirectUrl("https://i.pximg.net/a.jpg", "/b.jpg", { mode: "unknown" }),
      null,
    );
  });
});

describe("prewarm gate + path filter", () => {
  it("authorizePrewarmSecrets", () => {
    assert.equal(authorizePrewarmSecrets("s", "s"), true);
    assert.equal(authorizePrewarmSecrets("s", "x"), false);
    assert.equal(authorizePrewarmSecrets("", "s"), false);
  });
  it("filterPrewarmPaths allowlists and caps", () => {
    const paths = filterPrewarmPaths(
      [
        "/img-original/a.jpg",
        "../evil.jpg",
        "/img-original/a.jpg",
        "/open/x.jpg",
        "/img-master/b.png",
      ],
      50,
    );
    assert.deepEqual(paths, ["/img-original/a.jpg", "/img-master/b.png"]);
  });
});

describe("signed URL expiry", () => {
  it("expires at exact second boundary (now >= exp)", () => {
    assert.equal(isSignedUrlExpired(100, 99), false);
    assert.equal(isSignedUrlExpired(100, 100), true);
    assert.equal(isSignedUrlExpired(100, 101), true);
  });
});

describe("buildHealthzBody", () => {
  it("reports dual_secret, r2_mode, and rate_limit fields", () => {
    assert.deepEqual(
      buildHealthzBody({
        secrets: ["a", "b"],
        circuitOpen: true,
        r2Mode: "read_through",
        rateLimit: { enabled: true, rpm: 3000, burst: 300 },
      }),
      {
        ok: true,
        service: "random-image-edge",
        secret_configured: true,
        dual_secret: true,
        origin_circuit_open: true,
        r2: true,
        r2_mode: "read_through",
        rate_limit: { enabled: true, rpm: 3000, burst: 300 },
      },
    );
    assert.deepEqual(
      buildHealthzBody({ secrets: ["a"], circuitOpen: false, r2Mode: "off" }),
      {
        ok: true,
        service: "random-image-edge",
        secret_configured: true,
        dual_secret: false,
        origin_circuit_open: false,
        r2: false,
        r2_mode: "off",
        rate_limit: { enabled: false },
      },
    );
    assert.equal(
      buildHealthzBody({ secrets: [], circuitOpen: false, r2Mode: "off" }).secret_configured,
      false,
    );
  });
});

describe("rate limit token bucket", () => {
  it("defaults enabled 3000 rpm", () => {
    const cfg = parseRateLimitConfig({});
    assert.equal(cfg.enabled, true);
    assert.equal(cfg.rpm, 3000);
    assert.ok(cfg.burst >= 50);
  });
  it("RATE_LIMIT_RPM=0 disables", () => {
    assert.deepEqual(parseRateLimitConfig({ RATE_LIMIT_RPM: "0" }), {
      enabled: false,
      rpm: 0,
      burst: 0,
    });
  });
  it("allows up to burst then rejects", () => {
    const cfg = { rpm: 60, burst: 2 };
    let bucket = { tokens: 2, updatedAtMs: 0 };
    const a = takeRateLimitToken(bucket, cfg, 0);
    assert.equal(a.allow, true);
    bucket = a.bucket;
    const b = takeRateLimitToken(bucket, cfg, 0);
    assert.equal(b.allow, true);
    bucket = b.bucket;
    const c = takeRateLimitToken(bucket, cfg, 0);
    assert.equal(c.allow, false);
    assert.ok(c.retryAfterS >= 1);
  });
  it("refills over time", () => {
    const cfg = { rpm: 60, burst: 1 };
    let bucket = { tokens: 0, updatedAtMs: 0 };
    const denied = takeRateLimitToken(bucket, cfg, 0);
    assert.equal(denied.allow, false);
    const allowed = takeRateLimitToken(denied.bucket, cfg, 2000);
    assert.equal(allowed.allow, true);
  });
});

describe("origin circuit pure", () => {
  it("parses defaults and opens after threshold 403s", () => {
    const cfg = originCircuitConfig({});
    assert.equal(cfg.threshold, 8);
    let state = { windowStartMs: 0, samples: 0, forbidden: 0, openUntilMs: 0 };
    const now = 1_000_000;
    for (let i = 0; i < 8; i++) {
      state = noteOriginSample(state, 403, cfg, now + i);
    }
    assert.equal(isOriginCircuitOpen(state, now + 8), true);
    assert.ok(state.openUntilMs > now);
  });
  it("200 cools forbidden counter", () => {
    const cfg = { threshold: 3, windowMs: 60_000, openMs: 30_000 };
    let state = { windowStartMs: 1000, samples: 2, forbidden: 2, openUntilMs: 0 };
    state = noteOriginSample(state, 200, cfg, 1500);
    assert.equal(state.forbidden, 1);
    assert.equal(isOriginCircuitOpen(state, 1500), false);
  });
});

describe("cachePathKey", () => {
  it("matches r2 object key shape", () => {
    assert.equal(cachePathKey("/img-original/x.jpg"), "pximg/img-original/x.jpg");
    assert.equal(cachePathKey("/img-original/x.jpg"), r2ObjectKey("/img-original/x.jpg"));
  });
});
