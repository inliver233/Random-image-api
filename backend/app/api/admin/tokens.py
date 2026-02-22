from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request

from app.api.admin.deps import get_admin_claims
from app.core.crypto import FieldEncryptor, mask_secret
from app.core.errors import ApiError, ErrorCode
from app.core.proxy_routing import select_proxy_uri_for_url
from app.core.request_id import get_or_create_request_id
from app.core.runtime_settings import load_runtime_config
from app.core.time import iso_utc_ms
from app.db.models.pixiv_tokens import PixivToken
from app.db.models.token_proxy_bindings import TokenProxyBinding
from app.db.session import create_sessionmaker, with_sqlite_busy_retry
from app.pixiv.oauth import OAUTH_TOKEN_PATH, PixivOauthConfig, PixivOauthError, refresh_access_token
from app.pixiv.refresh_backoff import refresh_backoff_seconds

router = APIRouter()


def _parse_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "y", "on"}:
            return True
        if v in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _parse_bool_strict(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "y", "on"}:
            return True
        if v in {"0", "false", "no", "n", "off"}:
            return False
    return None


async def _load_create_token_json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception as exc:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400) from exc

    if not isinstance(data, dict):
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400)

    refresh_token = str(data.get("refresh_token") or "").strip()
    if not refresh_token:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Missing refresh_token", status_code=400)
    if len(refresh_token) > 2048:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported refresh_token", status_code=400)

    label_raw = data.get("label")
    label = str(label_raw).strip() if label_raw is not None else None
    label = label if label else None

    enabled = _parse_bool(data.get("enabled"), default=True)

    weight_raw = data.get("weight", 1.0)
    try:
        weight = float(weight_raw)
    except Exception as exc:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported weight", status_code=400) from exc
    if weight < 0.0 or weight > 100.0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported weight", status_code=400)

    return {
        "label": label,
        "enabled": enabled,
        "weight": weight,
        "refresh_token": refresh_token,
    }


async def _load_update_token_json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception as exc:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400) from exc

    if not isinstance(data, dict):
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400)
    if not data:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Missing fields", status_code=400)

    out: dict[str, Any] = {}

    if "label" in data:
        label_raw = data.get("label")
        if label_raw is None:
            label = None
        else:
            label = str(label_raw).strip() or None
        if label is not None and len(label) > 200:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported label", status_code=400)
        out["label"] = label

    if "enabled" in data:
        enabled = _parse_bool_strict(data.get("enabled"))
        if enabled is None:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported enabled", status_code=400)
        out["enabled"] = bool(enabled)

    if "weight" in data:
        weight_raw = data.get("weight")
        try:
            weight = float(weight_raw)
        except Exception as exc:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported weight", status_code=400) from exc
        if weight < 0.0 or weight > 100.0:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported weight", status_code=400)
        out["weight"] = float(weight)

    if not out:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Missing fields", status_code=400)

    return out


@router.get("/tokens")
async def list_tokens(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async with Session() as session:
        tokens = (
            (
                await session.execute(
                    sa.select(PixivToken).order_by(PixivToken.id.desc())
                )
            )
            .scalars()
            .all()
        )

    items = [
        {
            "id": str(t.id),
            "label": t.label,
            "enabled": bool(t.enabled),
            "refresh_token_masked": t.refresh_token_masked,
            "weight": float(t.weight),
            "error_count": int(t.error_count or 0),
            "backoff_until": t.backoff_until,
            "last_ok_at": t.last_ok_at,
            "last_fail_at": t.last_fail_at,
            "last_error_code": t.last_error_code,
            "last_error_msg": t.last_error_msg,
        }
        for t in tokens
    ]

    return {"ok": True, "items": items, "request_id": rid}


@router.post("/tokens")
async def create_token(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)
    body = await _load_create_token_json(request)

    settings = request.app.state.settings
    try:
        encryptor = FieldEncryptor.from_key(settings.field_encryption_key)
    except Exception as exc:
        raise ApiError(code=ErrorCode.INTERNAL_ERROR, message="Encryption not configured", status_code=500) from exc

    refresh_token = str(body["refresh_token"])
    refresh_token_enc = encryptor.encrypt_text(refresh_token)
    refresh_token_masked = mask_secret(refresh_token)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)
    async with Session() as session:
        row = PixivToken(
            label=body["label"],
            enabled=1 if bool(body["enabled"]) else 0,
            refresh_token_enc=refresh_token_enc,
            refresh_token_masked=refresh_token_masked,
            weight=float(body["weight"]),
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)

    return {"ok": True, "token_id": str(row.id), "request_id": rid}


@router.put("/tokens/{token_id}")
async def update_token(
    token_id: int,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    if token_id <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid token id", status_code=400)

    rid = get_or_create_request_id(request)
    body = await _load_update_token_json(request)

    now = iso_utc_ms()

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async with Session() as session:
        row = await session.get(PixivToken, token_id)
        if row is None:
            raise ApiError(code=ErrorCode.NOT_FOUND, message="Token not found", status_code=404)

        if "label" in body:
            row.label = body["label"]
        if "enabled" in body:
            row.enabled = 1 if bool(body["enabled"]) else 0
        if "weight" in body:
            row.weight = float(body["weight"])

        row.updated_at = now

        await session.commit()

    return {"ok": True, "token_id": str(token_id), "request_id": rid}


@router.delete("/tokens/{token_id}")
async def delete_token(
    token_id: int,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    if token_id <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid token id", status_code=400)

    rid = get_or_create_request_id(request)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async def _op() -> dict[str, Any]:
        async with Session() as session:
            row = await session.get(PixivToken, token_id)
            if row is None:
                raise ApiError(code=ErrorCode.NOT_FOUND, message="Token not found", status_code=404)

            await session.execute(sa.delete(TokenProxyBinding).where(TokenProxyBinding.token_id == int(token_id)))
            await session.delete(row)
            await session.commit()

        return {"ok": True, "token_id": str(token_id), "request_id": rid}

    return await with_sqlite_busy_retry(_op)


@router.post("/tokens/{token_id}/test-refresh")
async def test_refresh_token(
    token_id: int,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    if token_id <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid token id", status_code=400)

    rid = get_or_create_request_id(request)

    settings = request.app.state.settings
    try:
        encryptor = FieldEncryptor.from_key(settings.field_encryption_key)
    except Exception as exc:
        raise ApiError(code=ErrorCode.INTERNAL_ERROR, message="Encryption not configured", status_code=500) from exc

    client_id = (settings.pixiv_oauth_client_id or "").strip()
    client_secret = (settings.pixiv_oauth_client_secret or "").strip()
    if not client_id or not client_secret:
        raise ApiError(code=ErrorCode.INTERNAL_ERROR, message="Pixiv OAuth not configured", status_code=500)

    config = PixivOauthConfig(
        client_id=client_id,
        client_secret=client_secret,
        hash_secret=(settings.pixiv_oauth_hash_secret or "").strip() or None,
    )

    transport = getattr(request.app.state, "httpx_transport", None)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async with Session() as session:
        row = await session.get(PixivToken, token_id)
        if row is None:
            raise ApiError(code=ErrorCode.NOT_FOUND, message="Token not found", status_code=404)

        try:
            refresh_token = encryptor.decrypt_text(row.refresh_token_enc)
        except Exception as exc:
            raise ApiError(code=ErrorCode.INTERNAL_ERROR, message="Invalid stored token", status_code=500) from exc

        now = iso_utc_ms()

        try:
            runtime = await load_runtime_config(engine)
            oauth_url = config.base_url.rstrip("/") + OAUTH_TOKEN_PATH
            picked_proxy = await select_proxy_uri_for_url(
                engine,
                settings,
                runtime,
                url=oauth_url,
                token_id=int(token_id),
            )
            proxy_uri = picked_proxy.uri if picked_proxy is not None else None

            token = await refresh_access_token(
                refresh_token=refresh_token,
                config=config,
                transport=transport,
                proxy=proxy_uri,
            )
        except PixivOauthError as exc:
            new_error_count = int(row.error_count or 0) + 1
            backoff_s = refresh_backoff_seconds(attempt=new_error_count, status_code=exc.status_code)
            backoff_until = (
                iso_utc_ms(datetime.now(timezone.utc) + timedelta(seconds=backoff_s)) if backoff_s > 0 else None
            )

            row.error_count = new_error_count
            row.backoff_until = backoff_until
            row.last_fail_at = now
            row.last_error_code = ErrorCode.TOKEN_REFRESH_FAILED.value
            row.last_error_msg = str(exc)[:500]
            row.updated_at = now

            await session.commit()

            raise ApiError(
                code=ErrorCode.TOKEN_REFRESH_FAILED,
                message="Token refresh failed",
                status_code=502,
                details={"upstream_status": exc.status_code or 0, "backoff_until": backoff_until or ""},
            ) from exc
        except Exception as exc:
            new_error_count = int(row.error_count or 0) + 1
            backoff_s = refresh_backoff_seconds(attempt=new_error_count, status_code=None)
            backoff_until = (
                iso_utc_ms(datetime.now(timezone.utc) + timedelta(seconds=backoff_s)) if backoff_s > 0 else None
            )

            row.error_count = new_error_count
            row.backoff_until = backoff_until
            row.last_fail_at = now
            row.last_error_code = ErrorCode.TOKEN_REFRESH_FAILED.value
            row.last_error_msg = "exception"
            row.updated_at = now

            await session.commit()

            raise ApiError(
                code=ErrorCode.TOKEN_REFRESH_FAILED,
                message="Token refresh failed",
                status_code=502,
                details={"backoff_until": backoff_until or ""},
            ) from exc

        row.error_count = 0
        row.backoff_until = None
        row.last_ok_at = now
        row.last_fail_at = None
        row.last_error_code = None
        row.last_error_msg = None
        row.updated_at = now

        rotated = token.refresh_token
        if rotated:
            row.refresh_token_enc = encryptor.encrypt_text(rotated)
            row.refresh_token_masked = mask_secret(rotated)

        await session.commit()

    proxy_details: dict[str, Any] | None = None
    if picked_proxy is not None:
        proxy_details = {"endpoint_id": str(picked_proxy.endpoint_id), "pool_id": str(picked_proxy.pool_id)}

    return {
        "ok": True,
        "expires_in": int(token.expires_in),
        "user_id": token.user_id,
        "proxy": proxy_details,
        "request_id": rid,
    }


@router.post("/tokens/{token_id}/reset-failures")
async def reset_failures(
    token_id: int,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    if token_id <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid token id", status_code=400)

    rid = get_or_create_request_id(request)

    now = iso_utc_ms()

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async with Session() as session:
        row = await session.get(PixivToken, token_id)
        if row is None:
            raise ApiError(code=ErrorCode.NOT_FOUND, message="Token not found", status_code=404)

        row.error_count = 0
        row.backoff_until = None
        row.last_fail_at = None
        row.last_error_code = None
        row.last_error_msg = None
        row.updated_at = now

        await session.commit()

    return {"ok": True, "token_id": str(token_id), "request_id": rid}
