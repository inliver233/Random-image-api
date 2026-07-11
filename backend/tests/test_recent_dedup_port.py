from __future__ import annotations

from app.core.recent_dedup import (
    MemoryRecentDedup,
    RecentDedupPort,
    build_recent_dedup,
    clear_recent,
)


def test_build_recent_dedup_defaults_to_memory() -> None:
    store = build_recent_dedup()
    assert isinstance(store, MemoryRecentDedup)
    assert store.backend == "memory"
    assert isinstance(store, RecentDedupPort)


def test_build_recent_dedup_unknown_falls_back() -> None:
    store = build_recent_dedup(backend="redis")
    assert isinstance(store, MemoryRecentDedup)
    assert store.backend == "memory"


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
