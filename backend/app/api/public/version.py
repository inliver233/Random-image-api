from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.request_id import get_or_create_request_id, set_request_id_header, set_request_id_on_state

router = APIRouter()


def _get_env(key: str, default: str) -> str:
    value = os.environ.get(key, default)
    return str(value).strip()


@router.get("/version")
async def version(request: Request) -> Any:
    rid = get_or_create_request_id(request)
    set_request_id_on_state(request, rid)

    body = {
        "ok": True,
        "version": _get_env("APP_VERSION", "dev"),
        "build_time": _get_env("APP_BUILD_TIME", ""),
        "git_commit": _get_env("APP_COMMIT", ""),
        "request_id": rid,
    }

    resp = JSONResponse(status_code=200, content=body)
    set_request_id_header(resp, rid)
    return resp

