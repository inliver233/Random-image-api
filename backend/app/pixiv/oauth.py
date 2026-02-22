from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

import httpx


DEFAULT_OAUTH_BASE_URL = "https://oauth.secure.pixiv.net"
OAUTH_TOKEN_PATH = "/auth/token"


class PixivOauthError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class PixivOauthConfig:
    client_id: str
    client_secret: str
    hash_secret: str | None = None

    base_url: str = DEFAULT_OAUTH_BASE_URL
    user_agent: str = "PixivAndroidApp/5.0.234 (Android 11; Pixel 5)"
    accept_language: str = "en_US"
    app_os: str = "android"
    app_os_version: str = "11"
    app_version: str = "5.0.234"

    def build_headers(self, *, client_time: str) -> dict[str, str]:
        headers: dict[str, str] = {
            "User-Agent": self.user_agent,
            "Accept-Language": self.accept_language,
            "App-OS": self.app_os,
            "App-OS-Version": self.app_os_version,
            "App-Version": self.app_version,
        }
        if self.hash_secret:
            headers["X-Client-Time"] = client_time
            headers["X-Client-Hash"] = hashlib.md5((client_time + self.hash_secret).encode("utf-8")).hexdigest()
        return headers


@dataclass(frozen=True, slots=True)
class PixivOauthToken:
    access_token: str
    token_type: str
    expires_in: int
    refresh_token: str | None
    scope: str | None
    user_id: str | None


def _now_client_time() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _unwrap_response(data: Any) -> Mapping[str, Any]:
    if isinstance(data, dict) and isinstance(data.get("response"), dict):
        return data["response"]
    if isinstance(data, dict):
        return data
    raise PixivOauthError("Invalid OAuth response shape")


def _parse_token_response(data: Any) -> PixivOauthToken:
    payload = _unwrap_response(data)

    access_token = payload.get("access_token")
    token_type = payload.get("token_type")
    expires_in = payload.get("expires_in")
    refresh_token = payload.get("refresh_token")
    scope = payload.get("scope")
    user_id = None

    user = payload.get("user")
    if isinstance(user, dict) and user.get("id") is not None:
        user_id = str(user.get("id"))

    if not isinstance(access_token, str) or not access_token:
        raise PixivOauthError("OAuth response missing access_token")
    if not isinstance(token_type, str) or not token_type:
        raise PixivOauthError("OAuth response missing token_type")
    if isinstance(expires_in, str) and expires_in.isdigit():
        expires_in = int(expires_in)
    if not isinstance(expires_in, int) or expires_in <= 0:
        raise PixivOauthError("OAuth response missing expires_in")

    return PixivOauthToken(
        access_token=access_token,
        token_type=token_type,
        expires_in=expires_in,
        refresh_token=refresh_token if isinstance(refresh_token, str) and refresh_token else None,
        scope=scope if isinstance(scope, str) and scope else None,
        user_id=user_id,
    )


async def refresh_access_token(
    *,
    refresh_token: str,
    config: PixivOauthConfig,
    transport: httpx.BaseTransport | None = None,
    proxy: str | None = None,
    timeout_s: float = 30.0,
    client_time: str | None = None,
) -> PixivOauthToken:
    refresh_token = (refresh_token or "").strip()
    if not refresh_token:
        raise ValueError("refresh_token is required")

    if not config.client_id.strip() or not config.client_secret.strip():
        raise ValueError("PixivOauthConfig.client_id/client_secret are required")

    client_time = client_time or _now_client_time()
    headers = config.build_headers(client_time=client_time)

    payload = {
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "get_secure_url": "1",
        "include_policy": "1",
    }

    url = config.base_url.rstrip("/") + OAUTH_TOKEN_PATH

    async with httpx.AsyncClient(
        transport=transport,
        proxy=proxy,
        headers=headers,
        timeout=httpx.Timeout(timeout_s, connect=10.0),
        follow_redirects=True,
    ) as client:
        resp = await client.post(url, data=payload)

    if resp.status_code != 200:
        raise PixivOauthError("OAuth refresh failed", status_code=resp.status_code)

    try:
        data = resp.json()
    except Exception as exc:
        raise PixivOauthError("OAuth response is not JSON", status_code=resp.status_code) from exc

    return _parse_token_response(data)

