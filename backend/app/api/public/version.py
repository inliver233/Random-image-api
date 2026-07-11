from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.core.env_parse import parse_str_env
from app.core.public_json import public_ok_json

router = APIRouter()


@router.get("/version")
async def version(request: Request) -> Any:
    return public_ok_json(
        request,
        payload={
            "version": parse_str_env("APP_VERSION", default="dev"),
            "build_time": parse_str_env("APP_BUILD_TIME", default=""),
            "git_commit": parse_str_env("APP_COMMIT", default=""),
        },
    )
