from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.cf_api_proxy import resolve_pixiv_api_cf_candidates
from app.core.config import Settings
from app.core.egress_policy import allow_residential_pixiv_api_egress
from app.core.proxy_routing import ProxyUri, select_proxy_uri_for_url
from app.core.runtime_settings import RuntimeConfig


@dataclass(frozen=True, slots=True)
class EgressAttempt:
    """One CF or residential try for Pixiv API egress (OAuth / App API).

    CF attempts rewrite the URL and inject secret headers; residential attempts
    keep the original URL and may attach a pool proxy URI.
    """

    via_cf: bool
    request_url: str
    extra_headers: dict[str, str]
    residential: ProxyUri | None

    @property
    def proxy_uri(self) -> str | None:
        if self.via_cf or self.residential is None:
            return None
        return self.residential.uri


async def iter_pixiv_api_egress(
    engine: AsyncEngine,
    settings: Settings,
    runtime: RuntimeConfig,
    *,
    url: str,
    token_id: int | None = None,
    residential_failover_attempts: int = 0,
    force_residential_emergency: bool = False,
) -> AsyncIterator[EgressAttempt]:
    """Yield CF multi-base candidates first, then residential picks when allowed.

    Residential is skipped when CF is ready and ``RESIDENTIAL_EGRESS_EMERGENCY_ONLY``
    (default True) unless ``force_residential_emergency`` is set.

    When residential is allowed, try count is ``max(1, residential_failover_attempts + 1)``
    so a single direct (no-proxy) try still runs when pools are empty and fail-open.
    Each residential yield re-selects so blacklist updates apply mid-failover.
    """
    raw = (url or "").strip()
    if not raw:
        return

    cf_candidates = resolve_pixiv_api_cf_candidates(settings=settings, url=raw)
    for cf_url, cf_headers in cf_candidates:
        yield EgressAttempt(
            via_cf=True,
            request_url=str(cf_url),
            extra_headers=dict(cf_headers or {}),
            residential=None,
        )

    if not allow_residential_pixiv_api_egress(
        settings,
        cf_candidate_count=len(cf_candidates),
        force_emergency=force_residential_emergency,
    ):
        return

    residential_tries = max(1, int(residential_failover_attempts) + 1)
    for _ in range(residential_tries):
        picked: ProxyUri | None = await select_proxy_uri_for_url(
            engine,
            settings,
            runtime,
            url=raw,
            token_id=int(token_id) if token_id is not None and int(token_id) > 0 else None,
        )
        yield EgressAttempt(
            via_cf=False,
            request_url=raw,
            extra_headers={},
            residential=picked,
        )


def egress_attempt_count(
    *,
    cf_candidate_count: int,
    residential_failover_attempts: int,
    include_residential: bool = True,
) -> int:
    """Total tries for a CF-first + optional residential failover plan (pure helper)."""
    residential = max(1, int(residential_failover_attempts) + 1) if include_residential else 0
    return max(0, int(cf_candidate_count)) + residential
