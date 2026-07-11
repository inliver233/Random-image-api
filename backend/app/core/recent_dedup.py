from __future__ import annotations

from collections import deque
from threading import Lock


# Best-effort global de-dup (process-local): reduce short-term duplicates without extra DB writes.
_RECENT_LOCK = Lock()
_RECENT_IMAGES: deque[tuple[float, int]] = deque()
_RECENT_AUTHORS: deque[tuple[float, int]] = deque()


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
