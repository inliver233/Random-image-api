from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.proxy_routing import ProxyUri
from app.core.proxy_selector import (
    EgressAttempt,
    egress_attempt_count,
    iter_pixiv_api_egress,
)


def test_egress_attempt_count_formula() -> None:
    assert egress_attempt_count(cf_candidate_count=0, residential_failover_attempts=0) == 1
    assert egress_attempt_count(cf_candidate_count=2, residential_failover_attempts=0) == 3
    assert egress_attempt_count(cf_candidate_count=2, residential_failover_attempts=2) == 5


def test_iter_pixiv_api_egress_cf_first_then_residential() -> None:
    settings = SimpleNamespace()
    runtime = SimpleNamespace()
    engine = object()
    residential = ProxyUri(uri="http://u:p@10.0.0.1:8080", endpoint_id=3, pool_id=1)

    async def _run() -> list[EgressAttempt]:
        with (
            patch(
                "app.core.proxy_selector.resolve_pixiv_api_cf_candidates",
                return_value=[
                    ("https://cf-a.example/p/app-api.pixiv.net/v1", {"X-Proxy-Secret": "s"}),
                    ("https://cf-b.example/p/app-api.pixiv.net/v1", {"X-Proxy-Secret": "s"}),
                ],
            ),
            patch(
                "app.core.proxy_selector.select_proxy_uri_for_url",
                new_callable=AsyncMock,
                return_value=residential,
            ) as select,
        ):
            out = [
                a
                async for a in iter_pixiv_api_egress(
                    engine,  # type: ignore[arg-type]
                    settings,  # type: ignore[arg-type]
                    runtime,  # type: ignore[arg-type]
                    url="https://app-api.pixiv.net/v1/illust/detail",
                    token_id=9,
                    residential_failover_attempts=1,
                )
            ]
            assert select.await_count == 2  # failover_attempts+1 residential tries
            return out

    attempts = asyncio.run(_run())
    assert len(attempts) == 4
    assert attempts[0].via_cf is True
    assert attempts[0].proxy_uri is None
    assert attempts[0].request_url.startswith("https://cf-a.example/")
    assert attempts[0].extra_headers.get("X-Proxy-Secret") == "s"
    assert attempts[1].via_cf is True
    assert attempts[1].request_url.startswith("https://cf-b.example/")
    assert attempts[2].via_cf is False
    assert attempts[2].residential is residential
    assert attempts[2].proxy_uri == residential.uri
    assert attempts[2].request_url == "https://app-api.pixiv.net/v1/illust/detail"
    assert attempts[3].via_cf is False


def test_iter_pixiv_api_egress_residential_only_when_cf_empty() -> None:
    async def _run() -> list[EgressAttempt]:
        with (
            patch("app.core.proxy_selector.resolve_pixiv_api_cf_candidates", return_value=[]),
            patch(
                "app.core.proxy_selector.select_proxy_uri_for_url",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            return [
                a
                async for a in iter_pixiv_api_egress(
                    object(),  # type: ignore[arg-type]
                    SimpleNamespace(),  # type: ignore[arg-type]
                    SimpleNamespace(),  # type: ignore[arg-type]
                    url="https://oauth.secure.pixiv.net/auth/token",
                    residential_failover_attempts=0,
                )
            ]

    attempts = asyncio.run(_run())
    assert len(attempts) == 1
    assert attempts[0].via_cf is False
    assert attempts[0].residential is None
    assert attempts[0].proxy_uri is None
