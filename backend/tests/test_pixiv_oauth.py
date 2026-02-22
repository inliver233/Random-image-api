from __future__ import annotations

import asyncio
import hashlib
from urllib.parse import parse_qs

import httpx
import pytest

from app.pixiv.oauth import PixivOauthConfig, PixivOauthError, refresh_access_token


def test_refresh_access_token_success_builds_expected_request() -> None:
    client_time = "2026-02-10T00:00:00+00:00"
    hash_secret = "hash_secret_test"
    refresh_token = "dummy_refresh_token"

    config = PixivOauthConfig(
        client_id="client_id_test",
        client_secret="client_secret_test",
        hash_secret=hash_secret,
    )

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        assert str(req.url) == "https://oauth.secure.pixiv.net/auth/token"

        assert req.headers.get("User-Agent") == config.user_agent
        assert req.headers.get("X-Client-Time") == client_time

        expected_hash = hashlib.md5((client_time + hash_secret).encode("utf-8")).hexdigest()
        assert req.headers.get("X-Client-Hash") == expected_hash

        body = (req.content or b"").decode("utf-8")
        form = parse_qs(body)
        assert form["grant_type"] == ["refresh_token"]
        assert form["refresh_token"] == [refresh_token]
        assert form["client_id"] == [config.client_id]
        assert form["client_secret"] == [config.client_secret]
        assert form["get_secure_url"] == ["1"]
        assert form["include_policy"] == ["1"]

        return httpx.Response(
            200,
            json={
                "response": {
                    "access_token": "access_token_test",
                    "token_type": "bearer",
                    "expires_in": 3600,
                    "refresh_token": "refresh_token_rotated",
                    "scope": "",
                    "user": {"id": 123},
                }
            },
        )

    token = asyncio.run(
        refresh_access_token(
            refresh_token=refresh_token,
            config=config,
            transport=httpx.MockTransport(handler),
            client_time=client_time,
        )
    )

    assert token.access_token == "access_token_test"
    assert token.token_type == "bearer"
    assert token.expires_in == 3600
    assert token.refresh_token == "refresh_token_rotated"
    assert token.scope is None
    assert token.user_id == "123"


def test_refresh_access_token_error_does_not_echo_refresh_token() -> None:
    refresh_token = "dummy_refresh_token"

    config = PixivOauthConfig(
        client_id="client_id_test",
        client_secret="client_secret_test",
        hash_secret="hash_secret_test",
    )

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    with pytest.raises(PixivOauthError) as excinfo:
        asyncio.run(
            refresh_access_token(
                refresh_token=refresh_token,
                config=config,
                transport=httpx.MockTransport(handler),
                client_time="2026-02-10T00:00:00+00:00",
            )
        )

    assert excinfo.value.status_code == 400
    assert refresh_token not in str(excinfo.value)

