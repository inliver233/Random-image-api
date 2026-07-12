from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.origin_stream import prepare_origin_stream


def test_prepare_origin_stream_mirror_skips_proxy() -> None:
    async def _run() -> None:
        with patch("app.core.origin_stream.select_proxy_uri_for_url", new_callable=AsyncMock) as select:
            url, proxy = await prepare_origin_stream(
                engine=object(),
                settings=SimpleNamespace(),
                runtime=object(),
                origin_url="https://i.pximg.net/img-original/img/a.jpg",
                use_mirror=True,
                mirror_host="mirror.example",
            )
            assert "mirror.example" in url
            assert proxy is None
            select.assert_not_called()

    asyncio.run(_run())


def test_prepare_origin_stream_skips_residential_when_edge_ready() -> None:
    async def _run() -> None:
        settings = SimpleNamespace(
            residential_egress_emergency_only=True,
            image_edge_enabled=True,
            image_edge_secret="s" * 16,
            image_edge_base_urls=["https://img.example"],
            image_edge_sign_ttl_seconds=3600,
            image_edge_secret_previous="",
        )
        with patch("app.core.origin_stream.select_proxy_uri_for_url", new_callable=AsyncMock) as select:
            url, proxy = await prepare_origin_stream(
                engine=object(),
                settings=settings,
                runtime=object(),
                origin_url="https://i.pximg.net/img-original/img/a.jpg",
                use_mirror=False,
                mirror_host=None,
            )
            assert url == "https://i.pximg.net/img-original/img/a.jpg"
            assert proxy is None
            select.assert_not_called()

    asyncio.run(_run())


def test_prepare_origin_stream_uses_residential_when_edge_not_ready() -> None:
    async def _run() -> None:
        settings = SimpleNamespace(
            residential_egress_emergency_only=True,
            image_edge_enabled=False,
            image_edge_secret="",
            image_edge_base_urls=[],
            image_edge_sign_ttl_seconds=3600,
            image_edge_secret_previous="",
        )
        picked = SimpleNamespace(uri="http://user:pass@proxy.example:8080")
        with patch(
            "app.core.origin_stream.select_proxy_uri_for_url",
            new_callable=AsyncMock,
            return_value=picked,
        ) as select:
            url, proxy = await prepare_origin_stream(
                engine=object(),
                settings=settings,
                runtime=object(),
                origin_url="https://i.pximg.net/img-original/img/a.jpg",
                use_mirror=False,
                mirror_host=None,
            )
            assert url.startswith("https://i.pximg.net/")
            assert proxy == picked.uri
            select.assert_awaited_once()

    asyncio.run(_run())


def test_prepare_origin_stream_allow_residential_false_forces_direct() -> None:
    async def _run() -> None:
        with patch("app.core.origin_stream.select_proxy_uri_for_url", new_callable=AsyncMock) as select:
            with patch(
                "app.core.egress_policy.image_edge_is_ready",
                return_value=False,
            ):
                url, proxy = await prepare_origin_stream(
                    engine=object(),
                    settings=SimpleNamespace(residential_egress_emergency_only=True),
                    runtime=object(),
                    origin_url="https://i.pximg.net/img-original/img/a.jpg",
                    use_mirror=False,
                    mirror_host=None,
                    allow_residential_proxy=False,
                )
                assert proxy is None
                select.assert_not_called()
                assert url.startswith("https://i.pximg.net/")

    asyncio.run(_run())


def test_prepare_origin_stream_allow_residential_true_even_if_edge_ready() -> None:
    async def _run() -> None:
        picked = SimpleNamespace(uri="http://proxy.example:1")
        with patch(
            "app.core.egress_policy.image_edge_is_ready",
            return_value=True,
        ):
            with patch(
                "app.core.origin_stream.select_proxy_uri_for_url",
                new_callable=AsyncMock,
                return_value=picked,
            ) as select:
                _url, proxy = await prepare_origin_stream(
                    engine=object(),
                    settings=SimpleNamespace(residential_egress_emergency_only=True),
                    runtime=object(),
                    origin_url="https://i.pximg.net/img-original/img/a.jpg",
                    use_mirror=False,
                    mirror_host=None,
                    allow_residential_proxy=True,
                )
                assert proxy == picked.uri
                select.assert_awaited_once()

    asyncio.run(_run())


def test_prepare_origin_stream_force_residential_emergency_when_edge_ready() -> None:
    async def _run() -> None:
        picked = SimpleNamespace(uri="http://proxy.example:9")
        with patch(
            "app.core.egress_policy.image_edge_is_ready",
            return_value=True,
        ):
            with patch(
                "app.core.origin_stream.select_proxy_uri_for_url",
                new_callable=AsyncMock,
                return_value=picked,
            ) as select:
                _url, proxy = await prepare_origin_stream(
                    engine=object(),
                    settings=SimpleNamespace(residential_egress_emergency_only=True),
                    runtime=object(),
                    origin_url="https://i.pximg.net/img-original/img/a.jpg",
                    use_mirror=False,
                    mirror_host=None,
                    force_residential_emergency=True,
                )
                assert proxy == picked.uri
                select.assert_awaited_once()

    asyncio.run(_run())
