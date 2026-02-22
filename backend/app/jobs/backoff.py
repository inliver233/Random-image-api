from __future__ import annotations


def backoff_seconds(attempt: int) -> int:
    attempt_i = int(attempt)
    if attempt_i <= 0:
        return 0

    schedule = {
        1: 5,
        2: 30,
        3: 120,
        4: 600,
        5: 1800,
    }
    if attempt_i in schedule:
        return schedule[attempt_i]

    # Exponential after the documented schedule, with a hard cap.
    base = 1800
    seconds = base * (2 ** (attempt_i - 5))
    return min(seconds, 6 * 3600)

