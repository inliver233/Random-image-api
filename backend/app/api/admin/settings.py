from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.admin.deps import get_admin_claims
from app.core.errors import ApiError, ErrorCode
from app.core.request_id import get_or_create_request_id
from app.core.runtime_settings import (
    fetch_runtime_settings,
    runtime_config_from_values,
    set_runtime_setting,
)
from app.core.pximg_reverse_proxy import (
    DEFAULT_PXIMG_MIRROR_HOST,
    normalize_pximg_custom_mirror_host,
    normalize_pximg_mirror_host,
)

router = APIRouter()

_DEFAULT_SCORE_WEIGHTS: dict[str, float] = {
    "bookmark": 4.0,
    "view": 0.5,
    "comment": 2.0,
    "pixels": 1.0,
    "bookmark_rate": 3.0,
}

_DEFAULT_RECOMMENDATION: dict[str, Any] = {
    "pick_mode": "weighted",
    "temperature": 1.0,
    "score_weights": dict(_DEFAULT_SCORE_WEIGHTS),
    "multipliers": {
        "ai": 1.0,
        "non_ai": 1.0,
        "unknown_ai": 1.0,
        "illust": 1.0,
        "manga": 1.0,
        "ugoira": 1.0,
        "unknown_illust_type": 1.0,
    },
}

_DEFAULT_SETTINGS = {
    "random": {
        "default_attempts": 3,
        "default_r18_strict": True,
        "fail_cooldown_ms": 600_000,
        "strategy": "quality",
        "quality_samples": 5,
        "recommendation": dict(_DEFAULT_RECOMMENDATION),
    },
    "proxy": {"allowlist_domains": []},
}


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


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _normalize_recommendation(value: Any, *, strict: bool) -> dict[str, Any]:
    if value is None:
        return dict(_DEFAULT_RECOMMENDATION)
    if not isinstance(value, dict):
        if strict:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid random.recommendation", status_code=400)
        return dict(_DEFAULT_RECOMMENDATION)

    pick_mode_default = str(_DEFAULT_RECOMMENDATION["pick_mode"])
    pick_mode_raw = value.get("pick_mode")
    pick_mode = pick_mode_default
    if pick_mode_raw is not None:
        candidate = str(pick_mode_raw or "").strip().lower()
        if candidate not in {"best", "weighted"}:
            if strict:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid random.recommendation.pick_mode", status_code=400)
        else:
            pick_mode = candidate

    temperature_default = float(_DEFAULT_RECOMMENDATION["temperature"])
    temperature = temperature_default
    if "temperature" in value:
        v = _as_float(value.get("temperature"))
        if v is None:
            if strict:
                raise ApiError(
                    code=ErrorCode.BAD_REQUEST,
                    message="Invalid random.recommendation.temperature",
                    status_code=400,
                )
        else:
            temperature = float(max(0.05, min(float(v), 100.0)))

    score_weights_default = _DEFAULT_RECOMMENDATION["score_weights"]
    score_weights_raw = value.get("score_weights")
    if score_weights_raw is None:
        score_weights_obj: dict[str, Any] = {}
    elif not isinstance(score_weights_raw, dict):
        if strict:
            raise ApiError(
                code=ErrorCode.BAD_REQUEST,
                message="Invalid random.recommendation.score_weights",
                status_code=400,
            )
        score_weights_obj = {}
    else:
        score_weights_obj = score_weights_raw
        if strict:
            for k in score_weights_obj.keys():
                if str(k) not in _DEFAULT_SCORE_WEIGHTS:
                    raise ApiError(
                        code=ErrorCode.BAD_REQUEST,
                        message="Invalid random.recommendation.score_weights",
                        status_code=400,
                    )

    score_weights: dict[str, float] = {}
    for key, default_value in _DEFAULT_SCORE_WEIGHTS.items():
        if key in score_weights_obj:
            v = _as_float(score_weights_obj.get(key))
            if v is None:
                if strict:
                    raise ApiError(
                        code=ErrorCode.BAD_REQUEST,
                        message="Invalid random.recommendation.score_weights",
                        status_code=400,
                    )
                v = float(default_value)
            score_weights[key] = float(max(-100.0, min(float(v), 100.0)))
        else:
            score_weights[key] = float(default_value)

    multipliers_default = _DEFAULT_RECOMMENDATION["multipliers"]
    multipliers_raw = value.get("multipliers")
    if multipliers_raw is None:
        multipliers_obj: dict[str, Any] = {}
    elif not isinstance(multipliers_raw, dict):
        if strict:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid random.recommendation.multipliers", status_code=400)
        multipliers_obj = {}
    else:
        multipliers_obj = multipliers_raw
        if strict:
            for k in multipliers_obj.keys():
                if str(k) not in multipliers_default:
                    raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid random.recommendation.multipliers", status_code=400)

    multipliers: dict[str, float] = {}
    for key, default_value in multipliers_default.items():
        if key in multipliers_obj:
            v = _as_float(multipliers_obj.get(key))
            if v is None:
                if strict:
                    raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid random.recommendation.multipliers", status_code=400)
                v = float(default_value)
            multipliers[key] = float(max(0.0, min(float(v), 100.0)))
        else:
            multipliers[key] = float(default_value)

    return {
        "pick_mode": pick_mode,
        "temperature": temperature,
        "score_weights": score_weights,
        "multipliers": multipliers,
    }


async def _load_settings_json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception as exc:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400) from exc

    if not isinstance(data, dict):
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400)

    settings = data.get("settings") if "settings" in data else data
    if not isinstance(settings, dict):
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid settings", status_code=400)
    if not settings:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Missing fields", status_code=400)

    return settings


@router.get("/settings")
async def get_settings(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)

    engine = request.app.state.engine
    values = await fetch_runtime_settings(engine)
    runtime = runtime_config_from_values(values)

    random_defaults = dict(_DEFAULT_SETTINGS["random"])
    if isinstance(runtime.random_defaults, dict):
        for k in list(random_defaults.keys()):
            if k == "recommendation":
                continue
            if k in runtime.random_defaults:
                random_defaults[k] = runtime.random_defaults[k]
        random_defaults["recommendation"] = _normalize_recommendation(runtime.random_defaults.get("recommendation"), strict=False)
    else:
        random_defaults["recommendation"] = _normalize_recommendation(None, strict=False)

    return {
        "ok": True,
        "settings": {
            "proxy": {
                "enabled": bool(runtime.proxy_enabled),
                "fail_closed": bool(runtime.proxy_fail_closed),
                "route_mode": runtime.proxy_route_mode,
                "allowlist_domains": list(runtime.proxy_allowlist_domains),
                "default_pool_id": str(runtime.proxy_default_pool_id) if runtime.proxy_default_pool_id is not None else "",
                "route_pools": {k: str(v) for k, v in runtime.proxy_route_pools.items()},
            },
            "image_proxy": {
                "use_pixiv_cat": bool(runtime.image_proxy_use_pixiv_cat),
                "pximg_mirror_host": str(runtime.image_proxy_pximg_mirror_host or DEFAULT_PXIMG_MIRROR_HOST),
                "extra_pximg_mirror_hosts": list(getattr(runtime, "image_proxy_extra_pximg_mirror_hosts", []) or []),
            },
            "random": random_defaults,
            "security": {"hide_origin_url_in_public_json": bool(runtime.hide_origin_url_in_public_json)},
            "rate_limit": dict(runtime.rate_limit),
        },
        "request_id": rid,
    }


@router.put("/settings")
async def update_settings(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    rid = get_or_create_request_id(request)
    body = await _load_settings_json(request)

    actor = str(_claims.get("sub") or "admin").strip() or "admin"
    updated_by = f"admin:{actor}"

    updates: list[tuple[str, Any]] = []
    proxy_enabled_override: bool | None = None
    proxy_fail_closed_override: bool | None = None

    proxy = body.get("proxy")
    if proxy is not None:
        if not isinstance(proxy, dict):
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid proxy", status_code=400)

        if "enabled" in proxy:
            v = _as_bool(proxy.get("enabled"))
            if v is None:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid proxy.enabled", status_code=400)
            proxy_enabled_override = bool(v)
            updates.append(("proxy.enabled", bool(v)))

        if "fail_closed" in proxy:
            v = _as_bool(proxy.get("fail_closed"))
            if v is None:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid proxy.fail_closed", status_code=400)
            proxy_fail_closed_override = bool(v)
            updates.append(("proxy.fail_closed", bool(v)))

        if "route_mode" in proxy:
            route_mode = str(proxy.get("route_mode") or "").strip().lower()
            if route_mode not in {"pixiv_only", "all", "allowlist", "off"}:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid proxy.route_mode", status_code=400)
            updates.append(("proxy.route_mode", route_mode))

        if "allowlist_domains" in proxy:
            domains = _as_str_list(proxy.get("allowlist_domains"))
            if len(domains) > 200 or any(len(d) > 200 for d in domains):
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid proxy.allowlist_domains", status_code=400)
            updates.append(("proxy.allowlist_domains", domains))

        if "default_pool_id" in proxy:
            raw = proxy.get("default_pool_id")
            if raw is None or raw == "":
                updates.append(("proxy.default_pool_id", None))
            else:
                try:
                    pool_id = int(raw)
                except Exception as exc:
                    raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid proxy.default_pool_id", status_code=400) from exc
                if pool_id <= 0:
                    raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid proxy.default_pool_id", status_code=400)
                updates.append(("proxy.default_pool_id", int(pool_id)))

        if "route_pools" in proxy:
            raw = proxy.get("route_pools")
            if raw is None:
                updates.append(("proxy.route_pools", {}))
            else:
                if not isinstance(raw, dict):
                    raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid proxy.route_pools", status_code=400)
                route_pools: dict[str, int] = {}
                if len(raw) > 200:
                    raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid proxy.route_pools", status_code=400)
                for k, v in raw.items():
                    key = str(k or "").strip().lower().strip(".")
                    if not key or len(key) > 200:
                        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid proxy.route_pools", status_code=400)
                    try:
                        pool_id = int(v)
                    except Exception as exc:
                        raise ApiError(
                            code=ErrorCode.BAD_REQUEST,
                            message="Invalid proxy.route_pools",
                            status_code=400,
                        ) from exc
                    if pool_id <= 0:
                        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid proxy.route_pools", status_code=400)
                    route_pools[key] = int(pool_id)
                updates.append(("proxy.route_pools", route_pools))

    image_proxy = body.get("image_proxy")
    if image_proxy is not None:
        if not isinstance(image_proxy, dict):
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid image_proxy", status_code=400)

        if "use_pixiv_cat" in image_proxy:
            v = _as_bool(image_proxy.get("use_pixiv_cat"))
            if v is None:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid image_proxy.use_pixiv_cat", status_code=400)
            updates.append(("image_proxy.use_pixiv_cat", bool(v)))

        if "pximg_mirror_host" in image_proxy:
            raw = image_proxy.get("pximg_mirror_host")
            if raw is None or raw == "":
                updates.append(("image_proxy.pximg_mirror_host", None))
            else:
                host = normalize_pximg_mirror_host(raw)
                if host is None:
                    raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid image_proxy.pximg_mirror_host", status_code=400)
                updates.append(("image_proxy.pximg_mirror_host", str(host)))

        if "extra_pximg_mirror_hosts" in image_proxy:
            raw = image_proxy.get("extra_pximg_mirror_hosts")
            if raw is None:
                updates.append(("image_proxy.extra_pximg_mirror_hosts", []))
            else:
                if not isinstance(raw, list):
                    raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid image_proxy.extra_pximg_mirror_hosts", status_code=400)
                candidates = _as_str_list(raw)
                if len(candidates) > 200:
                    raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid image_proxy.extra_pximg_mirror_hosts", status_code=400)
                normalized: list[str] = []
                seen: set[str] = set()
                for item in candidates:
                    host = normalize_pximg_custom_mirror_host(item)
                    if host is None:
                        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid image_proxy.extra_pximg_mirror_hosts", status_code=400)
                    if host in seen:
                        continue
                    seen.add(host)
                    normalized.append(host)
                updates.append(("image_proxy.extra_pximg_mirror_hosts", normalized))

    random = body.get("random")
    if random is not None:
        if not isinstance(random, dict):
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid random", status_code=400)

        defaults: dict[str, Any] = {}
        for key in ("default_attempts", "default_r18_strict", "fail_cooldown_ms", "strategy", "quality_samples"):
            if key not in random:
                continue
            if key in {"default_attempts", "fail_cooldown_ms", "quality_samples"}:
                try:
                    n = int(random.get(key))
                except Exception as exc:
                    raise ApiError(code=ErrorCode.BAD_REQUEST, message=f"Invalid random.{key}", status_code=400) from exc
                if key == "quality_samples":
                    if n < 1 or n > 1000:
                        raise ApiError(code=ErrorCode.BAD_REQUEST, message=f"Invalid random.{key}", status_code=400)
                elif n < 0 or n > 10_000_000:
                    raise ApiError(code=ErrorCode.BAD_REQUEST, message=f"Invalid random.{key}", status_code=400)
                defaults[key] = n
            elif key == "strategy":
                s = str(random.get(key) or "").strip().lower()
                if s not in {"quality", "random"}:
                    raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid random.strategy", status_code=400)
                defaults[key] = s
            else:
                v = _as_bool(random.get(key))
                if v is None:
                    raise ApiError(code=ErrorCode.BAD_REQUEST, message=f"Invalid random.{key}", status_code=400)
                defaults[key] = bool(v)

        if "recommendation" in random:
            defaults["recommendation"] = _normalize_recommendation(random.get("recommendation"), strict=True)

        if defaults:
            values = await fetch_runtime_settings(request.app.state.engine)
            runtime = runtime_config_from_values(values)
            existing_raw = runtime.random_defaults if isinstance(runtime.random_defaults, dict) else {}
            if existing_raw:
                merged = dict(existing_raw)
                merged.update(defaults)
                defaults = merged
            updates.append(("random.defaults", defaults))

    security = body.get("security")
    if security is not None:
        if not isinstance(security, dict):
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid security", status_code=400)

        if "hide_origin_url_in_public_json" in security:
            v = _as_bool(security.get("hide_origin_url_in_public_json"))
            if v is None:
                raise ApiError(
                    code=ErrorCode.BAD_REQUEST,
                    message="Invalid security.hide_origin_url_in_public_json",
                    status_code=400,
                )
            updates.append(("security.hide_origin_url_in_public_json", bool(v)))

    rate_limit = body.get("rate_limit")
    if rate_limit is not None:
        if not isinstance(rate_limit, dict):
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid rate_limit", status_code=400)

        for key, value in rate_limit.items():
            k = str(key or "").strip()
            if not k or len(k) > 100:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid rate_limit key", status_code=400)
            updates.append((f"rate_limit.{k}", value))

    if not updates:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Missing fields", status_code=400)

    engine = request.app.state.engine

    if proxy_enabled_override is not None or proxy_fail_closed_override is not None:
        values = await fetch_runtime_settings(engine)
        runtime = runtime_config_from_values(values)
        desired_enabled = proxy_enabled_override if proxy_enabled_override is not None else bool(runtime.proxy_enabled)
        desired_fail_closed = (
            proxy_fail_closed_override if proxy_fail_closed_override is not None else bool(runtime.proxy_fail_closed)
        )
        if desired_enabled and desired_fail_closed:
            async with engine.connect() as conn:
                result = await conn.exec_driver_sql("SELECT COUNT(*) FROM proxy_endpoints WHERE enabled=1;")
                enabled_proxy_count = int(result.scalar_one())
            if enabled_proxy_count <= 0:
                raise ApiError(
                    code=ErrorCode.PROXY_REQUIRED,
                    message="Proxy required (fail-closed) but no enabled proxies",
                    status_code=400,
                )

    for key, value in updates:
        await set_runtime_setting(engine, key=key, value=value, updated_by=updated_by)

    return {"ok": True, "updated": len(updates), "request_id": rid}
