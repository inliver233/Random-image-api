from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    # 4.1 通用
    BAD_REQUEST = "BAD_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    # 4.2 Random
    NO_MATCH = "NO_MATCH"
    UPSTREAM_STREAM_ERROR = "UPSTREAM_STREAM_ERROR"
    UPSTREAM_403 = "UPSTREAM_403"
    UPSTREAM_404 = "UPSTREAM_404"
    UPSTREAM_RATE_LIMIT = "UPSTREAM_RATE_LIMIT"
    # 4.3 Import
    INVALID_UPLOAD_TYPE = "INVALID_UPLOAD_TYPE"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    UNSUPPORTED_URL = "UNSUPPORTED_URL"
    # 4.4 Pixiv/Token
    TOKEN_REFRESH_FAILED = "TOKEN_REFRESH_FAILED"
    TOKEN_BACKOFF = "TOKEN_BACKOFF"
    NO_TOKEN_AVAILABLE = "NO_TOKEN_AVAILABLE"
    # 4.5 Proxy
    PROXY_REQUIRED = "PROXY_REQUIRED"
    PROXY_AUTH_FAILED = "PROXY_AUTH_FAILED"
    PROXY_CONNECT_FAILED = "PROXY_CONNECT_FAILED"


UNKNOWN_REQUEST_ID = "req_unknown"

_DEFAULT_ZH_MESSAGE_BY_CODE: dict[ErrorCode, str] = {
    ErrorCode.BAD_REQUEST: "请求参数错误",
    ErrorCode.UNAUTHORIZED: "未授权",
    ErrorCode.FORBIDDEN: "禁止访问",
    ErrorCode.NOT_FOUND: "资源不存在",
    ErrorCode.RATE_LIMITED: "请求过于频繁",
    ErrorCode.INTERNAL_ERROR: "服务器内部错误",
    ErrorCode.NO_MATCH: "没有匹配的图片",
    ErrorCode.UPSTREAM_STREAM_ERROR: "上游请求失败",
    ErrorCode.UPSTREAM_403: "上游拒绝访问（403）",
    ErrorCode.UPSTREAM_404: "上游资源不存在（404）",
    ErrorCode.UPSTREAM_RATE_LIMIT: "上游触发限流（429）",
    ErrorCode.INVALID_UPLOAD_TYPE: "上传类型不支持",
    ErrorCode.PAYLOAD_TOO_LARGE: "请求体过大",
    ErrorCode.UNSUPPORTED_URL: "链接格式不支持",
    ErrorCode.TOKEN_REFRESH_FAILED: "令牌刷新失败",
    ErrorCode.TOKEN_BACKOFF: "令牌已进入退避",
    ErrorCode.NO_TOKEN_AVAILABLE: "没有可用令牌",
    ErrorCode.PROXY_REQUIRED: "需要代理",
    ErrorCode.PROXY_AUTH_FAILED: "代理认证失败",
    ErrorCode.PROXY_CONNECT_FAILED: "代理连接失败",
}


def _is_ascii_only(text: str) -> bool:
    return all(ord(ch) < 128 for ch in text)


_ASCII_MESSAGE_MAP: dict[str, str] = {
    "missing api key": "缺少 API Key",
    "invalid api key": "API Key 无效",
    "rate limited": "请求过于频繁",
    "database unavailable": "数据库不可用",
    "invalid json body": "JSON 请求体无效",
    "unsupported limit": "limit 参数不支持",
    "unsupported cursor": "cursor 参数不支持",
    "unsupported status": "status 参数不支持",
    "unsupported type": "type 参数不支持",
    "unsupported format": "format 参数不支持",
    "unsupported redirect": "redirect 参数不支持",
    "unsupported seed": "seed 参数不支持",
    "unsupported ai_type": "ai_type 参数不支持",
    "unsupported orientation": "orientation 参数不支持",
    "unsupported r18": "r18 参数不支持",
    "unsupported min_*": "min_* 参数不支持",
    "too many tag filters": "标签筛选条件过多",
    "pixiv oauth not configured": "未配置 Pixiv OAuth（client_id/client_secret）",
    "encryption not configured": "未配置加密密钥（FIELD_ENCRYPTION_KEY）",
    "invalid stored token": "令牌密文无效（无法解密）",
    "token not found": "令牌不存在",
    "job not found": "任务不存在",
    "proxy endpoint not found": "代理节点不存在",
    "proxy pool not found": "代理池不存在",
    "job is running": "任务正在运行中",
    "proxy authentication failed": "代理认证失败",
    "proxy connect failed": "代理连接失败",
    "upstream request failed": "上游请求失败",
    "upstream forbidden": "上游拒绝访问（403）",
    "upstream not found": "上游资源不存在（404）",
    "upstream rate limited": "上游触发限流（429）",
    "upstream error": "上游错误",
    "upstream request failed after attempts.": "多次尝试后上游请求仍失败",
    "invalid proxy endpoint": "代理节点配置无效",
    "unsupported conflict_policy": "conflict_policy 参数不支持",
}


def normalize_error_message(*, code: ErrorCode, message: str) -> str:
    msg = str(message or "").strip()
    if not msg:
        return _DEFAULT_ZH_MESSAGE_BY_CODE.get(code, "请求失败")

    if not _is_ascii_only(msg):
        return msg

    key = msg.lower()
    if key in _ASCII_MESSAGE_MAP:
        return _ASCII_MESSAGE_MAP[key]

    if key.startswith("unsupported "):
        rest = msg[len("Unsupported ") :].strip()
        return f"参数不支持：{rest}" if rest else _DEFAULT_ZH_MESSAGE_BY_CODE.get(code, "请求失败")

    if key.startswith("invalid "):
        rest = msg[len("Invalid ") :].strip()
        return f"参数无效：{rest}" if rest else _DEFAULT_ZH_MESSAGE_BY_CODE.get(code, "请求失败")

    if key.startswith("missing "):
        rest = msg[len("Missing ") :].strip()
        return f"缺少字段：{rest}" if rest else _DEFAULT_ZH_MESSAGE_BY_CODE.get(code, "请求失败")

    return _DEFAULT_ZH_MESSAGE_BY_CODE.get(code, "请求失败")


@dataclass(frozen=True, slots=True)
class ApiError(Exception):
    code: ErrorCode
    message: str
    status_code: int = 400
    details: dict[str, Any] | None = None


def _coerce_request_id(request_id: str | None) -> str:
    request_id = (request_id or "").strip()
    return request_id if request_id else UNKNOWN_REQUEST_ID


def _request_id_from_request(request: Any | None) -> str | None:
    if request is None:
        return None
    request_id = getattr(getattr(request, "state", None), "request_id", None)
    if request_id:
        return str(request_id)
    header = request.headers.get("X-Request-Id")
    return header.strip() if header else None


def error_body(
    *,
    code: ErrorCode,
    message: str,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "code": code.value,
        "message": normalize_error_message(code=code, message=message),
        "request_id": _coerce_request_id(request_id),
        "details": details or {},
    }


def json_error_response(
    *,
    code: ErrorCode,
    message: str,
    status_code: int,
    request: Any | None = None,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> Any:
    if request_id is None:
        request_id = _request_id_from_request(request)
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=status_code,
        content=error_body(code=code, message=message, request_id=request_id, details=details),
    )
