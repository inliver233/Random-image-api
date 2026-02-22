from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import IntegrityError

from app.api.admin.deps import get_admin_claims
from app.core.errors import ApiError, ErrorCode
from app.core.request_id import get_or_create_request_id
from app.db.models.proxy_endpoints import ProxyEndpoint
from app.db.models.proxy_pool_endpoints import ProxyPoolEndpoint
from app.db.models.proxy_pools import ProxyPool
from app.db.session import create_sessionmaker

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


async def _load_create_json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception as exc:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400) from exc

    if not isinstance(data, dict):
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400)

    name = str(data.get("name") or "").strip()
    if not name:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Missing name", status_code=400)
    if len(name) > 100:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported name", status_code=400)

    desc_raw = data.get("description")
    description = str(desc_raw).strip() if desc_raw is not None else None
    description = description if description else None

    enabled = _parse_bool(data.get("enabled"), default=True)
    return {"name": name, "description": description, "enabled": enabled}


async def _load_update_json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception as exc:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400) from exc

    if not isinstance(data, dict):
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400)

    out: dict[str, Any] = {}

    if "name" in data:
        name = str(data.get("name") or "").strip()
        if not name:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported name", status_code=400)
        if len(name) > 100:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported name", status_code=400)
        out["name"] = name

    if "description" in data:
        desc_raw = data.get("description")
        description = str(desc_raw).strip() if desc_raw is not None else None
        out["description"] = description if description else None

    if "enabled" in data:
        out["enabled"] = _parse_bool(data.get("enabled"), default=True)

    if not out:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Missing fields", status_code=400)

    return out


async def _load_set_endpoints_json(request: Request) -> list[dict[str, Any]]:
    try:
        data = await request.json()
    except Exception as exc:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400) from exc

    if not isinstance(data, dict):
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400)

    raw_items = data.get("items")
    if raw_items is None:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Missing items", status_code=400)
    if not isinstance(raw_items, list):
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid items", status_code=400)

    out: list[dict[str, Any]] = []
    seen: set[int] = set()

    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid items", status_code=400)
        try:
            endpoint_id = int(raw.get("endpoint_id"))
        except Exception as exc:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid endpoint_id", status_code=400) from exc
        if endpoint_id <= 0 or endpoint_id in seen:
            continue
        seen.add(endpoint_id)

        enabled = _parse_bool(raw.get("enabled"), default=True)

        weight_raw = raw.get("weight", 1)
        try:
            weight = int(weight_raw)
        except Exception as exc:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid weight", status_code=400) from exc
        if weight < 0 or weight > 1000:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid weight", status_code=400)

        out.append({"endpoint_id": endpoint_id, "enabled": enabled, "weight": weight})

    return out


@router.get("/proxy-pools")
async def list_proxy_pools(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)
    async with Session() as session:
        pools = (
            (await session.execute(sa.select(ProxyPool).order_by(ProxyPool.id.desc())))
            .scalars()
            .all()
        )

    items = [
        {
            "id": str(p.id),
            "name": p.name,
            "description": p.description,
            "enabled": bool(p.enabled),
        }
        for p in pools
    ]

    return {"ok": True, "items": items, "request_id": rid}


@router.post("/proxy-pools")
async def create_proxy_pool(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)
    body = await _load_create_json(request)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async with Session() as session:
        row = ProxyPool(
            name=str(body["name"]),
            description=body["description"],
            enabled=1 if bool(body["enabled"]) else 0,
        )
        session.add(row)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Proxy pool name exists", status_code=400) from exc
        await session.refresh(row)

    return {"ok": True, "pool_id": str(row.id), "request_id": rid}


@router.put("/proxy-pools/{pool_id}")
async def update_proxy_pool(
    pool_id: int,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    if pool_id <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid pool id", status_code=400)

    rid = get_or_create_request_id(request)
    body = await _load_update_json(request)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)
    async with Session() as session:
        row = await session.get(ProxyPool, pool_id)
        if row is None:
            raise ApiError(code=ErrorCode.NOT_FOUND, message="Proxy pool not found", status_code=404)

        if "name" in body:
            row.name = str(body["name"])
        if "description" in body:
            row.description = body["description"]
        if "enabled" in body:
            row.enabled = 1 if bool(body["enabled"]) else 0

        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Proxy pool name exists", status_code=400) from exc

    return {"ok": True, "pool_id": str(pool_id), "request_id": rid}


@router.post("/proxy-pools/{pool_id}/endpoints")
async def set_proxy_pool_endpoints(
    pool_id: int,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    if pool_id <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid pool id", status_code=400)

    rid = get_or_create_request_id(request)
    items = await _load_set_endpoints_json(request)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    created = 0
    updated = 0
    removed = 0

    async with Session() as session:
        pool = await session.get(ProxyPool, pool_id)
        if pool is None:
            raise ApiError(code=ErrorCode.NOT_FOUND, message="Proxy pool not found", status_code=404)

        endpoint_ids = [int(x["endpoint_id"]) for x in items]
        if endpoint_ids:
            existing = (
                (
                    await session.execute(
                        sa.select(ProxyEndpoint.id).where(ProxyEndpoint.id.in_(endpoint_ids))
                    )
                )
                .scalars()
                .all()
            )
            existing_set = {int(x) for x in existing}
            missing = [eid for eid in endpoint_ids if eid not in existing_set]
            if missing:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unknown endpoint_id", status_code=400)

        current_rows = (
            (
                await session.execute(
                    sa.select(ProxyPoolEndpoint).where(ProxyPoolEndpoint.pool_id == pool_id)
                )
            )
            .scalars()
            .all()
        )
        current_by_eid = {int(r.endpoint_id): r for r in current_rows}

        keep: set[int] = set()
        for item in items:
            eid = int(item["endpoint_id"])
            keep.add(eid)
            row = current_by_eid.get(eid)
            if row is None:
                session.add(
                    ProxyPoolEndpoint(
                        pool_id=pool_id,
                        endpoint_id=eid,
                        enabled=1 if bool(item["enabled"]) else 0,
                        weight=int(item["weight"]),
                    )
                )
                created += 1
            else:
                row.enabled = 1 if bool(item["enabled"]) else 0
                row.weight = int(item["weight"])
                updated += 1

        for eid, row in current_by_eid.items():
            if eid in keep:
                continue
            await session.delete(row)
            removed += 1

        await session.commit()

    return {
        "ok": True,
        "pool_id": str(pool_id),
        "created": created,
        "updated": updated,
        "removed": removed,
        "request_id": rid,
    }
