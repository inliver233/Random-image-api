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
  STRIP_REQ_HEADERS,
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
