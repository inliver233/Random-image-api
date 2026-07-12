from __future__ import annotations

from typing import Mapping

from app.core.config import Settings
from app.core.env_parse import parse_bool_env
from app.core.image_edge import image_edge_is_ready, load_image_edge_config_from_settings


def residential_egress_emergency_only(settings: Settings | Mapping[str, str] | None) -> bool:
    """When true, residential proxies are emergency-only (not normal path).

    Default **True** (mandate: CF primary, residential near-zero).
    Ops can set RESIDENTIAL_EGRESS_EMERGENCY_ONLY=false for explicit transition
    back to legacy CF+residential parallel failover.
    """
    if settings is None:
        return True
    if isinstance(settings, Mapping):
        return parse_bool_env("RESIDENTIAL_EGRESS_EMERGENCY_ONLY", default=True, env=settings)
    # Settings object: prefer attribute, else True.
    if hasattr(settings, "residential_egress_emergency_only"):
        return bool(getattr(settings, "residential_egress_emergency_only"))
    return True


def allow_residential_pixiv_api_egress(
    settings: Settings | None,
    *,
    cf_candidate_count: int = 0,
    force_emergency: bool = False,
) -> bool:
    """Whether hydrate/OAuth may attempt residential after (or instead of) CF.

    Rules (mandate-aligned):
    - force_emergency=True → always allow (admin explicit emergency).
    - emergency_only=False → legacy: always allow residential failover tries.
    - emergency_only=True (default):
        - If this URL has CF candidates → **skip residential** (CF path exists).
        - If no candidates → allow residential (only path left).
    """
    if force_emergency:
        return True
    if not residential_egress_emergency_only(settings):
        return True
    # Candidates already mean CF rewrite is available for this URL; do not
    # re-check settings.ready (tests / partial settings still demote correctly).
    if int(cf_candidate_count) > 0:
        return False
    # No CF path for this URL → residential remains last resort.
    return True


def allow_residential_image_origin(
    settings: Settings | None,
    *,
    force_emergency: bool = False,
    allow_override: bool | None = None,
) -> bool:
    """Whether public local cascade may pick residential proxy for origin stream.

    - allow_override True/False wins (explicit caller).
    - force_emergency=True → allow.
    - emergency_only + image edge ready → deny residential.
    - else: deny when edge ready (existing soft deprecation), allow when edge off.
    """
    if allow_override is not None:
        return bool(allow_override)
    if force_emergency:
        return True
    if settings is None:
        return True
    edge_ready = image_edge_is_ready(settings)
    if residential_egress_emergency_only(settings):
        # Edge ready → residential off; edge off → residential is the only path.
        return not edge_ready
    # Legacy: skip residential when edge configured (same as prior origin_stream default).
    return load_image_edge_config_from_settings(settings) is None


def egress_policy_snapshot(settings: Settings | None) -> dict[str, object]:
    """Ops-facing snapshot (no secrets)."""
    from app.core.cf_api_proxy import load_cf_api_proxy_config_from_settings

    emergency = residential_egress_emergency_only(settings)
    cf_cfg = load_cf_api_proxy_config_from_settings(settings) if settings is not None else None
    cf_ready = bool(cf_cfg is not None and cf_cfg.ready)
    edge_ready = image_edge_is_ready(settings) if settings is not None else False
    # When emergency_only: residential skipped if CF/edge ready.
    api_residential_if_cf_ready = not emergency
    image_residential_if_edge_ready = not emergency
    return {
        "residential_egress_emergency_only": emergency,
        "cf_api_proxy_ready": cf_ready,
        "image_edge_ready": edge_ready,
        "pixiv_api_allows_residential_when_cf_ready": api_residential_if_cf_ready,
        "image_origin_allows_residential_when_edge_ready": image_residential_if_edge_ready,
    }
