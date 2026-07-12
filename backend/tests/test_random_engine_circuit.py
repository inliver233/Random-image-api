from __future__ import annotations

from app.core.random_engine_client import (
    engine_circuit_allow,
    engine_circuit_record,
    engine_circuit_snapshot,
    reset_engine_circuit_for_tests,
)


def setup_function() -> None:
    reset_engine_circuit_for_tests()


def teardown_function() -> None:
    reset_engine_circuit_for_tests()


def test_engine_circuit_closed_allows() -> None:
    assert engine_circuit_allow(now=100.0) is True
    snap = engine_circuit_snapshot()
    assert snap["state"] == "closed"
    assert snap["consecutive_failures"] == 0


def test_engine_circuit_opens_after_threshold_hard_failures() -> None:
    now = 1_000.0
    for _ in range(4):
        engine_circuit_record("unavailable", now=now)
        assert engine_circuit_allow(now=now) is True
    engine_circuit_record("unavailable", now=now)
    # 5th hard failure opens circuit.
    assert engine_circuit_allow(now=now) is False
    snap = engine_circuit_snapshot(now=now)
    assert snap["state"] == "open"
    assert snap["open_remaining_s"] > 0


def test_engine_circuit_empty_index_is_hard_failure() -> None:
    now = 2_000.0
    for _ in range(5):
        engine_circuit_record("empty_index", now=now)
    assert engine_circuit_allow(now=now) is False


def test_engine_circuit_soft_miss_does_not_trip() -> None:
    now = 3_000.0
    for _ in range(20):
        engine_circuit_record("no_match", now=now)
    assert engine_circuit_allow(now=now) is True
    assert engine_circuit_snapshot()["state"] == "closed"


def test_engine_circuit_success_resets_failures() -> None:
    now = 4_000.0
    for _ in range(4):
        engine_circuit_record("unavailable", now=now)
    engine_circuit_record("ok", now=now)
    assert engine_circuit_snapshot()["consecutive_failures"] == 0
    engine_circuit_record("unavailable", now=now)
    assert engine_circuit_snapshot()["consecutive_failures"] == 1
    assert engine_circuit_allow(now=now) is True


def test_engine_circuit_half_open_single_probe() -> None:
    now = 5_000.0
    for _ in range(5):
        engine_circuit_record("unavailable", now=now)
    assert engine_circuit_allow(now=now) is False

    # After cool-down, one probe is allowed.
    probe_at = now + 31.0
    assert engine_circuit_allow(now=probe_at) is True
    # Concurrent requests during half-open stay on Python.
    assert engine_circuit_allow(now=probe_at) is False

    # Probe success closes circuit.
    engine_circuit_record("ok", now=probe_at)
    assert engine_circuit_allow(now=probe_at + 0.1) is True
    assert engine_circuit_snapshot()["state"] == "closed"


def test_engine_circuit_half_open_failure_reopens() -> None:
    now = 6_000.0
    for _ in range(5):
        engine_circuit_record("unavailable", now=now)
    probe_at = now + 31.0
    assert engine_circuit_allow(now=probe_at) is True
    # One hard failure while half-open (counter was reset on open) does not re-open
    # until threshold again — record enough hard failures.
    for _ in range(5):
        engine_circuit_record("unavailable", now=probe_at)
    assert engine_circuit_allow(now=probe_at) is False
