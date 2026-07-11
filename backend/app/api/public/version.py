from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Request

from app.core.public_json import public_ok_json

router = APIRouter()


def _get_env(key: str, default: str) -> str:
    value = os.environ.get(key, default)
    return str(value).strip()


@router.get("/version")
async def version(request: Request) -> Any:
    return public_ok_json(
        request,
        payload={
            "version": _get_env("APP_VERSION", "dev"),
            "build_time": _get_env("APP_BUILD_TIME", ""),
            "git_commit": _get_env("APP_COMMIT", ""),
        },
    )
