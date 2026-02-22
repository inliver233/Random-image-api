from __future__ import annotations

from app.pixiv.refresh_backoff import refresh_backoff_seconds


def test_refresh_backoff_seconds_network_short() -> None:
    assert refresh_backoff_seconds(attempt=0, status_code=None) == 0
    assert refresh_backoff_seconds(attempt=1, status_code=None) == 5
    assert refresh_backoff_seconds(attempt=2, status_code=None) == 30
    assert refresh_backoff_seconds(attempt=3, status_code=None) == 120


def test_refresh_backoff_seconds_auth_long() -> None:
    assert refresh_backoff_seconds(attempt=1, status_code=400) == 3600
    assert refresh_backoff_seconds(attempt=2, status_code=401) == 6 * 3600
    assert refresh_backoff_seconds(attempt=3, status_code=403) == 24 * 3600


def test_refresh_backoff_seconds_auth_caps() -> None:
    assert refresh_backoff_seconds(attempt=5, status_code=400) == 7 * 24 * 3600
    assert refresh_backoff_seconds(attempt=20, status_code=401) == 30 * 24 * 3600

