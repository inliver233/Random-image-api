from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RandomRequestStatsSnapshot:
    total_requests: int
    total_ok: int
    total_error: int
    in_flight: int
    window_seconds: int
    last_window_requests: int
    last_window_ok: int
    last_window_error: int
    last_window_success_rate: float


class RandomRequestStats:
    def __init__(self, *, window_seconds: int = 60) -> None:
        self._window_s = max(1, int(window_seconds))
        self._lock = asyncio.Lock()

        self._total_requests = 0
        self._total_ok = 0
        self._total_error = 0
        self._in_flight = 0

        # (monotonic_seconds, ok)
        self._events: deque[tuple[float, bool]] = deque()

    async def set_totals(self, *, total_requests: int, total_ok: int, total_error: int) -> None:
        tr = max(0, int(total_requests))
        tok = max(0, int(total_ok))
        terr = max(0, int(total_error))
        async with self._lock:
            self._total_requests = tr
            self._total_ok = tok
            self._total_error = terr
            self._in_flight = max(0, int(self._in_flight))

    def _purge(self, *, now_m: float) -> None:
        cutoff = float(now_m) - float(self._window_s)
        while self._events:
            t, _ok = self._events[0]
            if float(t) >= cutoff:
                break
            self._events.popleft()

    async def on_begin(self) -> float:
        now_m = float(time.monotonic())
        async with self._lock:
            self._total_requests += 1
            self._in_flight += 1
        return now_m

    async def on_end(self, *, status_code: int) -> None:
        now_m = float(time.monotonic())
        ok = 200 <= int(status_code) < 400

        async with self._lock:
            self._in_flight = max(0, int(self._in_flight) - 1)
            if ok:
                self._total_ok += 1
            else:
                self._total_error += 1
            self._events.append((now_m, bool(ok)))
            self._purge(now_m=now_m)

    async def snapshot(self) -> RandomRequestStatsSnapshot:
        now_m = float(time.monotonic())
        async with self._lock:
            self._purge(now_m=now_m)
            last_total = int(len(self._events))
            last_ok = int(sum(1 for _t, ok in self._events if ok))
            last_error = int(last_total - last_ok)
            rate = float(last_ok) / float(last_total) if last_total > 0 else 0.0

            return RandomRequestStatsSnapshot(
                total_requests=int(self._total_requests),
                total_ok=int(self._total_ok),
                total_error=int(self._total_error),
                in_flight=int(self._in_flight),
                window_seconds=int(self._window_s),
                last_window_requests=int(last_total),
                last_window_ok=int(last_ok),
                last_window_error=int(last_error),
                last_window_success_rate=float(rate),
            )
