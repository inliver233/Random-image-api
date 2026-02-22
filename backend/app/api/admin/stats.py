from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.admin.deps import get_admin_claims
from app.core.request_id import get_or_create_request_id

router = APIRouter()


@router.get("/stats/random")
async def get_random_stats(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)

    stats = getattr(request.app.state, "random_request_stats", None)
    if stats is None:
        snapshot = {
            "total_requests": 0,
            "total_ok": 0,
            "total_error": 0,
            "in_flight": 0,
            "window_seconds": 60,
            "last_window_requests": 0,
            "last_window_ok": 0,
            "last_window_error": 0,
            "last_window_success_rate": 0.0,
        }
    else:
        snap = await stats.snapshot()
        snapshot = asdict(snap)

    return {"ok": True, "stats": snapshot, "request_id": rid}

