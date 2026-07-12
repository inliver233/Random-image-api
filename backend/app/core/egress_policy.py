from __future__ import annotations

import threading
from typing import Mapping

from app.core.config import Settings
from app.core.env_parse import parse_bool_env
from app.core.image_edge import image_edge_is_ready, load_image_edge_config_from_settings

# Process-local admin override: allow residential even when CF/edge ready.
# Not durable across processes/restarts; env RESIDENTIAL_EGRESS_EMERGENCY_ONLY remains the durable policy.
_force_lock = threading.RLock()
_force_residential_emergency: bool = False


def is_force_residential_emergency() -> bool:
    with _force_lock:
        return bool(_force_residential_emergency)


def set_force_residential_emergency(enabled: bool) -> bool:
    """Set process-local emergency residential override. Returns new value."""
    global _force_residential_emergency
    with _force_lock:
        _force_residential_emergency = bool(enabled)
        return _force_residential_emergency


def reset_force_residential_emergency_for_tests() -> None:
    set_force_residential_emergency(False)


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

    Rules (mandate-aligned · TOKEN-2):
    - force_emergency=True → always allow (admin explicit emergency).
    - emergency_only=False → legacy: always allow residential failover tries.
    - emergency_only=True (default):
        - CF is still **first** (iterator yields CF candidates before residential).
        - Residential remains **last resort after CF exhaustion** (gate/5xx/transport),
          so OAuth test-refresh / hydrate do not hard-502 when every CF base fails.
        - Happy path stays near-zero residential: callers break on first CF success.

    ``cf_candidate_count`` is retained for callers/snapshot honesty; it no longer
    blocks post-CF residential under emergency_only (that was the TOKEN-2 hole).
    """
    _ = cf_candidate_count  # ordering is CF-first in iter_pixiv_api_egress; not a deny flag
    if force_emergency or is_force_residential_emergency():
        return True
    if not residential_egress_emergency_only(settings):
        return True
    # emergency_only: still allow residential after CF (or when CF list empty).
    return True


def allow_residential_image_origin(
    settings: Settings | None,
    *,
    force_emergency: bool = False,
    allow_override: bool | None = None,
) -> bool:
    """Whether public local cascade may pick residential proxy for origin stream.

    - allow_override True/False wins (explicit caller).
    - force_emergency=True (or process admin override) → allow.
    - emergency_only + image edge ready → deny residential.
    - else: deny when edge ready (existing soft deprecation), allow when edge off.
    """
    if allow_override is not None:
        return bool(allow_override)
    if force_emergency or is_force_residential_emergency():
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
    force = is_force_residential_emergency()
    cf_cfg = load_cf_api_proxy_config_from_settings(settings) if settings is not None else None
    cf_ready = bool(cf_cfg is not None and cf_cfg.ready)
    edge_ready = image_edge_is_ready(settings) if settings is not None else False
    # TOKEN-2: API always allows residential *after* CF exhaustion (CF stays first).
    # Snapshot flag means "residential may still run when CF pool is ready", not "prefer residential".
    api_residential_if_cf_ready = True
    # Image origin: emergency_only + edge ready still demotes residential unless force.
    image_residential_if_edge_ready = (not emergency) or force
    return {
        "residential_egress_emergency_only": emergency,
        "force_residential_emergency": force,
        "cf_api_proxy_ready": cf_ready,
        "image_edge_ready": edge_ready,
        "pixiv_api_allows_residential_when_cf_ready": api_residential_if_cf_ready,
        "image_origin_allows_residential_when_edge_ready": image_residential_if_edge_ready,
        "note": (
            "TOKEN-2: Pixiv API is CF-first then residential last-resort even under "
            "RESIDENTIAL_EGRESS_EMERGENCY_ONLY. Image origin still demotes residential when "
            "edge ready unless force_residential_emergency. force is process-local "
            "(admin POST …/egress-policy); does not change env."
        ),
    }
