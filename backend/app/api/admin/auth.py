from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Request

from app.api.admin.deps import get_admin_claims
from app.core.errors import ApiError, ErrorCode
from app.core.admin_json import admin_ok
from app.core.admin_request import load_json_object
from app.core.request_id import get_or_create_request_id
from app.core.security import create_jwt
from fastapi import Depends

router = APIRouter()


async def _load_login_json(request: Request) -> tuple[str, str]:
    data = await load_json_object(request)

    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    if not username or not password:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Missing credentials", status_code=400)

    return username, password


@router.post("/login")
async def login(request: Request) -> dict[str, Any]:
    username, password = await _load_login_json(request)
    settings = request.app.state.settings

    if username != settings.admin_username or not hmac.compare_digest(password, settings.admin_password):
        raise ApiError(code=ErrorCode.UNAUTHORIZED, message="Invalid credentials", status_code=401)

    token = create_jwt(secret_key=settings.secret_key, subject=settings.admin_username, ttl_s=3600)
    rid = get_or_create_request_id(request)
    return admin_ok(request, payload={"token": token}, request_id=rid)


@router.post("/logout")
async def logout(request: Request, _claims: dict[str, Any] = Depends(get_admin_claims)) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)
    return admin_ok(request, request_id=rid)
