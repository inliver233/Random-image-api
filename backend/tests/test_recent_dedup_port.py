from __future__ import annotations

from app.core.recent_dedup import (
    MemoryRecentDedup,
    RecentDedupPort,
    RedisRecentDedup,
    build_recent_dedup,
    clear_recent,
    normalize_recent_dedup_backend,
)


def test_normalize_recent_dedup_backend() -> None:
    assert normalize_recent_dedup_backend("memory") == "memory"
    assert normalize_recent_dedup_backend("REDIS") == "redis"
    assert normalize_recent_dedup_backend("weird") == "memory"
    assert normalize_recent_dedup_backend(None) == "memory"


def test_build_recent_dedup_defaults_to_memory() -> None:
    store = build_recent_dedup()
    assert isinstance(store, MemoryRecentDedup)
    assert store.backend == "memory"
    assert isinstance(store, RecentDedupPort)


def test_build_recent_dedup_unknown_falls_back() -> None:
    store = build_recent_dedup(backend="nats")
    assert isinstance(store, MemoryRecentDedup)
    assert store.backend == "memory"


def test_build_redis_without_url_falls_back_to_memory() -> None:
    store = build_recent_dedup(backend="redis", redis_url="")
    assert isinstance(store, MemoryRecentDedup)
    assert store.backend == "memory"


def test_build_redis_with_url_selects_redis_backend() -> None:
    store = build_recent_dedup(backend="redis", redis_url="redis://127.0.0.1:6379/0")
    assert isinstance(store, RedisRecentDedup)
    assert store.backend == "redis"


def test_memory_recent_dedup_record_and_lists() -> None:
    clear_recent()
    store = build_recent_dedup(backend="memory")
    store.record(
        now=1000.0,
        image_id=7,
        user_id=3,
        window_s=60.0,
        max_images=100,
        max_authors=50,
    )
    images, authors = store.get_lists(1000.0, window_s=60.0, max_images=100, max_authors=50)
    assert 7 in images
    assert 3 in authors
    img_set, author_set = store.get_sets(1000.0, window_s=60.0, max_images=100, max_authors=50)
    assert 7 in img_set
    assert 3 in author_set
    store.clear()
    images2, authors2 = store.get_lists(1000.0, window_s=60.0, max_images=100, max_authors=50)
    assert images2 == []
    assert authors2 == []


def test_redis_recent_dedup_fails_open_when_client_unavailable() -> None:
    """Without a live Redis, get/record fail open to process-local memory."""
    clear_recent()
    store = RedisRecentDedup(redis_url="redis://127.0.0.1:1/0")  # nothing listening
    store.record(
        now=1000.0,
        image_id=42,
        user_id=9,
        window_s=60.0,
        max_images=100,
        max_authors=50,
    )
    images, authors = store.get_lists(1000.0, window_s=60.0, max_images=100, max_authors=50)
    assert 42 in images
    assert 9 in authors
    store.clear()
    images2, authors2 = store.get_lists(1000.0, window_s=60.0, max_images=100, max_authors=50)
    assert images2 == []
    assert authors2 == []


class _FakeRedis:
    """Minimal sorted-set stub for RedisRecentDedup unit tests (no live Redis)."""

    def __init__(self) -> None:
        self.zsets: dict[str, dict[str, float]] = {}

    def ping(self) -> bool:
        return True

    def zadd(self, key: str, mapping: dict[str, float]) -> int:
        bucket = self.zsets.setdefault(key, {})
        for member, score in mapping.items():
            bucket[str(member)] = float(score)
        return len(mapping)

    def zremrangebyscore(self, key: str, min_s: str | float, max_s: float) -> int:
        bucket = self.zsets.get(key) or {}
        # min_s may be "-inf"
        lo = float("-inf") if min_s == "-inf" else float(min_s)
        hi = float(max_s)
        drop = [m for m, sc in bucket.items() if lo <= float(sc) <= hi]
        for m in drop:
            del bucket[m]
        return len(drop)

    def zcard(self, key: str) -> int:
        return len(self.zsets.get(key) or {})

    def zremrangebyrank(self, key: str, start: int, end: int) -> int:
        bucket = self.zsets.get(key) or {}
        ordered = sorted(bucket.items(), key=lambda kv: (float(kv[1]), kv[0]))
        # inclusive end like Redis
        to_drop = ordered[start : end + 1]
        for m, _ in to_drop:
            del bucket[m]
        return len(to_drop)

    def zrange(self, key: str, start: int, end: int) -> list[str]:
        bucket = self.zsets.get(key) or {}
        ordered = [m for m, _ in sorted(bucket.items(), key=lambda kv: (float(kv[1]), kv[0]))]
        if end == -1:
            end = len(ordered) - 1
        if not ordered or start > end:
            return []
        return ordered[start : end + 1]

    def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            if k in self.zsets:
                del self.zsets[k]
                n += 1
        return n

    def pipeline(self, transaction: bool = False) -> "_FakePipe":
        return _FakePipe(self)

    def close(self) -> None:
        return None


class _FakePipe:
    def __init__(self, client: _FakeRedis) -> None:
        self._client = client
        self._ops: list[tuple[str, tuple, dict]] = []

    def zadd(self, key: str, mapping: dict[str, float]) -> "_FakePipe":
        self._ops.append(("zadd", (key, mapping), {}))
        return self

    def execute(self) -> list[object]:
        out: list[object] = []
        for name, args, _kw in self._ops:
            if name == "zadd":
                out.append(self._client.zadd(*args))
        self._ops.clear()
        return out


def test_redis_recent_dedup_with_fake_client() -> None:
    store = RedisRecentDedup(redis_url="redis://fake")
    fake = _FakeRedis()
    store._client = fake  # inject without network
    store.record(
        now=1.0,
        image_id=11,
        user_id=22,
        window_s=3600.0,
        max_images=10,
        max_authors=10,
    )
    # Fire-and-forget Redis write — give the pool a beat to flush.
    import time as _time

    _time.sleep(0.05)
    images, authors = store.get_lists(1.0, window_s=3600.0, max_images=10, max_authors=10)
    assert images == [11]
    assert authors == [22]
    store.clear()
    images2, authors2 = store.get_lists(1.0, window_s=3600.0, max_images=10, max_authors=10)
    assert images2 == []
    assert authors2 == []


def test_redis_recent_dedup_get_lists_budgeted_on_slow_client() -> None:
    """Slow Redis fetch must not stall pick plan past the hard timeout."""
    import time as _time

    from app.core import recent_dedup as rd

    class _SlowRedis(_FakeRedis):
        def zrange(self, key: str, start: int, end: int) -> list[str]:
            # Exceed hard timeout (0.15s) without parking the shared pool for seconds.
            _time.sleep(0.35)
            return super().zrange(key, start, end)

    clear_recent()
    store = RedisRecentDedup(redis_url="redis://fake")
    store._client = _SlowRedis()
    # Seed local memory so fail-open has data.
    store._fallback.record(
        now=1000.0,
        image_id=77,
        user_id=8,
        window_s=60.0,
        max_images=100,
        max_authors=50,
    )
    t0 = _time.monotonic()
    images, authors = store.get_lists(1000.0, window_s=60.0, max_images=100, max_authors=50)
    elapsed = _time.monotonic() - t0
    assert elapsed < 0.8, f"get_lists hung too long: {elapsed:.3f}s"
    assert elapsed >= float(rd._REDIS_CALL_TIMEOUT_S) * 0.4
    assert 77 in images
    assert 8 in authors
    clear_recent()


def test_redis_recent_dedup_list_cache_avoids_second_redis_rtt() -> None:
    """Warm list cache must be served without waiting on Redis again."""
    import time as _time

    class _CountingRedis(_FakeRedis):
        def __init__(self) -> None:
            super().__init__()
            self.zrange_calls = 0

        def zrange(self, key: str, start: int, end: int) -> list[str]:
            self.zrange_calls += 1
            return super().zrange(key, start, end)

    store = RedisRecentDedup(redis_url="redis://fake")
    fake = _CountingRedis()
    store._client = fake
    store._store_list_cache([5], [6])
    t0 = _time.monotonic()
    images, authors = store.get_lists(1.0, window_s=60.0, max_images=10, max_authors=10)
    elapsed = _time.monotonic() - t0
    assert images == [5]
    assert authors == [6]
    assert elapsed < 0.05
    # Cache hit path does not call zrange synchronously.
    assert fake.zrange_calls == 0


def test_redis_recent_dedup_record_returns_immediately() -> None:
    """record dual-writes memory and returns without waiting on slow Redis."""
    import time as _time

    class _SlowWriteRedis(_FakeRedis):
        def pipeline(self, transaction: bool = False) -> "_SlowPipe":  # type: ignore[name-defined]
            return _SlowPipe(self)

    class _SlowPipe(_FakePipe):
        def execute(self) -> list[object]:
            _time.sleep(0.5)
            return super().execute()

    clear_recent()
    store = RedisRecentDedup(redis_url="redis://fake")
    store._client = _SlowWriteRedis()
    t0 = _time.monotonic()
    store.record(
        now=2000.0,
        image_id=99,
        user_id=1,
        window_s=60.0,
        max_images=100,
        max_authors=50,
    )
    elapsed = _time.monotonic() - t0
    assert elapsed < 0.15, f"record blocked on Redis: {elapsed:.3f}s"
    images, _authors = store._fallback.get_lists(2000.0, window_s=60.0, max_images=100, max_authors=50)
    assert 99 in images
    clear_recent()
