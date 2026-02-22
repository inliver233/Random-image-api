from __future__ import annotations


def refresh_backoff_seconds(*, attempt: int, status_code: int | None) -> int:
    attempt_i = int(attempt)
    if attempt_i <= 0:
        return 0

    status = int(status_code) if status_code is not None else None

    # Auth / token invalid style failures: back off much longer.
    if status in {400, 401, 403}:
        schedule = {
            1: 3600,
            2: 6 * 3600,
            3: 24 * 3600,
            4: 3 * 24 * 3600,
            5: 7 * 24 * 3600,
        }
        if attempt_i in schedule:
            return schedule[attempt_i]

        base = 7 * 24 * 3600
        seconds = base * (2 ** (attempt_i - 5))
        return min(seconds, 30 * 24 * 3600)

    # Network / transient failures: short backoff.
    schedule = {
        1: 5,
        2: 30,
        3: 120,
        4: 600,
        5: 1800,
    }
    if attempt_i in schedule:
        return schedule[attempt_i]

    base = 1800
    seconds = base * (2 ** (attempt_i - 5))
    return min(seconds, 6 * 3600)
