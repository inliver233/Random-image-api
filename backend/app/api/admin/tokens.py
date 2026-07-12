from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request

from app.api.admin.deps import get_admin_claims
from app.core.admin_cursor_query import parse_admin_int_cursor, slice_id_cursor_page
from app.core.admin_json import admin_cursor_list, admin_ok
from app.core.admin_request import (
    load_json_object,
    parse_bool,
    parse_float_in_range,
    parse_optional_str,
    parse_required_bool,
    parse_required_str,
    require_positive_id,
)
from app.core.crypto import FieldEncryptor, mask_secret
from app.core.errors import ApiError, ErrorCode
from app.core.metrics import observe_pixiv_api_egress
from app.core.proxy_selector import iter_pixiv_api_egress
from app.core.request_id import get_or_create_request_id
from app.core.runtime_config_cache import get_cached_runtime_config
from app.core.time import iso_utc_ms
from app.db.models.pixiv_tokens import PixivToken
from app.db.models.token_proxy_bindings import TokenProxyBinding
from app.db.session import resolve_sessionmaker, with_sqlite_busy_retry
from app.pixiv.oauth import OAUTH_TOKEN_PATH, PixivOauthConfig, PixivOauthError, refresh_access_token
from app.pixiv.refresh_backoff import refresh_backoff_seconds

router = APIRouter()




async def _load_create_token_json(request: Request) -> dict[str, Any]:
    data = await load_json_object(request)

    refresh_token = parse_required_str(data.get("refresh_token"), field="refresh_token", max_len=2048)
    label = parse_optional_str(data.get("label"), max_len=200, field="label")

    enabled = parse_bool(data.get("enabled"), default=True)

    weight = parse_float_in_range(
        data.get("weight", 1.0),
        field="weight",
        min_value=0.0,
        max_value=100.0,
    )

    return {
        "label": label,
        "enabled": enabled,
        "weight": weight,
        "refresh_token": refresh_token,
    }


async def _load_update_token_json(request: Request) -> dict[str, Any]:
    data = await load_json_object(request)
    if not data:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Missing fields", status_code=400)

    out: dict[str, Any] = {}

    if "label" in data:
        out["label"] = parse_optional_str(data.get("label"), max_len=200, field="label")

    if "enabled" in data:
        out["enabled"] = parse_required_bool(
            data.get("enabled"),
            field="enabled",
            invalid_message="Unsupported enabled",
        )

    if "weight" in data:
        out["weight"] = parse_float_in_range(
            data.get("weight"),
            field="weight",
            min_value=0.0,
            max_value=100.0,
        )

    if not out:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Missing fields", status_code=400)

    return out


@router.get(
    "/tokens",
    summary="List Pixiv tokens",
    description=(
        "Cursor-paginated PixivToken control-plane rows (label/weight/error backoff). "
        "Never returns encrypted refresh tokens — only masked. Used with residential bindings "
        "and CF-first OAuth test-refresh (`POST .../test-refresh`)."
    ),
)
async def list_tokens(
    request: Request,
    limit: int = 200,
    cursor: str | None = None,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    parsed = parse_admin_int_cursor(limit=limit, cursor=cursor, limit_max=500)
    limit = parsed.limit
    cursor_i = parsed.cursor_i
    rid = get_or_create_request_id(request)

    engine = request.app.state.engine
    Session = resolve_sessionmaker(request, engine)

    stmt = sa.select(PixivToken).order_by(PixivToken.id.desc()).limit(int(limit) + 1)
    if cursor_i is not None:
        stmt = stmt.where(PixivToken.id < int(cursor_i))

    async with Session() as session:
        tokens = (await session.execute(stmt)).scalars().all()

    page, next_cursor = slice_id_cursor_page(list(tokens), int(limit))

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
        for t in page
    ]

    return admin_cursor_list(request, items=items, next_cursor=next_cursor, request_id=rid)


@router.post(
    "/tokens",
    summary="Create Pixiv token",
    description=(
        "Store a new Pixiv refresh token encrypted via FieldEncryptor (`FIELD_ENCRYPTION_KEY`). "
        "Response is `token_id` only — never echoes secrets. Pair with bindings recompute for "
        "residential egress; OAuth probe is `POST .../test-refresh`."
    ),
)
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
    Session = resolve_sessionmaker(request, engine)
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

    return admin_ok(request, payload={"token_id": str(row.id)}, request_id=rid)


@router.put(
    "/tokens/{token_id}",
    summary="Update Pixiv token",
    description=(
        "Partial update of PixivToken label/enabled/weight (not the refresh secret). "
        "Enabled=false removes the token from hydrate/OAuth selection until re-enabled."
    ),
)
async def update_token(
    token_id: int,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    token_id = require_positive_id(token_id, invalid_message="Invalid token id")

    rid = get_or_create_request_id(request)
    body = await _load_update_token_json(request)

    now = iso_utc_ms()

    engine = request.app.state.engine
    Session = resolve_sessionmaker(request, engine)

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

    return admin_ok(request, payload={"token_id": str(token_id)}, request_id=rid)


@router.delete(
    "/tokens/{token_id}",
    summary="Delete Pixiv token",
    description=(
        "Delete PixivToken and cascade-clear TokenProxyBinding rows for that token. "
        "Does not enqueue jobs; pure catalog/control-plane write."
    ),
)
async def delete_token(
    token_id: int,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    token_id = require_positive_id(token_id, invalid_message="Invalid token id")

    rid = get_or_create_request_id(request)

    engine = request.app.state.engine
    Session = resolve_sessionmaker(request, engine)

    async def _op() -> dict[str, Any]:
        async with Session() as session:
            row = await session.get(PixivToken, token_id)
            if row is None:
                raise ApiError(code=ErrorCode.NOT_FOUND, message="Token not found", status_code=404)

            await session.execute(sa.delete(TokenProxyBinding).where(TokenProxyBinding.token_id == int(token_id)))
            await session.delete(row)
            await session.commit()

        return admin_ok(request, payload={"token_id": str(token_id)}, request_id=rid)

    return await with_sqlite_busy_retry(_op)


@router.post(
    "/tokens/{token_id}/test-refresh",
    summary="Test token OAuth refresh",
    description=(
        "Live Pixiv OAuth refresh probe for one stored token. Egress uses "
        "`iter_pixiv_api_egress` (CF API proxy first when ready, then residential). "
        "Does not enqueue jobs; returns success/failure, `via_cf` (bool), and optional "
        "residential `proxy` ids when the winning attempt was residential. "
        "Never returns refresh tokens or CF secrets. Parity with hydrate egress plan "
        "(contracts/cf-api-proxy.md)."
    ),
)
async def test_refresh_token(
    token_id: int,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    token_id = require_positive_id(token_id, invalid_message="Invalid token id")

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
    Session = resolve_sessionmaker(request, engine)

    async with Session() as session:
        row = await session.get(PixivToken, token_id)
        if row is None:
            raise ApiError(code=ErrorCode.NOT_FOUND, message="Token not found", status_code=404)

        try:
            refresh_token = encryptor.decrypt_text(row.refresh_token_enc)
        except Exception as exc:
            raise ApiError(code=ErrorCode.INTERNAL_ERROR, message="Invalid stored token", status_code=500) from exc

        now = iso_utc_ms()
        picked_proxy = None
        token = None
        winning_via_cf = False

        try:
            runtime = await get_cached_runtime_config(engine)
            oauth_url = config.base_url.rstrip("/") + OAUTH_TOKEN_PATH
            last_exc: BaseException | None = None
            # CF multi-base first, then residential (same plan as hydrate).
            async for attempt in iter_pixiv_api_egress(
                engine,
                settings,
                runtime,
                url=oauth_url,
                token_id=int(token_id),
                residential_failover_attempts=0,
            ):
                via_label = "cf" if attempt.via_cf else "residential"
                try:
                    if attempt.via_cf:
                        token = await refresh_access_token(
                            refresh_token=refresh_token,
                            config=config,
                            transport=transport,
                            proxy=None,
                            request_url=attempt.request_url,
                            extra_headers=attempt.extra_headers,
                        )
                    else:
                        token = await refresh_access_token(
                            refresh_token=refresh_token,
                            config=config,
                            transport=transport,
                            proxy=attempt.proxy_uri,
                        )
                except PixivOauthError as exc:
                    observe_pixiv_api_egress(via=via_label, result="error")
                    if exc.status_code is None or int(exc.status_code) >= 500:
                        last_exc = exc
                        # Soft fail-open: no residential + not CF → stop; else try next.
                        if attempt.residential is None and not attempt.via_cf:
                            break
                        continue
                    raise
                except Exception as exc:
                    observe_pixiv_api_egress(via=via_label, result="error")
                    last_exc = exc
                    if attempt.residential is None and not attempt.via_cf:
                        break
                    continue
                else:
                    observe_pixiv_api_egress(via=via_label, result="ok")
                    picked_proxy = attempt.residential
                    winning_via_cf = bool(attempt.via_cf)
                    break

            if token is None:
                if isinstance(last_exc, PixivOauthError):
                    raise last_exc
                if last_exc is not None:
                    raise last_exc
                raise PixivOauthError("OAuth refresh failed", status_code=None)
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

    return admin_ok(
        request,
        payload={
            "expires_in": int(token.expires_in),
            "user_id": token.user_id,
            "via_cf": bool(winning_via_cf),
            "proxy": proxy_details,
        },
        request_id=rid,
    )


@router.post(
    "/tokens/{token_id}/reset-failures",
    summary="Reset token failure backoff",
    description=(
        "Clear error_count / backoff_until / last_error fields so the token is eligible again "
        "for hydrate and OAuth refresh selection. Does not rotate secrets or touch bindings."
    ),
)
async def reset_failures(
    token_id: int,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    token_id = require_positive_id(token_id, invalid_message="Invalid token id")

    rid = get_or_create_request_id(request)

    now = iso_utc_ms()

    engine = request.app.state.engine
    Session = resolve_sessionmaker(request, engine)

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

    return admin_ok(request, payload={"token_id": str(token_id)}, request_id=rid)
