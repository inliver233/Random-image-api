from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.core.egress_policy import (
    allow_residential_image_origin,
    allow_residential_pixiv_api_egress,
    egress_policy_snapshot,
    is_force_residential_emergency,
    residential_egress_emergency_only,
    reset_force_residential_emergency_for_tests,
    set_force_residential_emergency,
)


def test_residential_emergency_only_default_true() -> None:
    assert residential_egress_emergency_only(None) is True
    assert residential_egress_emergency_only({"RESIDENTIAL_EGRESS_EMERGENCY_ONLY": "0"}) is False
    assert residential_egress_emergency_only(SimpleNamespace(residential_egress_emergency_only=True)) is True


def test_allow_residential_pixiv_api_when_cf_candidates_emergency() -> None:
    settings = SimpleNamespace(residential_egress_emergency_only=True)
    assert allow_residential_pixiv_api_egress(settings, cf_candidate_count=2) is False
    assert (
        allow_residential_pixiv_api_egress(settings, cf_candidate_count=2, force_emergency=True)
        is True
    )


def test_allow_residential_pixiv_api_when_cf_empty() -> None:
    settings = SimpleNamespace(residential_egress_emergency_only=True)
    # No CF candidates for this URL → residential last resort.
    assert allow_residential_pixiv_api_egress(settings, cf_candidate_count=0) is True


def test_allow_residential_image_origin_edge_ready() -> None:
    settings = SimpleNamespace(residential_egress_emergency_only=True)
    reset_force_residential_emergency_for_tests()
    with patch("app.core.egress_policy.image_edge_is_ready", return_value=True):
        assert allow_residential_image_origin(settings) is False
        assert allow_residential_image_origin(settings, force_emergency=True) is True
        assert allow_residential_image_origin(settings, allow_override=True) is True


def test_process_force_residential_emergency_override() -> None:
    settings = SimpleNamespace(residential_egress_emergency_only=True)
    reset_force_residential_emergency_for_tests()
    assert is_force_residential_emergency() is False
    assert allow_residential_pixiv_api_egress(settings, cf_candidate_count=2) is False
    set_force_residential_emergency(True)
    try:
        assert is_force_residential_emergency() is True
        assert allow_residential_pixiv_api_egress(settings, cf_candidate_count=2) is True
        with patch("app.core.egress_policy.image_edge_is_ready", return_value=True):
            assert allow_residential_image_origin(settings) is True
        snap = egress_policy_snapshot(settings)
        assert snap["force_residential_emergency"] is True
        assert snap["pixiv_api_allows_residential_when_cf_ready"] is True
    finally:
        reset_force_residential_emergency_for_tests()
