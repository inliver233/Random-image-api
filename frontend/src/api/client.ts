import { clearAdminToken, getAdminToken } from "../auth/tokenStorage";

export type ApiErrorBody = {
  ok: false;
  code: string;
  message: string;
  request_id: string;
  details: Record<string, unknown>;
};

export class ApiError extends Error {
  status: number;
  body: ApiErrorBody | null;

  constructor(message: string, options: { status: number; body?: ApiErrorBody | null }) {
    super(message);
    this.name = "ApiError";
    this.status = options.status;
    this.body = options.body ?? null;
  }
}

function containsChinese(text: string): boolean {
  return /[\u4e00-\u9fff]/.test(text);
}

function translateErrorCode(code: string): string | null {
  switch (code) {
    case "BAD_REQUEST":
      return "请求参数错误";
    case "UNAUTHORIZED":
      return "未登录或登录已失效";
    case "FORBIDDEN":
      return "无权限";
    case "NOT_FOUND":
      return "资源不存在";
    case "RATE_LIMITED":
      return "请求过于频繁，请稍后再试";
    case "INTERNAL_ERROR":
      return "服务器内部错误";
    case "NO_MATCH":
      return "没有匹配的图片";
    case "UPSTREAM_STREAM_ERROR":
      return "上游图片流错误";
    case "UPSTREAM_403":
      return "上游拒绝访问（403）";
    case "UPSTREAM_404":
      return "上游资源不存在（404）";
    case "UPSTREAM_RATE_LIMIT":
      return "上游限流，请稍后再试";
    case "INVALID_UPLOAD_TYPE":
      return "不支持的上传类型";
    case "PAYLOAD_TOO_LARGE":
      return "上传内容过大";
    case "UNSUPPORTED_URL":
      return "不支持的 URL";
    case "TOKEN_REFRESH_FAILED":
      return "令牌刷新失败";
    case "TOKEN_BACKOFF":
      return "令牌退避中，请稍后再试";
    case "NO_TOKEN_AVAILABLE":
      return "没有可用令牌";
    case "PROXY_REQUIRED":
      return "必须启用代理";
    case "PROXY_AUTH_FAILED":
      return "代理认证失败";
    case "PROXY_CONNECT_FAILED":
      return "代理连接失败";
    default:
      return null;
  }
}

export function formatApiErrorMessage(status: number, body: ApiErrorBody | null): string {
  const bodyCode = body && typeof body.code === "string" ? String(body.code).trim() : "";
  const rawMessage = body && typeof body.message === "string" ? body.message.trim() : "";
  const translated = bodyCode ? translateErrorCode(bodyCode) : null;

  if (translated) {
    return rawMessage && containsChinese(rawMessage) ? rawMessage : translated;
  }
  return rawMessage || `HTTP ${status}`;
}

function getApiBaseUrl(): string {
  const raw = (import.meta.env.VITE_API_BASE_URL || "").trim();
  return raw.endsWith("/") ? raw.slice(0, -1) : raw;
}

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const base = getApiBaseUrl();
  const url = base ? base + path : path;

  const headers = new Headers(init?.headers || {});
  const token = getAdminToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const body = init?.body;
  const isFormData =
    typeof FormData !== "undefined" && body && body instanceof FormData;
  if (!headers.has("Content-Type") && body && !isFormData) {
    headers.set("Content-Type", "application/json");
  }

  return fetch(url, { ...init, headers });
}

export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await apiFetch(path, init);
  const text = await resp.text();
  const contentType = resp.headers.get("content-type") || "";
  const isJson = contentType.toLowerCase().includes("application/json");

  let data: unknown = null;
  if (text && isJson) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }

  if (resp.ok) {
    if (!isJson) throw new ApiError("Response is not JSON", { status: resp.status });
    return data as T;
  }

  const body = data && typeof data === "object" ? (data as ApiErrorBody) : null;

  if (
    resp.status === 401 &&
    path.startsWith("/admin/api/") &&
    !path.startsWith("/admin/api/login")
  ) {
    try {
      clearAdminToken();
    } catch {
      // ignore
    }
    try {
      window.dispatchEvent(
        new CustomEvent("admin:unauthorized", {
          detail: { path, request_id: body?.request_id ? String(body.request_id) : null },
        }),
      );
    } catch {
      // ignore
    }
  }

  const msg = formatApiErrorMessage(resp.status, body);
  throw new ApiError(msg, { status: resp.status, body });
}
