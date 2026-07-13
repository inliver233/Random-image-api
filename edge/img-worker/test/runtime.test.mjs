import assert from "node:assert/strict";
import { createHmac, webcrypto } from "node:crypto";
import { afterEach, describe, it } from "node:test";

import worker from "../src/index.js";

const originalFetch = globalThis.fetch;
const originalCaches = globalThis.caches;
if (!globalThis.crypto) globalThis.crypto = webcrypto;

afterEach(() => {
  globalThis.fetch = originalFetch;
  globalThis.caches = originalCaches;
});

function b64url(value) {
  return Buffer.from(value)
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function signedRequest(method, path, secret = "test-secret") {
  const exp = Math.floor(Date.now() / 1000) + 3600;
  const sig = b64url(createHmac("sha256", secret).update(`${exp}\n${path}`).digest());
  return new Request(`https://edge.test/u/${exp}/${sig}/${b64url(path)}`, { method });
}

function context() {
  return {
    pending: [],
    waitUntil(promise) {
      this.pending.push(Promise.resolve(promise));
    },
  };
}

const JPEG_PREFIX = Uint8Array.from([
  0xff, 0xd8,
  0xff, 0xc0, 0x00, 0x11, 0x08, 0x00, 0x01, 0x00, 0x01,
  0x03, 0x01, 0x11, 0x00, 0x02, 0x11, 0x00, 0x03, 0x11, 0x00,
]);

describe("public cold path runtime", () => {
  it("returns GET response before the origin stream completes", async () => {
    let releaseTail;
    const tail = new Promise((resolve) => {
      releaseTail = resolve;
    });
    globalThis.fetch = async (_url, init) => {
      assert.equal(init.method, "GET");
      const body = new ReadableStream({
        async start(controller) {
          controller.enqueue(JPEG_PREFIX);
          await tail;
          controller.enqueue(new Uint8Array([4, 5]));
          controller.close();
        },
      });
      return new Response(body, { status: 200, headers: { "Content-Type": "image/jpeg" } });
    };
    globalThis.caches = {
      default: {
        async match() {
          return null;
        },
        async put(_key, response) {
          await response.arrayBuffer();
        },
        async delete() {},
      },
    };
    const ctx = context();
    const response = await Promise.race([
      worker.fetch(signedRequest("GET", "/img-original/a.jpg"), { IMAGE_EDGE_SECRET: "test-secret" }, ctx),
      new Promise((_, reject) => setTimeout(() => reject(new Error("cold GET waited for full body")), 100)),
    ]);
    assert.equal(response.status, 200);
    const reader = response.body.getReader();
    const first = await reader.read();
    assert.deepEqual(first.value, JPEG_PREFIX);
    releaseTail();
    const second = await reader.read();
    assert.deepEqual([...second.value], [4, 5]);
    await reader.read();
    await Promise.all(ctx.pending);
  });

  it("uses upstream HEAD and never reads or caches a full body", async () => {
    let cachePuts = 0;
    globalThis.fetch = async (_url, init) => {
      assert.equal(init.method, "HEAD");
      return new Response(null, {
        status: 200,
        headers: { "Content-Type": "image/png", "Content-Length": "1234" },
      });
    };
    globalThis.caches = {
      default: {
        async match() {
          return null;
        },
        async put() {
          cachePuts += 1;
        },
      },
    };
    const ctx = context();
    const response = await worker.fetch(
      signedRequest("HEAD", "/img-original/a.png"),
      { IMAGE_EDGE_SECRET: "test-secret" },
      ctx,
    );
    assert.equal(response.status, 200);
    assert.equal(response.body, null);
    assert.equal(response.headers.get("content-length"), "1234");
    assert.equal(cachePuts, 0);
    assert.equal(ctx.pending.length, 0);
  });

  it("uses R2 head metadata without calling get for HEAD", async () => {
    let headCalls = 0;
    let getCalls = 0;
    globalThis.fetch = async () => {
      throw new Error("origin must not be called on R2 HEAD hit");
    };
    globalThis.caches = { default: { match: async () => null, put: async () => {} } };
    const r2 = {
      async head() {
        headCalls += 1;
        return {
          size: 55,
          httpMetadata: { contentType: "image/jpeg" },
          customMetadata: { edgeValidation: "v1" },
          httpEtag: '"etag"',
        };
      },
      async get() {
        getCalls += 1;
        throw new Error("GET must not run for HEAD");
      },
    };
    const response = await worker.fetch(
      signedRequest("HEAD", "/img-original/a.jpg"),
      { IMAGE_EDGE_SECRET: "test-secret", R2: r2 },
      context(),
    );
    assert.equal(response.status, 200);
    assert.equal(response.body, null);
    assert.equal(response.headers.get("content-length"), "55");
    assert.equal(headCalls, 1);
    assert.equal(getCalls, 0);
  });

  it("rejects a declared oversized origin before consuming its body", async () => {
    let canceled = false;
    globalThis.fetch = async () =>
      new Response(
        new ReadableStream({
          pull(controller) {
            controller.enqueue(new Uint8Array([1]));
          },
          cancel() {
            canceled = true;
          },
        }),
        { status: 200, headers: { "Content-Type": "image/jpeg", "Content-Length": "2097152" } },
      );
    globalThis.caches = { default: { match: async () => null, put: async () => {} } };
    const response = await worker.fetch(
      signedRequest("GET", "/img-original/a.jpg"),
      { IMAGE_EDGE_SECRET: "test-secret", MAX_IMAGE_BYTES: "1048576" },
      context(),
    );
    assert.equal(response.status, 413);
    assert.equal(canceled, true);
  });

  it("does not drain the origin while the client is paused", async () => {
    let pulls = 0;
    const totalChunks = 128;
    globalThis.fetch = async () =>
      new Response(
        new ReadableStream({
          pull(controller) {
            pulls += 1;
            if (pulls === 1) controller.enqueue(JPEG_PREFIX);
            else if (pulls <= totalChunks) controller.enqueue(new Uint8Array(64 * 1024));
            else controller.close();
          },
        }),
        { status: 200, headers: { "Content-Type": "image/jpeg" } },
      );
    globalThis.caches = {
      default: {
        match: async () => null,
        put: async () => assert.fail("unknown length must not persist"),
        delete: async () => {},
      },
    };
    const ctx = context();
    const response = await worker.fetch(
      signedRequest("GET", "/img-original/backpressure.jpg"),
      { IMAGE_EDGE_SECRET: "test-secret" },
      ctx,
    );
    const reader = response.body.getReader();
    await reader.read();
    await new Promise((resolve) => setTimeout(resolve, 25));
    assert.ok(pulls < totalChunks, `origin unexpectedly drained ${pulls} chunks`);
    await reader.cancel("test complete");
    await Promise.all(ctx.pending);
  });

  it("deduplicates a same-key cold fill and bounds follower waiting", async () => {
    let originCalls = 0;
    let releaseTail;
    const tail = new Promise((resolve) => {
      releaseTail = resolve;
    });
    globalThis.fetch = async () => {
      originCalls += 1;
      return new Response(
        new ReadableStream({
          async start(controller) {
            controller.enqueue(JPEG_PREFIX);
            await tail;
            controller.close();
          },
        }),
        { status: 200, headers: { "Content-Type": "image/jpeg" } },
      );
    };
    globalThis.caches = {
      default: { match: async () => null, put: async () => {}, delete: async () => {} },
    };
    const env = { IMAGE_EDGE_SECRET: "test-secret", COLD_FLIGHT_WAIT_MS: "10" };
    const leaderCtx = context();
    const leader = await worker.fetch(signedRequest("GET", "/img-original/same.jpg"), env, leaderCtx);
    const followers = await Promise.all(
      Array.from({ length: 49 }, () =>
        worker.fetch(signedRequest("GET", "/img-original/same.jpg"), env, context()),
      ),
    );
    assert.ok(followers.every((response) => response.status === 503));
    assert.ok(followers.every((response) => response.headers.get("retry-after") === "1"));
    assert.equal(originCalls, 1);
    releaseTail();
    await leader.arrayBuffer();
    await Promise.all(leaderCtx.pending);
  });

  it("persists one bounded buffer after the client finishes a small image", async () => {
    const bytes = new Uint8Array([...JPEG_PREFIX, 1, 2, 3, 4]);
    const cacheWrites = [];
    const r2Writes = [];
    globalThis.fetch = async () =>
      new Response(bytes, {
        status: 200,
        headers: { "Content-Type": "image/jpeg", "Content-Length": String(bytes.byteLength) },
      });
    globalThis.caches = {
      default: {
        match: async () => null,
        delete: async () => {},
        async put(_key, response) {
          cacheWrites.push({
            headers: new Headers(response.headers),
            bytes: new Uint8Array(await response.arrayBuffer()),
          });
        },
      },
    };
    const r2 = {
      get: async () => null,
      async put(key, body, options) {
        r2Writes.push({ key, bytes: new Uint8Array(body), options });
      },
    };
    const ctx = context();
    const response = await worker.fetch(
      signedRequest("GET", "/img-original/small.jpg"),
      { IMAGE_EDGE_SECRET: "test-secret", R2: r2 },
      ctx,
    );
    assert.deepEqual(new Uint8Array(await response.arrayBuffer()), bytes);
    await Promise.all(ctx.pending);
    assert.equal(cacheWrites.length, 1);
    assert.equal(cacheWrites[0].headers.get("x-edge-validation"), "v1");
    assert.deepEqual(cacheWrites[0].bytes, bytes);
    assert.equal(r2Writes.length, 1);
    assert.equal(r2Writes[0].options.customMetadata.edgeValidation, "v1");
    assert.deepEqual(r2Writes[0].bytes, bytes);
  });

  it("errors the client stream when the declared length is shorter or longer than the body", async () => {
    const cases = [
      {
        path: "/img-original/too-long.jpg",
        declared: JPEG_PREFIX.byteLength + 1,
        chunks: [JPEG_PREFIX, Uint8Array.from([1, 2])],
      },
      {
        path: "/img-original/too-short.jpg",
        declared: JPEG_PREFIX.byteLength + 2,
        chunks: [JPEG_PREFIX, Uint8Array.from([1])],
      },
    ];
    for (const item of cases) {
      let cachePuts = 0;
      globalThis.fetch = async () =>
        new Response(
          new ReadableStream({
            start(controller) {
              for (const chunk of item.chunks) controller.enqueue(chunk);
              controller.close();
            },
          }),
          {
            status: 200,
            headers: { "Content-Type": "image/jpeg", "Content-Length": String(item.declared) },
          },
        );
      globalThis.caches = {
        default: {
          match: async () => null,
          delete: async () => {},
          async put() {
            cachePuts += 1;
          },
        },
      };
      const ctx = context();
      const response = await worker.fetch(
        signedRequest("GET", item.path),
        { IMAGE_EDGE_SECRET: "test-secret" },
        ctx,
      );
      assert.equal(response.status, 200);
      await assert.rejects(response.arrayBuffer(), /declared length/);
      await Promise.all(ctx.pending);
      assert.equal(cachePuts, 0);
    }
  });

  it("deletes an R2 object when its validated size does not match the streamed body", async () => {
    let deleteCalls = 0;
    const ctx = context();
    globalThis.fetch = async () => assert.fail("origin must not run on an R2 hit");
    globalThis.caches = {
      default: { match: async () => null, put: async () => {}, delete: async () => {} },
    };
    const response = await worker.fetch(
      signedRequest("GET", "/img-original/r2-corrupt.jpg"),
      {
        IMAGE_EDGE_SECRET: "test-secret",
        R2: {
          async get() {
            return {
              size: JPEG_PREFIX.byteLength + 1,
              httpMetadata: { contentType: "image/jpeg" },
              customMetadata: { edgeValidation: "v1" },
              body: new ReadableStream({
                start(controller) {
                  controller.enqueue(JPEG_PREFIX);
                  controller.enqueue(Uint8Array.from([1, 2]));
                  controller.close();
                },
              }),
            };
          },
          async delete() {
            deleteCalls += 1;
          },
        },
      },
      ctx,
    );
    assert.equal(response.status, 200);
    await assert.rejects(response.arrayBuffer(), /declared length/);
    await Promise.all(ctx.pending);
    assert.equal(deleteCalls, 1);
  });

  it("falls back on an R2 read error without deleting the object", async () => {
    let originCalls = 0;
    let deleteCalls = 0;
    const bytes = new Uint8Array([...JPEG_PREFIX, 5]);
    globalThis.fetch = async () => {
      originCalls += 1;
      return new Response(bytes, {
        status: 200,
        headers: { "Content-Type": "image/jpeg", "Content-Length": String(bytes.byteLength) },
      });
    };
    globalThis.caches = {
      default: { match: async () => null, put: async () => {}, delete: async () => {} },
    };
    const ctx = context();
    const response = await worker.fetch(
      signedRequest("GET", "/img-original/r2-transient.jpg"),
      {
        IMAGE_EDGE_SECRET: "test-secret",
        R2: {
          async get() {
            return {
              size: bytes.byteLength,
              httpMetadata: { contentType: "image/jpeg" },
              customMetadata: { edgeValidation: "v1" },
              body: new ReadableStream({
                pull() {
                  throw new Error("transient R2 stream failure");
                },
              }),
            };
          },
          async delete() {
            deleteCalls += 1;
          },
          async put() {},
        },
      },
      ctx,
    );
    assert.deepEqual(new Uint8Array(await response.arrayBuffer()), bytes);
    await Promise.all(ctx.pending);
    assert.equal(originCalls, 1);
    assert.equal(deleteCalls, 0);
  });

  it("releases cold-fill capacity after stalled clients hit the idle deadline", async () => {
    let originCalls = 0;
    globalThis.fetch = async () => {
      originCalls += 1;
      return new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(JPEG_PREFIX);
          },
        }),
        { status: 200, headers: { "Content-Type": "image/jpeg" } },
      );
    };
    globalThis.caches = {
      default: { match: async () => null, put: async () => {}, delete: async () => {} },
    };
    const env = {
      IMAGE_EDGE_SECRET: "test-secret",
      MAX_CONCURRENT_COLD_FILLS: "4",
      COLD_STREAM_IDLE_TIMEOUT_MS: "1000",
      COLD_STREAM_TOTAL_TIMEOUT_MS: "1000",
    };
    const contexts = [];
    const stalled = [];
    for (let i = 0; i < 4; i += 1) {
      const ctx = context();
      contexts.push(ctx);
      stalled.push(await worker.fetch(signedRequest("GET", `/img-original/stall-${i}.jpg`), env, ctx));
    }
    const saturated = await worker.fetch(
      signedRequest("GET", "/img-original/saturated.jpg"),
      env,
      context(),
    );
    assert.equal(saturated.status, 503);
    await new Promise((resolve) => setTimeout(resolve, 1100));
    await Promise.all(contexts.flatMap((ctx) => ctx.pending));
    const recoveryCtx = context();
    const recovered = await worker.fetch(
      signedRequest("GET", "/img-original/recovered.jpg"),
      env,
      recoveryCtx,
    );
    assert.equal(recovered.status, 200);
    await recovered.body.cancel();
    await Promise.all(recoveryCtx.pending);
    assert.equal(originCalls, 5);
    void stalled;
  });

  it("rejects HTML disguised as a JPEG before returning an image response", async () => {
    globalThis.fetch = async () =>
      new Response(new TextEncoder().encode("<html>not an image</html>"), {
        status: 200,
        headers: { "Content-Type": "image/jpeg" },
      });
    globalThis.caches = {
      default: { match: async () => null, put: async () => {}, delete: async () => {} },
    };
    const response = await worker.fetch(
      signedRequest("GET", "/img-original/fake.jpg"),
      { IMAGE_EDGE_SECRET: "test-secret" },
      context(),
    );
    assert.equal(response.status, 415);
  });

  it("rejects image dimensions above the configured limit before returning the body", async () => {
    const wideJpeg = Uint8Array.from([
      0xff, 0xd8, 0xff, 0xc0, 0x00, 0x11, 0x08, 0x00, 0x10, 0x08, 0x00,
      0x03, 0x01, 0x11, 0x00, 0x02, 0x11, 0x00, 0x03, 0x11, 0x00,
    ]);
    globalThis.fetch = async () =>
      new Response(wideJpeg, {
        status: 200,
        headers: { "Content-Type": "image/jpeg", "Content-Length": String(wideJpeg.byteLength) },
      });
    globalThis.caches = {
      default: { match: async () => null, put: async () => {}, delete: async () => {} },
    };
    const response = await worker.fetch(
      signedRequest("GET", "/img-original/wide.jpg"),
      { IMAGE_EDGE_SECRET: "test-secret", MAX_IMAGE_WIDTH: "1024" },
      context(),
    );
    assert.equal(response.status, 413);
  });

  it("aborts an unknown-length image at the runtime byte limit without persisting it", async () => {
    let cachePuts = 0;
    let emitted = 0;
    globalThis.fetch = async () =>
      new Response(
        new ReadableStream({
          pull(controller) {
            emitted += 1;
            if (emitted === 1) controller.enqueue(JPEG_PREFIX);
            else if (emitted <= 18) controller.enqueue(new Uint8Array(64 * 1024));
            else controller.close();
          },
        }),
        { status: 200, headers: { "Content-Type": "image/jpeg" } },
      );
    globalThis.caches = {
      default: {
        match: async () => null,
        delete: async () => {},
        async put() {
          cachePuts += 1;
        },
      },
    };
    const ctx = context();
    const response = await worker.fetch(
      signedRequest("GET", "/img-original/chunked.jpg"),
      { IMAGE_EDGE_SECRET: "test-secret", MAX_IMAGE_BYTES: String(1024 * 1024) },
      ctx,
    );
    await assert.rejects(response.arrayBuffer(), /MAX_IMAGE_BYTES/);
    await Promise.all(ctx.pending);
    assert.equal(cachePuts, 0);
  });

  it("purges a legacy cache entry without a validation marker", async () => {
    let originCalls = 0;
    let cacheDeletes = 0;
    const bytes = new Uint8Array([...JPEG_PREFIX, 9]);
    globalThis.fetch = async () => {
      originCalls += 1;
      return new Response(bytes, {
        status: 200,
        headers: { "Content-Type": "image/jpeg", "Content-Length": String(bytes.byteLength) },
      });
    };
    let firstMatch = true;
    globalThis.caches = {
      default: {
        async match() {
          if (!firstMatch) return null;
          firstMatch = false;
          return new Response(bytes, {
            headers: { "Content-Type": "image/jpeg", "Content-Length": String(bytes.byteLength) },
          });
        },
        async delete() {
          cacheDeletes += 1;
        },
        async put() {},
      },
    };
    const ctx = context();
    const response = await worker.fetch(
      signedRequest("GET", "/img-original/legacy.jpg"),
      { IMAGE_EDGE_SECRET: "test-secret" },
      ctx,
    );
    await response.arrayBuffer();
    await Promise.all(ctx.pending);
    assert.equal(cacheDeletes, 1);
    assert.equal(originCalls, 1);
  });

  it("reports an R2-only infrastructure error as unavailable, not missing", async () => {
    globalThis.fetch = async () => assert.fail("origin must not run in r2_only mode");
    globalThis.caches = {
      default: { match: async () => null, put: async () => {}, delete: async () => {} },
    };
    const response = await worker.fetch(
      signedRequest("GET", "/img-original/r2.jpg"),
      {
        IMAGE_EDGE_SECRET: "test-secret",
        R2_MODE: "r2_only",
        R2: {
          async get() {
            throw new Error("R2 down");
          },
        },
      },
      context(),
    );
    assert.equal(response.status, 503);
  });
});

describe("prewarm runtime guards", () => {
  it("validates and bounds an origin image before writing R2 and Cache", async () => {
    const bytes = new Uint8Array([...JPEG_PREFIX, 7, 8]);
    const r2Writes = [];
    let cachePuts = 0;
    globalThis.fetch = async () =>
      new Response(bytes, {
        status: 200,
        headers: { "Content-Type": "image/jpeg", "Content-Length": String(bytes.byteLength) },
      });
    globalThis.caches = {
      default: {
        async put(_key, response) {
          cachePuts += 1;
          assert.equal(response.headers.get("x-edge-validation"), "v1");
          assert.deepEqual(new Uint8Array(await response.arrayBuffer()), bytes);
        },
      },
    };
    const r2 = {
      get: async () => null,
      async put(key, body, options) {
        r2Writes.push({ key, bytes: new Uint8Array(body), options });
      },
    };
    const request = new Request("https://edge.test/v1/prewarm", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Prewarm-Secret": "prewarm-secret" },
      body: JSON.stringify({ paths: ["/img-original/prewarm.jpg"] }),
    });
    const response = await worker.fetch(
      request,
      { IMAGE_EDGE_SECRET: "prewarm-secret", R2: r2 },
      context(),
    );
    assert.equal(response.status, 200);
    assert.equal(cachePuts, 1);
    assert.equal(r2Writes.length, 1);
    assert.equal(r2Writes[0].options.customMetadata.edgeValidation, "v1");
    assert.deepEqual(r2Writes[0].bytes, bytes);
  });

  it("does not buffer or persist a declared oversized prewarm image", async () => {
    let canceled = false;
    let r2Puts = 0;
    globalThis.fetch = async () =>
      new Response(
        new ReadableStream({
          pull(controller) {
            controller.enqueue(JPEG_PREFIX);
          },
          cancel() {
            canceled = true;
          },
        }),
        {
          status: 200,
          headers: { "Content-Type": "image/jpeg", "Content-Length": String(9 * 1024 * 1024) },
        },
      );
    globalThis.caches = { default: { put: async () => assert.fail("cache must not be written") } };
    const request = new Request("https://edge.test/v1/prewarm", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Prewarm-Secret": "prewarm-secret" },
      body: JSON.stringify({ paths: ["/img-original/large.jpg"] }),
    });
    const response = await worker.fetch(
      request,
      {
        IMAGE_EDGE_SECRET: "prewarm-secret",
        R2: {
          get: async () => null,
          async put() {
            r2Puts += 1;
          },
        },
      },
      context(),
    );
    assert.equal(response.status, 200);
    const result = await response.json();
    assert.equal(result.failed, 1);
    assert.equal(canceled, true);
    assert.equal(r2Puts, 0);
  });

  it("deletes a deterministically corrupt validated R2 object during prewarm", async () => {
    let deleteCalls = 0;
    globalThis.fetch = async () => assert.fail("prewarm must not call origin for an R2 hit");
    globalThis.caches = { default: { put: async () => assert.fail("corrupt object must not warm cache") } };
    const request = new Request("https://edge.test/v1/prewarm", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Prewarm-Secret": "prewarm-secret" },
      body: JSON.stringify({ paths: ["/img-original/corrupt.jpg"] }),
    });
    const fake = new TextEncoder().encode("<html>not jpeg</html>");
    const response = await worker.fetch(
      request,
      {
        IMAGE_EDGE_SECRET: "prewarm-secret",
        R2: {
          async get() {
            return {
              size: fake.byteLength,
              httpMetadata: { contentType: "image/jpeg" },
              customMetadata: { edgeValidation: "v1" },
              body: new Response(fake).body,
            };
          },
          async delete() {
            deleteCalls += 1;
          },
        },
      },
      context(),
    );
    assert.equal(response.status, 200);
    const result = await response.json();
    assert.equal(result.failed, 1);
    assert.equal(deleteCalls, 1);
  });
});
