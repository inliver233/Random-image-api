from __future__ import annotations

import pytest

from app.pixiv.token_strategy import NoTokenAvailable, TokenCandidate, choose_token


def test_round_robin_basic() -> None:
    tokens = [
        TokenCandidate(id=1, enabled=True, weight=1.0, error_count=0),
        TokenCandidate(id=2, enabled=True, weight=1.0, error_count=0),
        TokenCandidate(id=3, enabled=True, weight=1.0, error_count=0),
    ]

    t1, last = choose_token(tokens, strategy="round_robin", now=100.0, last_id=None)
    assert t1.id == 1

    t2, last = choose_token(tokens, strategy="round_robin", now=100.0, last_id=last)
    assert t2.id == 2

    t3, last = choose_token(tokens, strategy="round_robin", now=100.0, last_id=last)
    assert t3.id == 3

    t4, last = choose_token(tokens, strategy="round_robin", now=100.0, last_id=last)
    assert t4.id == 1


def test_round_robin_skips_backoff() -> None:
    tokens = [
        TokenCandidate(id=1, enabled=True, weight=1.0, error_count=0),
        TokenCandidate(id=2, enabled=True, weight=1.0, error_count=0, backoff_until=200.0),
        TokenCandidate(id=3, enabled=True, weight=1.0, error_count=0),
    ]

    t1, last = choose_token(tokens, strategy="round_robin", now=100.0, last_id=1)
    assert t1.id == 3

    t2, last = choose_token(tokens, strategy="round_robin", now=100.0, last_id=last)
    assert t2.id == 1


def test_least_error_with_round_robin_tiebreak() -> None:
    tokens = [
        TokenCandidate(id=1, enabled=True, weight=1.0, error_count=2),
        TokenCandidate(id=2, enabled=True, weight=1.0, error_count=0),
        TokenCandidate(id=3, enabled=True, weight=1.0, error_count=0),
    ]

    t1, last = choose_token(tokens, strategy="least_error", now=100.0, last_id=None)
    assert t1.id == 2

    t2, last = choose_token(tokens, strategy="least_error", now=100.0, last_id=last)
    assert t2.id == 3

    t3, last = choose_token(tokens, strategy="least_error", now=100.0, last_id=last)
    assert t3.id == 2


def test_weighted_choice_deterministic_r() -> None:
    tokens = [
        TokenCandidate(id=1, enabled=True, weight=1.0, error_count=0),
        TokenCandidate(id=2, enabled=True, weight=3.0, error_count=0),
        TokenCandidate(id=3, enabled=True, weight=0.0, error_count=0),
    ]

    t1, _ = choose_token(tokens, strategy="weighted", now=100.0, last_id=None, r=0.0)
    assert t1.id == 1

    t2, _ = choose_token(tokens, strategy="weighted", now=100.0, last_id=None, r=0.25)
    assert t2.id == 2

    t3, _ = choose_token(tokens, strategy="weighted", now=100.0, last_id=None, r=0.999)
    assert t3.id == 2


def test_weighted_falls_back_to_round_robin_when_all_zero() -> None:
    tokens = [
        TokenCandidate(id=1, enabled=True, weight=0.0, error_count=0),
        TokenCandidate(id=2, enabled=True, weight=0.0, error_count=0),
    ]

    t1, last = choose_token(tokens, strategy="weighted", now=100.0, last_id=None, r=0.99)
    assert t1.id == 1

    t2, _ = choose_token(tokens, strategy="weighted", now=100.0, last_id=last, r=0.99)
    assert t2.id == 2


def test_no_token_available_reports_next_retry() -> None:
    tokens = [
        TokenCandidate(id=1, enabled=True, weight=1.0, error_count=0, backoff_until=200.0),
        TokenCandidate(id=2, enabled=True, weight=1.0, error_count=0, backoff_until=150.0),
    ]

    with pytest.raises(NoTokenAvailable) as excinfo:
        choose_token(tokens, strategy="round_robin", now=100.0, last_id=None)
    assert excinfo.value.next_retry_at == 150.0

