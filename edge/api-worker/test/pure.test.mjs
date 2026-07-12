/**
 * Offline pure-function suite for API proxy Worker.
 * Run: node --test test/pure.test.mjs  (from edge/api-worker)
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, it } from "node:test";

import {
  authorizeSecret,
  buildUpstreamHeaderPairs,
  DEFAULT_ALLOWED,
  hostAllowed,
  parseAllowedHosts,
  parseProxyPath,
  parseRateLimitConfig,
  STRIP_REQ_HEADERS,
  takeRateLimitToken,
} from "../src/pure.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const vectors = JSON.parse(readFileSync(join(__dirname, "proxy_vectors.json"), "utf8"));

describe("parseAllowedHosts", () => {
  it("defaults when empty", () => {
    assert.deepEqual(parseAllowedHosts({}), vectors.default_hosts);
    assert.deepEqual(DEFAULT_ALLOWED, vectors.default_hosts);
  });
  it("parses CSV and lowercases", () => {
    assert.deepEqual(parseAllowedHosts({ ALLOWED_HOSTS: "App-API.Pixiv.Net, evil.com " }), [
      "app-api.pixiv.net",
      "evil.com",
    ]);
  });
  it("rejects host with port/slash/@", () => {
    for (const bad of vectors.host_reject_in_allowlist_parse) {
      // push path is internal; empty raw after filtering → defaults
      const hosts = parseAllowedHosts({ ALLOWED_HOSTS: bad });
      // if single invalid token, falls back to defaults
      if (bad.includes("/") || bad.includes(":") || bad.includes("@")) {
        assert.deepEqual(hosts, vectors.default_hosts);
      }
    }
  });
});

describe("parseProxyPath", () => {
  for (const row of vectors.parse_ok) {
    it(`ok ${row.pathname}${row.search}`, () => {
      const got = parseProxyPath({ pathname: row.pathname, search: row.search });
      assert.deepEqual(got, { host: row.host, pathWithQuery: row.pathWithQuery });
    });
  }
  for (const pathname of vectors.parse_reject) {
    it(`reject ${pathname}`, () => {
      assert.equal(parseProxyPath({ pathname, search: "" }), null);
    });
  }
});

describe("authorizeSecret + hostAllowed", () => {
  it("fail closed when no secret configured", () => {
    assert.equal(authorizeSecret("", "anything"), false);
    assert.equal(authorizeSecret("", ""), false);
    assert.equal(authorizeSecret("   ", "x"), false);
  });
  it("requires match when set", () => {
    assert.equal(authorizeSecret("sekrit", "sekrit"), true);
    assert.equal(authorizeSecret("sekrit", "wrong"), false);
    assert.equal(authorizeSecret("sekrit", ""), false);
  });
  it("host allowlist exact", () => {
    const allowed = parseAllowedHosts({});
    assert.equal(hostAllowed("app-api.pixiv.net", allowed), true);
    assert.equal(hostAllowed("evil.com", allowed), false);
  });
});

describe("rate limit token bucket", () => {
  it("defaults enabled 600 rpm", () => {
    const cfg = parseRateLimitConfig({});
    assert.equal(cfg.enabled, true);
    assert.equal(cfg.rpm, 600);
    assert.ok(cfg.burst >= 20);
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
    // 2 seconds at 60 rpm → +2 tokens, capped at burst 1
    const allowed = takeRateLimitToken(denied.bucket, cfg, 2000);
    assert.equal(allowed.allow, true);
  });
});

describe("strip headers set", () => {
  it("includes identity + secret headers", () => {
    assert.ok(STRIP_REQ_HEADERS.has("cf-connecting-ip"));
    assert.ok(STRIP_REQ_HEADERS.has("x-forwarded-for"));
    assert.ok(STRIP_REQ_HEADERS.has("x-proxy-secret"));
    assert.ok(STRIP_REQ_HEADERS.has("cookie") === false); // cookie stripped in buildUpstreamHeaderPairs
  });
});

describe("buildUpstreamHeaderPairs", () => {
  it("strips cookie, CF identity, and gate secret; sets Host", () => {
    const pairs = buildUpstreamHeaderPairs(
      [
        ["Authorization", "Bearer tok"],
        ["Cookie", "sid=abc"],
        ["cf-connecting-ip", "1.2.3.4"],
        ["X-Forwarded-For", "9.9.9.9"],
        ["X-Proxy-Secret", "sekrit"],
        ["Accept", "application/json"],
        ["Host", "client-spoof.example"],
      ],
      "app-api.pixiv.net",
    );
    const map = new Map(pairs.map(([k, v]) => [String(k).toLowerCase(), v]));
    assert.equal(map.get("authorization"), "Bearer tok");
    assert.equal(map.get("accept"), "application/json");
    assert.equal(map.get("host"), "app-api.pixiv.net");
    assert.equal(map.has("cookie"), false);
    assert.equal(map.has("cf-connecting-ip"), false);
    assert.equal(map.has("x-forwarded-for"), false);
    assert.equal(map.has("x-proxy-secret"), false);
  });
});
