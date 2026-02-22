from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


class NoTokenAvailable(RuntimeError):
    def __init__(self, *, next_retry_at: float | None) -> None:
        super().__init__("No eligible token available")
        self.next_retry_at = next_retry_at


@dataclass(frozen=True, slots=True)
class TokenCandidate:
    id: int
    enabled: bool
    weight: float
    error_count: int
    backoff_until: float = 0.0


def _eligible(tokens: Iterable[TokenCandidate], *, now: float) -> list[TokenCandidate]:
    out: list[TokenCandidate] = []
    for t in tokens:
        if not t.enabled:
            continue
        if float(t.backoff_until) > float(now):
            continue
        out.append(t)
    out.sort(key=lambda x: int(x.id))
    return out


def _next_retry_at(tokens: Sequence[TokenCandidate]) -> float | None:
    values: list[float] = []
    for t in tokens:
        if not t.enabled:
            continue
        values.append(float(t.backoff_until))
    return min(values) if values else None


def _choose_round_robin(tokens: Sequence[TokenCandidate], *, last_id: int | None) -> TokenCandidate:
    if not tokens:
        raise ValueError("tokens is empty")
    if last_id is None:
        return tokens[0]
    ids = [int(t.id) for t in tokens]
    try:
        idx = ids.index(int(last_id))
    except ValueError:
        return tokens[0]
    return tokens[(idx + 1) % len(tokens)]


def _choose_weighted(
    tokens: Sequence[TokenCandidate],
    *,
    r: float,
    last_id: int | None,
) -> TokenCandidate:
    if not tokens:
        raise ValueError("tokens is empty")

    weights = [max(0.0, float(t.weight)) for t in tokens]
    total = sum(weights)
    if total <= 0.0:
        return _choose_round_robin(tokens, last_id=last_id)

    r = float(r)
    if r < 0.0:
        r = 0.0
    if r >= 1.0:
        r = 0.999999999

    target = r * total
    chosen = tokens[0]
    for token, w in zip(tokens, weights, strict=True):
        if w <= 0.0:
            continue
        chosen = token
        if target < w:
            return token
        target -= w
    return chosen


def choose_token(
    tokens: Sequence[TokenCandidate],
    *,
    strategy: str,
    now: float,
    last_id: int | None,
    r: float = 0.0,
) -> tuple[TokenCandidate, int]:
    eligible = _eligible(tokens, now=now)
    if not eligible:
        raise NoTokenAvailable(next_retry_at=_next_retry_at(tokens))

    strategy = (strategy or "").strip().lower()
    if strategy in {"round_robin", ""}:
        token = _choose_round_robin(eligible, last_id=last_id)
        return token, int(token.id)

    if strategy == "least_error":
        min_err = min(int(t.error_count) for t in eligible)
        best = [t for t in eligible if int(t.error_count) == min_err]
        token = _choose_round_robin(best, last_id=last_id)
        return token, int(token.id)

    if strategy == "weighted":
        token = _choose_weighted(eligible, r=r, last_id=last_id)
        return token, int(token.id)

    raise ValueError("Unsupported strategy")

