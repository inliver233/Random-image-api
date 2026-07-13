import { dimensionsWithinLimit, parseImageDimensions } from "./pure.js";

export class ImageBodyError extends Error {
  constructor(status, message, deleteStored = false) {
    super(message);
    this.name = "ImageBodyError";
    this.status = status;
    this.deleteStored = deleteStored;
  }
}

function withTimeout(promise, timeoutMs, message) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(message)), timeoutMs);
    Promise.resolve(promise).then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

function toBytes(value) {
  if (value instanceof Uint8Array) return value;
  if (ArrayBuffer.isView(value)) {
    return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  }
  return new Uint8Array(value || []);
}

function concatChunks(chunks, totalBytes) {
  const result = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return result;
}

async function readImageHeader(reader, path, options) {
  const { maxBytes, expectedLength, limits, timeouts, readErrorStatus } = options;
  const chunks = [];
  let totalBytes = 0;
  let done = false;
  const maxHeaderBytes = Math.min(maxBytes, 64 * 1024);
  const deadlineMs = Date.now() + timeouts.totalMs;
  while (!done) {
    let next;
    try {
      const remainingMs = deadlineMs - Date.now();
      if (remainingMs <= 0) throw new ImageBodyError(504, "Image header total timeout");
      next = await withTimeout(
        reader.read(),
        Math.min(timeouts.idleMs, remainingMs),
        "Image header idle timeout",
      );
    } catch (error) {
      if (error instanceof ImageBodyError || /timeout/i.test(String(error?.message || ""))) {
        await reader.cancel("Image header timeout").catch(() => undefined);
        throw error instanceof ImageBodyError ? error : new ImageBodyError(504, error.message);
      }
      throw new ImageBodyError(readErrorStatus, "Image stream read failed");
    }
    done = Boolean(next.done);
    if (!done) {
      const chunk = toBytes(next.value);
      totalBytes += chunk.byteLength;
      if (totalBytes > maxBytes) {
        await reader.cancel("Image exceeds MAX_IMAGE_BYTES");
        throw new ImageBodyError(413, "Image exceeds MAX_IMAGE_BYTES", true);
      }
      if (expectedLength !== null && totalBytes > expectedLength) {
        await reader.cancel("Image exceeds its declared length");
        throw new ImageBodyError(502, "Image exceeds its declared length", true);
      }
      chunks.push(chunk);
    }
    const header = concatChunks(chunks, totalBytes);
    const dimensions = parseImageDimensions(path, header);
    if (dimensions.status === "ok") {
      if (!dimensionsWithinLimit(dimensions, limits)) {
        await reader.cancel("Image dimensions exceed configured limits");
        throw new ImageBodyError(413, "Image dimensions exceed configured limits", true);
      }
      return { chunks, totalBytes, done };
    }
    if (dimensions.status === "invalid" || done || totalBytes >= maxHeaderBytes) {
      await reader.cancel("Image header is invalid or incomplete");
      throw new ImageBodyError(415, "Image header is invalid or incomplete", true);
    }
  }
  throw new ImageBodyError(415, "Image header is invalid or incomplete", true);
}

export async function createValidatedImageStream(body, path, options) {
  const {
    maxBytes,
    collect,
    expectedLength = null,
    limits,
    timeouts,
    readErrorStatus = 502,
  } = options;
  if (!body) throw new ImageBodyError(readErrorStatus, "Image response has no body");
  if (collect && (!Number.isSafeInteger(expectedLength) || expectedLength < 0)) {
    throw new ImageBodyError(502, "Collected image requires a declared safe length");
  }
  const reader = body.getReader();
  const prefix = await readImageHeader(reader, path, {
    maxBytes,
    expectedLength,
    limits,
    timeouts,
    readErrorStatus,
  });
  const collected = collect ? new Uint8Array(expectedLength) : null;
  let totalBytes = prefix.totalBytes;
  let collectedOffset = 0;
  if (collected) {
    for (const chunk of prefix.chunks) {
      collected.set(chunk, collectedOffset);
      collectedOffset += chunk.byteLength;
    }
  }
  let settled = false;
  let controllerRef = null;
  let idleTimer = null;
  let totalTimer = null;
  let resolveCompletion;
  const completion = new Promise((resolve) => {
    resolveCompletion = resolve;
  });
  const settle = (result) => {
    if (settled) return;
    settled = true;
    if (idleTimer !== null) clearTimeout(idleTimer);
    if (totalTimer !== null) clearTimeout(totalTimer);
    resolveCompletion(result);
  };
  const fail = async (error) => {
    if (settled) return;
    try {
      await reader.cancel(error.message);
    } catch {
      // The completion result still releases the caller's cold-flight slot.
    }
    controllerRef?.error(error);
    settle({ complete: false, bytes: null, totalBytes, error });
  };
  const armIdleTimeout = () => {
    if (idleTimer !== null) clearTimeout(idleTimer);
    idleTimer = setTimeout(
      () => void fail(new ImageBodyError(504, "Image stream idle timeout")),
      timeouts.idleMs,
    );
  };
  const finishAtEof = (controller) => {
    if (expectedLength !== null && totalBytes !== expectedLength) {
      const error = new ImageBodyError(502, "Image ended before its declared length", true);
      controller.error(error);
      settle({ complete: false, bytes: null, totalBytes, error });
      return;
    }
    controller.close();
    settle({ complete: true, bytes: collected, totalBytes, error: null });
  };
  const stream = new ReadableStream({
    start(controller) {
      controllerRef = controller;
      totalTimer = setTimeout(
        () => void fail(new ImageBodyError(504, "Image stream total timeout")),
        timeouts.totalMs,
      );
      armIdleTimeout();
      for (const chunk of prefix.chunks) controller.enqueue(chunk);
      if (prefix.done) finishAtEof(controller);
    },
    async pull(controller) {
      if (settled) return;
      try {
        armIdleTimeout();
        const next = await reader.read();
        if (next.done) {
          finishAtEof(controller);
          return;
        }
        const chunk = toBytes(next.value);
        const nextTotal = totalBytes + chunk.byteLength;
        if (nextTotal > maxBytes) {
          await fail(new ImageBodyError(413, "Image exceeds MAX_IMAGE_BYTES", true));
          return;
        }
        if (expectedLength !== null && nextTotal > expectedLength) {
          await fail(new ImageBodyError(502, "Image exceeds its declared length", true));
          return;
        }
        totalBytes = nextTotal;
        if (collected) {
          collected.set(chunk, collectedOffset);
          collectedOffset += chunk.byteLength;
        }
        controller.enqueue(chunk);
      } catch (error) {
        if (settled) return;
        const streamError =
          error instanceof ImageBodyError
            ? error
            : new ImageBodyError(readErrorStatus, "Image stream read failed");
        controller.error(streamError);
        settle({ complete: false, bytes: null, totalBytes, error: streamError });
      }
    },
    async cancel(reason) {
      try {
        await reader.cancel(reason);
      } finally {
        settle({ complete: false, bytes: null, totalBytes, error: null });
      }
    },
  });
  return { stream, completion };
}

export async function readValidatedImageBuffer(body, path, options) {
  const opened = await createValidatedImageStream(body, path, { ...options, collect: true });
  const reader = opened.stream.getReader();
  try {
    while (!(await reader.read()).done) {
      // Drain the bounded stream. The completion result owns the preallocated buffer.
    }
  } catch {
    // The typed completion error preserves validation vs infrastructure classification.
  }
  const result = await opened.completion;
  if (!result.complete || !result.bytes) {
    throw result.error || new ImageBodyError(options.readErrorStatus || 502, "Image body was incomplete");
  }
  return result.bytes;
}
