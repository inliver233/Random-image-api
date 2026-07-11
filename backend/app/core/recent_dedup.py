from __future__ import annotations

import logging
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Protocol, runtime_checkable


log = logging.getLogger(__name__)

# Best-effort global de-dup (process-local): reduce short-term duplicates without extra DB writes.
_RECENT_LOCK = Lock()
_RECENT_IMAGES: deque[tuple[float, int]] = deque()
_RECENT_AUTHORS: deque[tuple[float, int]] = deque()

# Shared pool for RedisRecentDedup so request threads never block on Redis RTT.
_REDIS_IO_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="recent-dedup-redis")
# Hard ceiling for any Redis call on the pick path (fail open past this).
_REDIS_CALL_TIMEOUT_S = 0.15
# Reuse last successful cross-instance window briefly to avoid sync Redis on every pick.
_REDIS_LIST_CACHE_TTL_S = 0.5


@runtime_checkable
class RecentDedupPort(Protocol):
    """Short-window anti-repeat store (memory default; optional Redis for multi-instance).

    Semantics: best-effort only. Fail-open is intentional — dedup must never block /random.
    Cross-instance Redis must fail open (empty windows / no-op record), not raise into the
    request path. Call sites are synchronous (pick plan + background side-effects).
    """

    backend: str

    def prune(self, now: float, *, window_s: float, max_images: int, max_authors: int) -> None: ...

    def get_lists(
        self, now: float, *, window_s: float, max_images: int, max_authors: int
    ) -> tuple[list[int], list[int]]: ...

    def get_sets(
        self, now: float, *, window_s: float, max_images: int, max_authors: int
    ) -> tuple[set[int], set[int]]: ...

    def record(
        self,
        *,
        now: float,
        image_id: int,
        user_id: int | None,
        window_s: float,
        max_images: int,
        max_authors: int,
    ) -> None: ...

    def clear(self) -> None: ...


class MemoryRecentDedup:
    """Default: process-local deques (shared module state so module helpers stay coherent)."""

    backend: str = "memory"

    def prune(self, now: float, *, window_s: float, max_images: int, max_authors: int) -> None:
        prune_recent(now, window_s=float(window_s), max_images=int(max_images), max_authors=int(max_authors))

    def get_lists(
        self, now: float, *, window_s: float, max_images: int, max_authors: int
    ) -> tuple[list[int], list[int]]:
        return get_recent_lists(
            now, window_s=float(window_s), max_images=int(max_images), max_authors=int(max_authors)
        )

    def get_sets(
        self, now: float, *, window_s: float, max_images: int, max_authors: int
    ) -> tuple[set[int], set[int]]:
        return get_recent_sets(
            now, window_s=float(window_s), max_images=int(max_images), max_authors=int(max_authors)
        )

    def record(
        self,
        *,
        now: float,
        image_id: int,
        user_id: int | None,
        window_s: float,
        max_images: int,
        max_authors: int,
    ) -> None:
        record_recent(
            now=float(now),
            image_id=int(image_id),
            user_id=user_id,
            window_s=float(window_s),
            max_images=int(max_images),
            max_authors=int(max_authors),
        )

    def clear(self) -> None:
        clear_recent()


def prune_recent(now: float, *, window_s: float, max_images: int, max_authors: int) -> None:
    cutoff = float(now) - float(window_s)
    while _RECENT_IMAGES and float(_RECENT_IMAGES[0][0]) < cutoff:
        _RECENT_IMAGES.popleft()
    while _RECENT_AUTHORS and float(_RECENT_AUTHORS[0][0]) < cutoff:
        _RECENT_AUTHORS.popleft()

    while len(_RECENT_IMAGES) > int(max_images):
        _RECENT_IMAGES.popleft()
    while len(_RECENT_AUTHORS) > int(max_authors):
        _RECENT_AUTHORS.popleft()


def get_recent_sets(now: float, *, window_s: float, max_images: int, max_authors: int) -> tuple[set[int], set[int]]:
    image_ids, author_ids = get_recent_lists(
        now, window_s=float(window_s), max_images=int(max_images), max_authors=int(max_authors)
    )
    return set(image_ids), set(author_ids)


def get_recent_lists(now: float, *, window_s: float, max_images: int, max_authors: int) -> tuple[list[int], list[int]]:
    """Return recent ids oldest→newest (process-local)."""
    with _RECENT_LOCK:
        prune_recent(now, window_s=float(window_s), max_images=int(max_images), max_authors=int(max_authors))
        recent_images = [int(image_id) for _t, image_id in _RECENT_IMAGES]
        recent_authors = [int(user_id) for _t, user_id in _RECENT_AUTHORS]
    return recent_images, recent_authors


def clear_recent() -> None:
    """Drop process-local recent windows (tests / admin maintenance)."""
    with _RECENT_LOCK:
        _RECENT_IMAGES.clear()
        _RECENT_AUTHORS.clear()


def record_recent(
    *,
    now: float,
    image_id: int,
    user_id: int | None,
    window_s: float,
    max_images: int,
    max_authors: int,
) -> None:
    try:
        image_id_i = int(image_id)
    except Exception:
        return
    if image_id_i <= 0:
        return
    user_id_i: int | None
    try:
        user_id_i = int(user_id) if user_id is not None else None
    except Exception:
        user_id_i = None

    with _RECENT_LOCK:
        prune_recent(now, window_s=float(window_s), max_images=int(max_images), max_authors=int(max_authors))
        _RECENT_IMAGES.append((float(now), int(image_id_i)))
        if user_id_i is not None and user_id_i > 0:
            _RECENT_AUTHORS.append((float(now), int(user_id_i)))


@dataclass
class RedisRecentDedup:
    """Cross-instance short-window anti-repeat via Redis sorted sets (optional).

    Keys:
      - ``{prefix}images``  member=image_id, score=unix wall time
      - ``{prefix}authors`` member=user_id,  score=unix wall time

    Call sites pass process ``time.monotonic()`` for memory; Redis always scores with
    wall-clock so multi-instance windows align. Fail-open to process-local memory when
    redis package/connect/command fails (public API stays up).

    Request-path contract (latency):
      - ``record`` always dual-writes process-local memory, then fire-and-forgets Redis.
      - ``get_lists`` serves a short TTL cache or memory immediately; Redis refresh is
        bounded by a thread-pool timeout so slow Redis never stalls /random plan build.
    """

    redis_url: str
    key_prefix: str = "np:recent:"
    backend: str = "redis"
    _client: Any = field(default=None, repr=False)
    _fallback: MemoryRecentDedup = field(default_factory=MemoryRecentDedup, repr=False)
    _connect_failed: bool = field(default=False, repr=False)
    _cache_lock: Lock = field(default_factory=Lock, repr=False)
    _list_cache: tuple[list[int], list[int]] | None = field(default=None, repr=False)
    _list_cache_at: float = field(default=0.0, repr=False)
    _refresh_inflight: bool = field(default=False, repr=False)

    def _images_key(self) -> str:
        return f"{self.key_prefix}images"

    def _authors_key(self) -> str:
        return f"{self.key_prefix}authors"

    def _get_client(self) -> Any | None:
        if self._client is not None:
            return self._client
        if self._connect_failed:
            return None
        try:
            import redis as redis_sync  # type: ignore[import-not-found]
        except Exception as exc:
            log.warning("recent_dedup_redis_import_failed err=%s", type(exc).__name__)
            self._connect_failed = True
            return None
        try:
            client = redis_sync.from_url(
                str(self.redis_url),
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=0.2,
                socket_timeout=0.2,
            )
            client.ping()
            self._client = client
            return client
        except Exception as exc:
            log.warning("recent_dedup_redis_connect_failed err=%s", type(exc).__name__)
            self._connect_failed = True
            return None

    def _prune_key(self, client: Any, *, key: str, now_s: float, window_s: float, max_n: int) -> None:
        cutoff = float(now_s) - max(0.0, float(window_s))
        try:
            client.zremrangebyscore(key, "-inf", cutoff)
            if int(max_n) > 0:
                # Drop oldest (lowest score) beyond hard cap.
                n = int(client.zcard(key) or 0)
                overflow = n - int(max_n)
                if overflow > 0:
                    client.zremrangebyrank(key, 0, overflow - 1)
        except Exception as exc:
            log.warning("recent_dedup_redis_prune_failed err=%s", type(exc).__name__)

    def _fetch_lists_sync(
        self, *, window_s: float, max_images: int, max_authors: int
    ) -> tuple[list[int], list[int]] | None:
        client = self._get_client()
        if client is None:
            return None
        now_s = time.time()
        try:
            self._prune_key(
                client, key=self._images_key(), now_s=now_s, window_s=float(window_s), max_n=int(max_images)
            )
            self._prune_key(
                client, key=self._authors_key(), now_s=now_s, window_s=float(window_s), max_n=int(max_authors)
            )
            raw_images = client.zrange(self._images_key(), 0, -1) or []
            raw_authors = client.zrange(self._authors_key(), 0, -1) or []
            images: list[int] = []
            for x in raw_images:
                try:
                    images.append(int(x))
                except Exception:
                    continue
            authors: list[int] = []
            for x in raw_authors:
                try:
                    authors.append(int(x))
                except Exception:
                    continue
            return images, authors
        except Exception as exc:
            log.warning("recent_dedup_redis_get_failed err=%s", type(exc).__name__)
            return None

    def _store_list_cache(self, images: list[int], authors: list[int]) -> None:
        with self._cache_lock:
            self._list_cache = (list(images), list(authors))
            self._list_cache_at = time.monotonic()

    def _read_list_cache(self) -> tuple[list[int], list[int]] | None:
        with self._cache_lock:
            if self._list_cache is None:
                return None
            if (time.monotonic() - float(self._list_cache_at)) > float(_REDIS_LIST_CACHE_TTL_S):
                return None
            images, authors = self._list_cache
            return list(images), list(authors)

    def _schedule_list_refresh(self, *, window_s: float, max_images: int, max_authors: int) -> None:
        with self._cache_lock:
            if self._refresh_inflight:
                return
            self._refresh_inflight = True

        def _job() -> None:
            try:
                fetched = self._fetch_lists_sync(
                    window_s=float(window_s), max_images=int(max_images), max_authors=int(max_authors)
                )
                if fetched is not None:
                    self._store_list_cache(fetched[0], fetched[1])
            finally:
                with self._cache_lock:
                    self._refresh_inflight = False

        try:
            _REDIS_IO_EXECUTOR.submit(_job)
        except Exception:
            with self._cache_lock:
                self._refresh_inflight = False

    def _record_redis_sync(
        self,
        *,
        image_id_i: int,
        user_id_i: int | None,
        window_s: float,
        max_images: int,
        max_authors: int,
    ) -> None:
        client = self._get_client()
        if client is None:
            return
        now_s = time.time()
        try:
            pipe = client.pipeline(transaction=False)
            pipe.zadd(self._images_key(), {str(image_id_i): float(now_s)})
            if user_id_i is not None and user_id_i > 0:
                pipe.zadd(self._authors_key(), {str(user_id_i): float(now_s)})
            pipe.execute()
            self._prune_key(
                client, key=self._images_key(), now_s=now_s, window_s=float(window_s), max_n=int(max_images)
            )
            self._prune_key(
                client, key=self._authors_key(), now_s=now_s, window_s=float(window_s), max_n=int(max_authors)
            )
            # Keep local cache coherent for same-process follow-up picks.
            with self._cache_lock:
                images, authors = (
                    (list(self._list_cache[0]), list(self._list_cache[1]))
                    if self._list_cache is not None
                    else ([], [])
                )
            if image_id_i not in images:
                images.append(int(image_id_i))
            if user_id_i is not None and user_id_i > 0 and user_id_i not in authors:
                authors.append(int(user_id_i))
            self._store_list_cache(images, authors)
        except Exception as exc:
            log.warning("recent_dedup_redis_record_failed err=%s", type(exc).__name__)

    def prune(self, now: float, *, window_s: float, max_images: int, max_authors: int) -> None:
        # Local always; Redis prune rides on record/get refresh workers.
        self._fallback.prune(
            now, window_s=float(window_s), max_images=int(max_images), max_authors=int(max_authors)
        )
        self._schedule_list_refresh(
            window_s=float(window_s), max_images=int(max_images), max_authors=int(max_authors)
        )

    def get_lists(
        self, now: float, *, window_s: float, max_images: int, max_authors: int
    ) -> tuple[list[int], list[int]]:
        """Never block the event loop / request thread on Redis RTT.

        Warm process cache → return immediately (background refresh).
        Cold/expired cache → schedule Redis fetch and fail-open to local memory
        window so public /random plan build stays non-blocking.
        """
        cached = self._read_list_cache()
        # Always schedule a background refresh (no-op if already in-flight).
        self._schedule_list_refresh(
            window_s=float(window_s), max_images=int(max_images), max_authors=int(max_authors)
        )
        if cached is not None:
            return cached

        # Cold path: do not fut.result() — that can stall asyncio up to timeout.
        return self._fallback.get_lists(
            now, window_s=float(window_s), max_images=int(max_images), max_authors=int(max_authors)
        )

    def get_sets(
        self, now: float, *, window_s: float, max_images: int, max_authors: int
    ) -> tuple[set[int], set[int]]:
        images, authors = self.get_lists(
            now, window_s=float(window_s), max_images=int(max_images), max_authors=int(max_authors)
        )
        return set(images), set(authors)

    def record(
        self,
        *,
        now: float,
        image_id: int,
        user_id: int | None,
        window_s: float,
        max_images: int,
        max_authors: int,
    ) -> None:
        try:
            image_id_i = int(image_id)
        except Exception:
            return
        if image_id_i <= 0:
            return
        user_id_i: int | None
        try:
            user_id_i = int(user_id) if user_id is not None else None
        except Exception:
            user_id_i = None

        # Always dual-write local so same-process anti-repeat stays correct even if Redis lags.
        self._fallback.record(
            now=float(now),
            image_id=int(image_id_i),
            user_id=user_id_i,
            window_s=float(window_s),
            max_images=int(max_images),
            max_authors=int(max_authors),
        )
        with self._cache_lock:
            images, authors = (
                (list(self._list_cache[0]), list(self._list_cache[1]))
                if self._list_cache is not None
                else ([], [])
            )
            if image_id_i not in images:
                images.append(int(image_id_i))
            if user_id_i is not None and user_id_i > 0 and user_id_i not in authors:
                authors.append(int(user_id_i))
            self._list_cache = (images, authors)
            self._list_cache_at = time.monotonic()

        try:
            _REDIS_IO_EXECUTOR.submit(
                self._record_redis_sync,
                image_id_i=int(image_id_i),
                user_id_i=user_id_i,
                window_s=float(window_s),
                max_images=int(max_images),
                max_authors=int(max_authors),
            )
        except Exception as exc:
            log.warning("recent_dedup_redis_record_submit_failed err=%s", type(exc).__name__)

    def clear(self) -> None:
        with self._cache_lock:
            self._list_cache = None
            self._list_cache_at = 0.0
        self._fallback.clear()
        client = self._get_client()
        if client is None:
            return
        try:
            client.delete(self._images_key(), self._authors_key())
        except Exception as exc:
            log.warning("recent_dedup_redis_clear_failed err=%s", type(exc).__name__)

    def aclose(self) -> None:
        """Optional close for app shutdown (sync client)."""
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            client.close()
        except Exception:
            pass


def normalize_recent_dedup_backend(raw: str | None) -> str:
    v = (raw or "memory").strip().lower()
    if v in {"redis", "memory"}:
        return v
    return "memory"


def build_recent_dedup(*, backend: str = "memory", redis_url: str = "") -> RecentDedupPort:
    """Build anti-repeat store. Redis only when backend=redis and redis_url set; else memory."""
    backend_norm = normalize_recent_dedup_backend(backend)
    url = (redis_url or "").strip()
    if backend_norm == "redis" and url:
        return RedisRecentDedup(redis_url=url, backend="redis")
    return MemoryRecentDedup()
