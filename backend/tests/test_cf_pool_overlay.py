from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

from app.core.cf_pool_overlay import (
    apply_runtime_values_to_overlay,
    ensure_overlay_fresh,
    get_api_overlay_bases,
    get_image_overlay_bases,
    merge_api_bases_with_overlay,
    overlay_age_s,
    reset_overlay_for_tests,
    should_refresh_overlay,
)
from app.core.cf_pool_registry import (
    RUNTIME_KEY_API_BASES,
    RUNTIME_KEY_API_VERIFIED_BASES,
    RUNTIME_KEY_IMAGE_BASES,
    RUNTIME_KEY_IMAGE_VERIFIED_BASES,
)


def test_apply_runtime_values_and_merge() -> None:
    reset_overlay_for_tests()
    apply_runtime_values_to_overlay(
        {
            RUNTIME_KEY_API_BASES: ["https://api-a.example.workers.dev/"],
            RUNTIME_KEY_API_VERIFIED_BASES: ["https://api-a.example.workers.dev/"],
            RUNTIME_KEY_IMAGE_BASES: ["https://img-a.example.workers.dev"],
            RUNTIME_KEY_IMAGE_VERIFIED_BASES: ["https://img-a.example.workers.dev"],
        }
    )
    assert get_api_overlay_bases() == ["https://api-a.example.workers.dev"]
    assert get_image_overlay_bases() == ["https://img-a.example.workers.dev"]
    merged = merge_api_bases_with_overlay(["https://env.example.workers.dev"])
    assert merged[0] == "https://env.example.workers.dev"
    assert "https://api-a.example.workers.dev" in merged
    assert overlay_age_s() is not None
    assert should_refresh_overlay(ttl_s=999.0) is False
    # Floor is 0.2s: age≈0 still fresh even if caller passes ttl_s=0
    assert should_refresh_overlay(ttl_s=0.0) is False
    # Stale when now is far past load time
    assert should_refresh_overlay(ttl_s=5.0, now=time.monotonic() + 10.0) is True
    reset_overlay_for_tests()
    assert should_refresh_overlay() is True


def test_ensure_overlay_fresh_skips_when_fresh() -> None:
    reset_overlay_for_tests()
    apply_runtime_values_to_overlay(
        {
            RUNTIME_KEY_API_BASES: ["https://a.example.workers.dev"],
            RUNTIME_KEY_API_VERIFIED_BASES: ["https://a.example.workers.dev"],
        }
    )

    async def _run() -> bool:
        with patch(
            "app.core.cf_pool_overlay.reload_overlay_from_engine",
            new_callable=AsyncMock,
        ) as reload:
            # Fresh → skip
            skipped = await ensure_overlay_fresh(object(), ttl_s=60.0)
            assert skipped is False
            reload.assert_not_awaited()
            # Force → reload
            forced = await ensure_overlay_fresh(object(), ttl_s=60.0, force=True)
            assert forced is True
            reload.assert_awaited_once()
            return True

    assert asyncio.run(_run()) is True
    reset_overlay_for_tests()


def test_apply_runtime_values_drops_unverified_legacy_bases() -> None:
    reset_overlay_for_tests()
    apply_runtime_values_to_overlay(
        {
            RUNTIME_KEY_API_BASES: ["https://attacker.example.workers.dev"],
            RUNTIME_KEY_IMAGE_BASES: ["https://old-custom.example.com"],
        }
    )
    assert get_api_overlay_bases() == []
    assert get_image_overlay_bases() == []
    reset_overlay_for_tests()


def test_ensure_overlay_fresh_reloads_when_stale() -> None:
    reset_overlay_for_tests()

    async def _run() -> None:
        with patch(
            "app.core.cf_pool_overlay.reload_overlay_from_engine",
            new_callable=AsyncMock,
        ) as reload:
            # Never loaded → refresh
            ok = await ensure_overlay_fresh(object(), ttl_s=5.0)
            assert ok is True
            reload.assert_awaited_once()

    asyncio.run(_run())
    reset_overlay_for_tests()
