from __future__ import annotations

from app.core.config import load_settings
from app.core.random_engine_client import random_engine_base_url
from app.core.random_engine_pick import build_engine_pick_payload


def test_random_engine_settings_default_off() -> None:
    s = load_settings({})
    assert s.random_engine_url == ""
    assert s.random_engine_enabled is False
    assert s.random_engine_timeout_ms == 800
    assert random_engine_base_url(s) is None


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
    assert random_engine_base_url(s2) == "http://127.0.0.1:8091"


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
