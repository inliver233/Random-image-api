import { ApiError } from "../api/client";

/** Extract request_id from API errors for admin alerts. */
export function requestIdFromError(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  return err.body?.request_id ? String(err.body.request_id) : null;
}

/** Human-readable message from API / generic errors. */
export function messageFromError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "未知错误";
}

/** Optional description line: `请求ID: …` when present. */
export function requestIdDescription(err: unknown): string {
  const rid = requestIdFromError(err);
  return rid ? `请求ID: ${rid}` : "";
}

/** Description preferring request id, else message (import history style). */
export function requestIdOrMessageDescription(err: unknown): string {
  return requestIdDescription(err) || messageFromError(err);
}

/** `请求ID: …（message）` when id present, else message only. */
export function requestIdWithMessageDescription(err: unknown): string {
  const rid = requestIdFromError(err);
  const msg = messageFromError(err);
  return rid ? `请求ID: ${rid}（${msg}）` : msg;
}
