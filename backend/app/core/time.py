from __future__ import annotations

from datetime import datetime, timezone


def iso_utc_ms(dt: datetime | None = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    dt = dt.astimezone(timezone.utc)
    ms = dt.microsecond // 1000
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms:03d}Z"

