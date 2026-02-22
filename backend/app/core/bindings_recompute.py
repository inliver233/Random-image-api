from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, ErrorCode
from app.db.models.pixiv_tokens import PixivToken
from app.db.models.proxy_endpoints import ProxyEndpoint
from app.db.models.proxy_pool_endpoints import ProxyPoolEndpoint
from app.db.models.token_proxy_bindings import TokenProxyBinding


def _fnv1a64(text: str) -> int:
    h = 14695981039346656037
    prime = 1099511628211
    for b in text.encode("utf-8"):
        h ^= b
        h = (h * prime) & 0xFFFFFFFFFFFFFFFF
    return h


def _rendezvous_proxy_order(*, token_id: int, proxy_ids: list[int], salt: str) -> list[int]:
    scored = [(_fnv1a64(f"{token_id}|{pid}|{salt}"), pid) for pid in proxy_ids]
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [pid for _, pid in scored]


def _compute_primary_assignments(
    *,
    token_ids: list[int],
    proxy_ids: list[int],
    capacity_by_proxy_id: dict[int, int],
    salt: str,
) -> dict[int, int]:
    remaining = {pid: int(capacity_by_proxy_id.get(int(pid), 0)) for pid in proxy_ids}
    out: dict[int, int] = {}
    for token_id in token_ids:
        for pid in _rendezvous_proxy_order(token_id=token_id, proxy_ids=proxy_ids, salt=salt):
            if remaining.get(pid, 0) > 0:
                out[token_id] = pid
                remaining[pid] -= 1
                break
    return out


def _compute_primary_assignments_soft(
    *,
    token_ids: list[int],
    proxy_ids: list[int],
    capacity_by_proxy_id: dict[int, int],
    salt: str,
) -> tuple[dict[int, int], int]:
    out = _compute_primary_assignments(
        token_ids=token_ids,
        proxy_ids=proxy_ids,
        capacity_by_proxy_id=capacity_by_proxy_id,
        salt=salt,
    )

    over_capacity = 0
    for token_id in token_ids:
        if token_id in out:
            continue
        order = _rendezvous_proxy_order(token_id=token_id, proxy_ids=proxy_ids, salt=salt)
        if not order:
            continue
        out[token_id] = int(order[0])
        over_capacity += 1

    return out, int(over_capacity)


async def recompute_token_proxy_bindings(
    session: AsyncSession,
    *,
    pool_id: int,
    now: str,
    max_tokens_per_proxy: int,
    strict: bool,
) -> dict[str, Any]:
    if int(pool_id) <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid pool_id", status_code=400)
    if int(max_tokens_per_proxy) <= 0 or int(max_tokens_per_proxy) > 1000:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid max_tokens_per_proxy", status_code=400)

    proxy_rows = (
        (
            await session.execute(
                sa.select(ProxyPoolEndpoint.endpoint_id, ProxyPoolEndpoint.weight)
                .join(ProxyEndpoint, ProxyEndpoint.id == ProxyPoolEndpoint.endpoint_id)
                .where(ProxyPoolEndpoint.pool_id == int(pool_id))
                .where(ProxyPoolEndpoint.enabled == 1)
                .where(ProxyEndpoint.enabled == 1)
                .order_by(ProxyPoolEndpoint.endpoint_id.asc())
            )
        )
        .all()
    )

    proxies: list[tuple[int, int]] = []
    for endpoint_id, weight in proxy_rows:
        try:
            eid = int(endpoint_id)
        except Exception:
            continue
        try:
            w = int(weight or 0)
        except Exception:
            w = 0
        if eid <= 0:
            continue
        proxies.append((eid, w))

    capacity_by_proxy_id = {pid: int(max_tokens_per_proxy) * max(0, int(w)) for pid, w in proxies}
    proxy_ids_norm = [pid for pid, _w in proxies if capacity_by_proxy_id.get(pid, 0) > 0]
    weight_sum = sum(max(0, int(w)) for pid, w in proxies if int(pid) in set(proxy_ids_norm))

    if not proxy_ids_norm:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="No enabled proxies in pool", status_code=400)

    token_ids = (
        (
            await session.execute(
                sa.select(PixivToken.id)
                .where(PixivToken.enabled == 1)
                .where(PixivToken.weight > 0)
                .order_by(PixivToken.id.asc())
            )
        )
        .scalars()
        .all()
    )
    if not token_ids:
        return {"recomputed": 0}

    capacity = sum(int(capacity_by_proxy_id.get(int(pid), 0)) for pid in proxy_ids_norm)
    if strict and len(token_ids) > capacity:
        raise ApiError(
            code=ErrorCode.BAD_REQUEST,
            message="代理容量不足（请增加节点或调高单代理最多绑定令牌数）",
            status_code=400,
            details={
                "token_count": len(token_ids),
                "proxy_count": len(proxy_ids_norm),
                "max_tokens_per_proxy": int(max_tokens_per_proxy),
                "weight_sum": int(weight_sum),
                "capacity": int(capacity),
            },
        )

    salt = f"pool:{pool_id}"
    over_capacity_assigned = 0
    if strict:
        assignments = _compute_primary_assignments(
            token_ids=token_ids,
            proxy_ids=proxy_ids_norm,
            capacity_by_proxy_id=capacity_by_proxy_id,
            salt=salt,
        )
    else:
        assignments, over_capacity_assigned = _compute_primary_assignments_soft(
            token_ids=token_ids,
            proxy_ids=proxy_ids_norm,
            capacity_by_proxy_id=capacity_by_proxy_id,
            salt=salt,
        )

    for token_id in token_ids:
        primary_proxy_id = assignments.get(int(token_id))
        if primary_proxy_id is None:
            raise ApiError(code=ErrorCode.INTERNAL_ERROR, message="Binding recompute failed", status_code=500)

        stmt = sqlite_insert(TokenProxyBinding).values(
            token_id=int(token_id),
            pool_id=int(pool_id),
            primary_proxy_id=int(primary_proxy_id),
            override_proxy_id=None,
            override_expires_at=None,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[TokenProxyBinding.token_id, TokenProxyBinding.pool_id],
            set_={"primary_proxy_id": int(primary_proxy_id), "updated_at": now},
        )
        await session.execute(stmt)

    resp: dict[str, Any] = {"recomputed": len(token_ids)}
    if not strict:
        resp.update(
            {
                "strict": False,
                "over_capacity_assigned": int(over_capacity_assigned),
                "capacity": int(capacity),
                "token_count": len(token_ids),
                "proxy_count": len(proxy_ids_norm),
                "max_tokens_per_proxy": int(max_tokens_per_proxy),
                "weight_sum": int(weight_sum),
            }
        )
    return resp

