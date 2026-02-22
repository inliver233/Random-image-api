from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.pximg_reverse_proxy import DEFAULT_PXIMG_MIRROR_HOST, normalize_pximg_mirror_host
from app.core.logging import get_logger
from app.core.time import iso_utc_ms
from app.db.models.runtime_settings import RuntimeSetting
from app.db.session import create_sessionmaker, with_sqlite_busy_retry

log = get_logger(__name__)


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "1", "yes", "y", "on"}:
            return True
        if v in {"false", "0", "no", "n", "off"}:
            return False
    return None


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        v = item.strip()
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    proxy_enabled: bool
    proxy_fail_closed: bool
    proxy_route_mode: str
    proxy_allowlist_domains: list[str]
    proxy_route_pools: dict[str, int]
    proxy_default_pool_id: int | None
    image_proxy_use_pixiv_cat: bool
    image_proxy_pximg_mirror_host: str
    random_defaults: dict[str, Any]
    hide_origin_url_in_public_json: bool
    rate_limit: dict[str, Any]

    @classmethod
    def defaults(cls) -> "RuntimeConfig":
        return cls(
            proxy_enabled=False,
            proxy_fail_closed=True,
            proxy_route_mode="pixiv_only",
            proxy_allowlist_domains=[],
            proxy_route_pools={},
            proxy_default_pool_id=None,
            image_proxy_use_pixiv_cat=False,
            image_proxy_pximg_mirror_host=str(DEFAULT_PXIMG_MIRROR_HOST),
            random_defaults={},
            hide_origin_url_in_public_json=True,
            rate_limit={},
        )


async def fetch_runtime_settings(engine: AsyncEngine) -> dict[str, Any]:
    Session = create_sessionmaker(engine)

    async def _op() -> dict[str, Any]:
        async with Session() as session:
            result = await session.execute(select(RuntimeSetting.key, RuntimeSetting.value_json))
            values: dict[str, Any] = {}
            for key, value_json in result.all():
                try:
                    values[str(key)] = json.loads(value_json)
                except Exception:
                    log.warning("runtime_settings_invalid_json key=%s", key)
            return values

    return await with_sqlite_busy_retry(_op)


def runtime_config_from_values(values: dict[str, Any]) -> RuntimeConfig:
    defaults = RuntimeConfig.defaults()

    proxy_enabled = _as_bool(values.get("proxy.enabled"))
    proxy_fail_closed = _as_bool(values.get("proxy.fail_closed"))

    proxy_route_mode_raw = values.get("proxy.route_mode")
    proxy_route_mode = defaults.proxy_route_mode
    if isinstance(proxy_route_mode_raw, str):
        candidate = proxy_route_mode_raw.strip().lower()
        if candidate in {"pixiv_only", "all", "allowlist", "off"}:
            proxy_route_mode = candidate

    proxy_allowlist_domains = _as_str_list(values.get("proxy.allowlist_domains")) or defaults.proxy_allowlist_domains

    proxy_route_pools_raw = values.get("proxy.route_pools")
    proxy_route_pools: dict[str, int] = {}
    if isinstance(proxy_route_pools_raw, dict):
        for k, v in proxy_route_pools_raw.items():
            key = str(k or "").strip().lower().strip(".")
            if not key or len(key) > 200:
                continue
            try:
                pool_id = int(v)
            except Exception:
                continue
            if pool_id <= 0:
                continue
            proxy_route_pools[key] = pool_id

    proxy_default_pool_id: int | None = None
    default_pool_raw = values.get("proxy.default_pool_id")
    if default_pool_raw is not None:
        try:
            candidate = int(default_pool_raw)
        except Exception:
            candidate = 0
        proxy_default_pool_id = candidate if candidate > 0 else None

    image_proxy_use_pixiv_cat_raw = values.get("image_proxy.use_pixiv_cat")
    image_proxy_use_pixiv_cat = _as_bool(image_proxy_use_pixiv_cat_raw)

    mirror_host_raw = values.get("image_proxy.pximg_mirror_host")
    mirror_host = normalize_pximg_mirror_host(mirror_host_raw)

    random_defaults_raw = values.get("random.defaults")
    random_defaults = defaults.random_defaults
    if isinstance(random_defaults_raw, dict):
        random_defaults = dict(random_defaults_raw)

    hide_origin_url_raw = values.get("security.hide_origin_url_in_public_json")
    hide_origin_url = _as_bool(hide_origin_url_raw)

    rate_limit: dict[str, Any] = {}
    for key, value in values.items():
        if key.startswith("rate_limit."):
            rate_limit[key.removeprefix("rate_limit.")] = value

    return RuntimeConfig(
        proxy_enabled=proxy_enabled if proxy_enabled is not None else defaults.proxy_enabled,
        proxy_fail_closed=proxy_fail_closed if proxy_fail_closed is not None else defaults.proxy_fail_closed,
        proxy_route_mode=proxy_route_mode,
        proxy_allowlist_domains=proxy_allowlist_domains,
        proxy_route_pools=proxy_route_pools,
        proxy_default_pool_id=proxy_default_pool_id,
        image_proxy_use_pixiv_cat=image_proxy_use_pixiv_cat
        if image_proxy_use_pixiv_cat is not None
        else defaults.image_proxy_use_pixiv_cat,
        image_proxy_pximg_mirror_host=str(mirror_host or defaults.image_proxy_pximg_mirror_host),
        random_defaults=random_defaults,
        hide_origin_url_in_public_json=hide_origin_url
        if hide_origin_url is not None
        else defaults.hide_origin_url_in_public_json,
        rate_limit=rate_limit,
    )


async def load_runtime_config(engine: AsyncEngine) -> RuntimeConfig:
    values = await fetch_runtime_settings(engine)
    return runtime_config_from_values(values)


async def set_runtime_setting(
    engine: AsyncEngine,
    *,
    key: str,
    value: Any,
    description: str | None = None,
    updated_by: str | None = None,
) -> None:
    key = (key or "").strip()
    if not key:
        raise ValueError("key is required")

    value_json = json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    now = iso_utc_ms()

    Session = create_sessionmaker(engine)

    async def _op() -> None:
        async with Session() as session:
            stmt = sqlite_insert(RuntimeSetting).values(
                key=key,
                value_json=value_json,
                description=description,
                updated_at=now,
                updated_by=updated_by,
            )
            set_values: dict[str, Any] = {
                "value_json": value_json,
                "updated_at": now,
                "updated_by": updated_by,
            }
            if description is not None:
                set_values["description"] = description
            stmt = stmt.on_conflict_do_update(
                index_elements=[RuntimeSetting.key],
                set_=set_values,
            )
            await session.execute(stmt)
            await session.commit()

    await with_sqlite_busy_retry(_op)
