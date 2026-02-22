from __future__ import annotations

import asyncio

from app.pixiv.access_token_cache import AccessTokenCache
from app.pixiv.oauth import PixivOauthToken


def test_access_token_cache_reuses_token_until_expiry_margin() -> None:
    now_box = {"now": 1000.0}

    def now() -> float:
        return float(now_box["now"])

    cache = AccessTokenCache(now=now, refresh_margin_s=10.0)
    calls = {"n": 0}

    async def refresher() -> PixivOauthToken:
        calls["n"] += 1
        return PixivOauthToken(
            access_token=f"acc{calls['n']}",
            token_type="bearer",
            expires_in=100,
            refresh_token=None,
            scope=None,
            user_id=None,
        )

    async def _run() -> None:
        t1 = await cache.get_or_refresh("k1", refresher=refresher)
        t2 = await cache.get_or_refresh("k1", refresher=refresher)
        assert calls["n"] == 1
        assert t1.access_token == "acc1"
        assert t2.access_token == "acc1"

        now_box["now"] = 1089.0
        t3 = await cache.get_or_refresh("k1", refresher=refresher)
        assert calls["n"] == 1
        assert t3.access_token == "acc1"

        now_box["now"] = 1095.0
        t4 = await cache.get_or_refresh("k1", refresher=refresher)
        assert calls["n"] == 2
        assert t4.access_token == "acc2"

    asyncio.run(_run())

