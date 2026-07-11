from __future__ import annotations

from app.core.config import load_settings
from app.core.random_engine_client import (
    random_engine_base_url,
    random_engine_traffic_percent,
    should_route_pick_to_engine,
)
from app.core.random_engine_pick import build_engine_pick_payload
from app.core.r2_prewarm import r2_prewarm_enabled


class _FixedRng:
    def __init__(self, value: float) -> None:
        self._value = float(value)

    def random(self) -> float:
        return self._value


def test_random_engine_settings_default_off() -> None:
    s = load_settings({})
    assert s.random_engine_url == ""
    assert s.random_engine_enabled is False
    assert s.random_engine_timeout_ms == 800
    assert s.random_engine_traffic_percent == 100
    assert s.r2_prewarm_enabled is False
    assert s.r2_prewarm_url == ""
    assert random_engine_base_url(s) is None
    assert should_route_pick_to_engine(s) is False
    assert r2_prewarm_enabled(s) is False


def test_random_engine_settings_enabled_requires_url() -> None:
    s = load_settings({"RANDOM_ENGINE_ENABLED": "1", "RANDOM_ENGINE_URL": ""})
    assert s.random_engine_enabled is False

    s2 = load_settings(
        {
            "RANDOM_ENGINE_ENABLED": "1",
            "RANDOM_ENGINE_URL": "http://127.0.0.1:8091/",
            "RANDOM_ENGINE_TIMEOUT_MS": "500",
        }
    )
    assert s2.random_engine_enabled is True
    assert s2.random_engine_url == "http://127.0.0.1:8091"
    assert s2.random_engine_timeout_ms == 500
    assert s2.random_engine_traffic_percent == 100
    assert random_engine_base_url(s2) == "http://127.0.0.1:8091"
    assert should_route_pick_to_engine(s2) is True


def test_random_engine_traffic_percent_cutover() -> None:
    base = {
        "RANDOM_ENGINE_ENABLED": "1",
        "RANDOM_ENGINE_URL": "http://127.0.0.1:8091",
        "RANDOM_ENGINE_TRAFFIC_PERCENT": "0",
    }
    s0 = load_settings(base)
    assert random_engine_traffic_percent(s0) == 0
    assert should_route_pick_to_engine(s0) is False

    s25 = load_settings({**base, "RANDOM_ENGINE_TRAFFIC_PERCENT": "25"})
    assert random_engine_traffic_percent(s25) == 25
    # roll=0.10 → 10 < 25 → route; roll=0.50 → 50 < 25 → skip
    assert should_route_pick_to_engine(s25, rng=_FixedRng(0.10)) is True
    assert should_route_pick_to_engine(s25, rng=_FixedRng(0.50)) is False

    s100 = load_settings({**base, "RANDOM_ENGINE_TRAFFIC_PERCENT": "100"})
    assert should_route_pick_to_engine(s100, rng=_FixedRng(0.99)) is True


def test_r2_prewarm_requires_url_and_flag() -> None:
    s = load_settings({"R2_PREWARM_ENABLED": "1", "R2_PREWARM_URL": ""})
    assert s.r2_prewarm_enabled is False
    s2 = load_settings({"R2_PREWARM_ENABLED": "1", "R2_PREWARM_URL": "http://127.0.0.1:8787/"})
    assert s2.r2_prewarm_enabled is True
    assert s2.r2_prewarm_url == "http://127.0.0.1:8787"
    assert r2_prewarm_enabled(s2) is True


def test_build_engine_pick_payload() -> None:
    body = build_engine_pick_payload(
        filters={"r18": 0, "included_tags": ["a"]},
        strategy="quality",
        quality={"samples": 12, "pick_mode": "best"},
        seed="x",
        limit=1,
        debug=True,
    )
    assert body["strategy"] == "quality"
    assert body["seed"] == "x"
    assert body["quality"]["samples"] == 12
    assert body["filters"]["r18"] == 0


def test_build_engine_pick_payload_batch_limit() -> None:
    body = build_engine_pick_payload(
        filters={"r18": 0},
        strategy="random",
        quality=None,
        seed=None,
        limit=12,
        debug=False,
    )
    assert body["limit"] == 12
    assert "quality" not in body
    assert "seed" not in body
