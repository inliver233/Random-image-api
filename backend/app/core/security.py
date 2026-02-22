from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any

from app.core.errors import ApiError, ErrorCode

JWT_ALG_HS256 = "HS256"
JWT_TYP = "JWT"


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    data = data.strip()
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def create_jwt(
    *,
    secret_key: str,
    subject: str,
    ttl_s: int = 3600,
    extra_claims: Mapping[str, Any] | None = None,
    now_s: int | None = None,
) -> str:
    secret_key = secret_key.strip()
    if not secret_key:
        raise ValueError("SECRET_KEY is required")

    now_i = int(now_s if now_s is not None else time.time())

    header = {"alg": JWT_ALG_HS256, "typ": JWT_TYP}
    payload: dict[str, Any] = {"sub": subject, "iat": now_i, "exp": now_i + ttl_s}
    if extra_claims:
        payload.update(dict(extra_claims))

    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    sig_b64 = _b64url_encode(signature)
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def decode_jwt(token: str, *, secret_key: str, leeway_s: int = 0, now_s: int | None = None) -> dict[str, Any]:
    secret_key = secret_key.strip()
    if not secret_key:
        raise ValueError("SECRET_KEY is required")

    token = token.strip()
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid token format")

    header_raw = _b64url_decode(parts[0])
    payload_raw = _b64url_decode(parts[1])
    signature_raw = _b64url_decode(parts[2])

    try:
        header = json.loads(header_raw)
        payload = json.loads(payload_raw)
    except Exception as exc:
        raise ValueError("Invalid token JSON") from exc

    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise ValueError("Invalid token JSON")

    if header.get("alg") != JWT_ALG_HS256 or header.get("typ") != JWT_TYP:
        raise ValueError("Unsupported token")

    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    expected_sig = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(signature_raw, expected_sig):
        raise ValueError("Invalid token signature")

    now_i = int(now_s if now_s is not None else time.time())

    exp = payload.get("exp")
    if exp is not None:
        try:
            exp_i = int(exp)
        except Exception as exc:
            raise ValueError("Invalid exp") from exc
        if now_i > exp_i + leeway_s:
            raise ValueError("Token expired")

    iat = payload.get("iat")
    if iat is not None:
        try:
            iat_i = int(iat)
        except Exception as exc:
            raise ValueError("Invalid iat") from exc
        if iat_i > now_i + leeway_s:
            raise ValueError("Token issued in the future")

    return payload


def _authorization_from_headers(headers: Mapping[str, str] | None) -> str | None:
    if not headers:
        return None
    return headers.get("Authorization") or headers.get("authorization")


def parse_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.strip().split(None, 1)
    if len(parts) != 2:
        return None
    scheme, token = parts
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token if token else None


def require_admin(
    headers: Mapping[str, str] | None,
    *,
    secret_key: str,
    admin_username: str,
) -> dict[str, Any]:
    token = parse_bearer_token(_authorization_from_headers(headers))
    if not token:
        raise ApiError(code=ErrorCode.UNAUTHORIZED, message="Missing admin token", status_code=401)

    try:
        claims = decode_jwt(token, secret_key=secret_key)
    except Exception as exc:
        raise ApiError(code=ErrorCode.UNAUTHORIZED, message="Invalid admin token", status_code=401) from exc

    subject = str(claims.get("sub") or "")
    if subject != admin_username:
        raise ApiError(code=ErrorCode.FORBIDDEN, message="Forbidden", status_code=403)

    return claims

