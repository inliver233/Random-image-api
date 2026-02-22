from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Hashable

from app.pixiv.oauth import PixivOauthToken


DEFAULT_REFRESH_MARGIN_S = 60.0


@dataclass(frozen=True, slots=True)
class CachedAccessToken:
    access_token: str
    expires_at: float

    def is_valid(self, *, now: float, refresh_margin_s: float) -> bool:
        if not self.access_token:
            return False
        return now < (self.expires_at - refresh_margin_s)


class AccessTokenCache:
    def __init__(
        self,
        *,
        now: Callable[[], float] | None = None,
        refresh_margin_s: float = DEFAULT_REFRESH_MARGIN_S,
    ) -> None:
        self._now = now or time.time
        self._refresh_margin_s = float(refresh_margin_s)
        if self._refresh_margin_s < 0:
            self._refresh_margin_s = 0.0

        self._items: dict[Hashable, CachedAccessToken] = {}
        self._locks: dict[Hashable, asyncio.Lock] = {}

    def get(self, key: Hashable) -> str | None:
        item = self._items.get(key)
        if item is None:
            return None
        if not item.is_valid(now=float(self._now()), refresh_margin_s=self._refresh_margin_s):
            return None
        return item.access_token

    def set(self, key: Hashable, *, access_token: str, expires_in_s: float) -> None:
        access_token = (access_token or "").strip()
        expires_in_s = float(expires_in_s)
        if not access_token or expires_in_s <= 0:
            self._items.pop(key, None)
            return
        expires_at = float(self._now()) + expires_in_s
        self._items[key] = CachedAccessToken(access_token=access_token, expires_at=expires_at)

    def invalidate(self, key: Hashable) -> None:
        self._items.pop(key, None)

    async def get_or_refresh(
        self,
        key: Hashable,
        *,
        refresher: Callable[[], Awaitable[PixivOauthToken]],
    ) -> PixivOauthToken:
        cached = self.get(key)
        if cached is not None:
            return PixivOauthToken(
                access_token=cached,
                token_type="bearer",
                expires_in=int(self._items[key].expires_at - float(self._now())),
                refresh_token=None,
                scope=None,
                user_id=None,
            )

        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock

        async with lock:
            cached2 = self.get(key)
            if cached2 is not None:
                return PixivOauthToken(
                    access_token=cached2,
                    token_type="bearer",
                    expires_in=int(self._items[key].expires_at - float(self._now())),
                    refresh_token=None,
                    scope=None,
                    user_id=None,
                )

            token = await refresher()
            self.set(key, access_token=token.access_token, expires_in_s=float(token.expires_in))
            return token

