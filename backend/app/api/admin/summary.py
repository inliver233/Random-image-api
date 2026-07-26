from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request

from app.api.admin.deps import get_admin_claims
from app.core.admin_json import admin_ok
from app.core.request_id import get_or_create_request_id
from app.core.runtime_settings import worker_last_seen_from_value_json
from app.db.session import with_sqlite_busy_retry

router = APIRouter()


@router.get(
    "/summary",
    summary="Get admin dashboard counts",
    description=(
        "Aggregate control-plane counts: catalog hydration gaps, tokens/proxies/pools/"
        "bindings, `jobs` status histogram, and worker last-seen from RuntimeSetting. "
        "Job counts reflect the jobs table (payload store) — not external redis/nats queues."
    ),
)
async def get_summary(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)

    engine = request.app.state.engine

    async def _op() -> dict[str, Any]:
        async with engine.connect() as conn:
            images_total = int((await conn.exec_driver_sql("SELECT COUNT(*) FROM images;")).scalar_one())
            images_enabled = int(
                (await conn.exec_driver_sql("SELECT COUNT(*) FROM images WHERE status=1;")).scalar_one()
            )

            missing_tags = int(
                (
                    await conn.exec_driver_sql(
                        """
SELECT COUNT(*)
FROM images
WHERE status=1
  AND id NOT IN (SELECT DISTINCT image_id FROM image_tags);
""".strip()
                    )
                ).scalar_one()
            )
            missing_geometry = int(
                (
                    await conn.exec_driver_sql(
                        "SELECT COUNT(*) FROM images WHERE status=1 AND (width IS NULL OR height IS NULL);"
                    )
                ).scalar_one()
            )
            missing_r18 = int(
                (await conn.exec_driver_sql("SELECT COUNT(*) FROM images WHERE status=1 AND x_restrict IS NULL;")).scalar_one()
            )
            missing_ai = int(
                (await conn.exec_driver_sql("SELECT COUNT(*) FROM images WHERE status=1 AND ai_type IS NULL;")).scalar_one()
            )
            missing_illust_type = int(
                (await conn.exec_driver_sql("SELECT COUNT(*) FROM images WHERE status=1 AND illust_type IS NULL;")).scalar_one()
            )
            missing_user = int(
                (await conn.exec_driver_sql("SELECT COUNT(*) FROM images WHERE status=1 AND user_id IS NULL;")).scalar_one()
            )
            missing_title = int(
                (
                    await conn.exec_driver_sql(
                        "SELECT COUNT(*) FROM images WHERE status=1 AND (title IS NULL OR TRIM(title)='');"
                    )
                ).scalar_one()
            )
            missing_created_at = int(
                (
                    await conn.exec_driver_sql(
                        "SELECT COUNT(*) FROM images WHERE status=1 AND (created_at_pixiv IS NULL OR TRIM(created_at_pixiv)='');"
                    )
                ).scalar_one()
            )
            missing_popularity = int(
                (
                    await conn.exec_driver_sql(
                        "SELECT COUNT(*) FROM images "
                        "WHERE status=1 AND (bookmark_count IS NULL OR view_count IS NULL OR comment_count IS NULL);"
                    )
                ).scalar_one()
            )

            tokens_total = int((await conn.exec_driver_sql("SELECT COUNT(*) FROM pixiv_tokens;")).scalar_one())
            tokens_enabled = int(
                (await conn.exec_driver_sql("SELECT COUNT(*) FROM pixiv_tokens WHERE enabled=1;")).scalar_one()
            )

            proxies_total = int((await conn.exec_driver_sql("SELECT COUNT(*) FROM proxy_endpoints;")).scalar_one())
            proxies_enabled = int(
                (await conn.exec_driver_sql("SELECT COUNT(*) FROM proxy_endpoints WHERE enabled=1;")).scalar_one()
            )

            pools_total = int((await conn.exec_driver_sql("SELECT COUNT(*) FROM proxy_pools;")).scalar_one())
            pools_enabled = int(
                (await conn.exec_driver_sql("SELECT COUNT(*) FROM proxy_pools WHERE enabled=1;")).scalar_one()
            )

            bindings_total = int((await conn.exec_driver_sql("SELECT COUNT(*) FROM token_proxy_bindings;")).scalar_one())

            jobs_counts: dict[str, int] = {}
            rows = (await conn.exec_driver_sql("SELECT status, COUNT(*) AS c FROM jobs GROUP BY status;")).fetchall()
            for status, count in rows:
                jobs_counts[str(status)] = int(count)

            worker_last_seen_json = (
                await conn.execute(
                    sa.text("SELECT value_json FROM runtime_settings WHERE key = :key"),
                    {"key": "worker.last_seen_at"},
                )
            ).scalar_one_or_none()
            worker_last_seen_at = worker_last_seen_from_value_json(
                str(worker_last_seen_json) if worker_last_seen_json is not None else None
            )

        # D3 cold-start: default r18_strict=1 excludes x_restrict NULL → NO_MATCH.
        r18_unknown_ratio = (
            float(missing_r18) / float(images_enabled) if images_enabled > 0 else 0.0
        )
        cold_start_r18_risk = bool(images_enabled > 0 and r18_unknown_ratio >= 0.5)

        return {
            "images": {"total": images_total, "enabled": images_enabled},
            "hydration": {
                "enabled_images_total": images_enabled,
                "missing": {
                    "tags": missing_tags,
                    "geometry": missing_geometry,
                    "r18": missing_r18,
                    "ai": missing_ai,
                    "illust_type": missing_illust_type,
                    "user": missing_user,
                    "title": missing_title,
                    "created_at": missing_created_at,
                    "popularity": missing_popularity,
                },
                "cold_start_r18_risk": cold_start_r18_risk,
                "r18_unknown_ratio": round(r18_unknown_ratio, 4),
                "cold_start_hint": (
                    "≥50% enabled images have unknown x_restrict. Default /random "
                    "(r18_strict=1) will often NO_MATCH. Set random.defaults.default_r18_strict=false "
                    "or run hydrate; clients may use r18_strict=0 / r18=2."
                    if cold_start_r18_risk
                    else None
                ),
            },
            "tokens": {"total": tokens_total, "enabled": tokens_enabled},
            "proxies": {"endpoints_total": proxies_total, "endpoints_enabled": proxies_enabled},
            "proxy_pools": {"total": pools_total, "enabled": pools_enabled},
            "bindings": {"total": bindings_total},
            "jobs": {"counts": jobs_counts},
            "worker": {"last_seen_at": worker_last_seen_at},
        }

    counts = await with_sqlite_busy_retry(_op)
    return admin_ok(request, payload={"counts": counts}, request_id=rid)
