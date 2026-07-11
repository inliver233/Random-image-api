from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.admin.deps import get_admin_claims
from app.core.errors import ApiError, ErrorCode
from app.core.admin_json import admin_ok
from app.core.admin_request import (
    load_json_object,
    parse_bool_optional,
    parse_choice,
    parse_float_clamped,
    parse_int_clamped,
    parse_int_in_range,
    parse_positive_int,
    parse_required_bool,
)
from app.core.recommendation import DEFAULT_RECOMMENDATION, DEFAULT_SCORE_WEIGHTS
from app.core.request_id import get_or_create_request_id
from app.core.runtime_config_cache import invalidate_runtime_config_cache
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

_DEFAULT_SCORE_WEIGHTS = DEFAULT_SCORE_WEIGHTS
_DEFAULT_RECOMMENDATION = DEFAULT_RECOMMENDATION

_DEFAULT_SETTINGS = {
    "random": {
        "default_attempts": 3,
        "default_r18_strict": True,
        "fail_cooldown_ms": 600_000,
        "strategy": "quality",
        "quality_samples": 12,
        "dedup": {
            "enabled": True,
            "window_s": 20 * 60,
            "max_images": 5000,
            "max_authors": 2000,
            "strict": False,
            "image_penalty": 8.0,
            "author_penalty": 2.5,
        },
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


def _normalize_dedup(value: Any, *, strict: bool) -> dict[str, Any]:
    default = dict(_DEFAULT_SETTINGS["random"]["dedup"])
    if value is None:
        return default
    if not isinstance(value, dict):
        if strict:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid random.dedup", status_code=400)
        return default

    if strict:
        for k in value.keys():
            if str(k) not in default:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid random.dedup", status_code=400)

    enabled = default["enabled"]
    v = parse_bool_optional(value.get("enabled"))
    if v is not None:
        enabled = bool(v)

    window_s = int(default["window_s"])
    if "window_s" in value:
        window_s = parse_int_clamped(
            value.get("window_s"),
            field="window_s",
            min_value=0,
            max_value=24 * 60 * 60,
            strict=strict,
            default=int(default["window_s"]),
            invalid_message="Invalid random.dedup.window_s",
        )

    max_images = int(default["max_images"])
    if "max_images" in value:
        max_images = parse_int_clamped(
            value.get("max_images"),
            field="max_images",
            min_value=1,
            max_value=200_000,
            strict=strict,
            default=int(default["max_images"]),
            invalid_message="Invalid random.dedup.max_images",
        )

    max_authors = int(default["max_authors"])
    if "max_authors" in value:
        max_authors = parse_int_clamped(
            value.get("max_authors"),
            field="max_authors",
            min_value=1,
            max_value=200_000,
            strict=strict,
            default=int(default["max_authors"]),
            invalid_message="Invalid random.dedup.max_authors",
        )

    strict_mode = bool(default["strict"])
    v = parse_bool_optional(value.get("strict"))
    if v is not None:
        strict_mode = bool(v)

    # Dedup penalties always raise on invalid (historical GET/PUT behavior).
    image_penalty = float(default["image_penalty"])
    if "image_penalty" in value:
        image_penalty = parse_float_clamped(
            value.get("image_penalty"),
            field="image_penalty",
            min_value=0.0,
            max_value=1000.0,
            strict=True,
            require_finite=True,
            invalid_message="Invalid random.dedup.image_penalty",
        )

    author_penalty = float(default["author_penalty"])
    if "author_penalty" in value:
        author_penalty = parse_float_clamped(
            value.get("author_penalty"),
            field="author_penalty",
            min_value=0.0,
            max_value=1000.0,
            strict=True,
            require_finite=True,
            invalid_message="Invalid random.dedup.author_penalty",
        )

    return {
        "enabled": bool(enabled),
        "window_s": int(window_s),
        "max_images": int(max_images),
        "max_authors": int(max_authors),
        "strict": bool(strict_mode),
        "image_penalty": float(image_penalty),
        "author_penalty": float(author_penalty),
    }


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
        if not candidate:
            if strict:
                raise ApiError(
                    code=ErrorCode.BAD_REQUEST,
                    message="Invalid random.recommendation.pick_mode",
                    status_code=400,
                )
        else:
            try:
                pick_mode = parse_choice(
                    candidate,
                    field="pick_mode",
                    choices=frozenset({"best", "weighted"}),
                    invalid_message="Invalid random.recommendation.pick_mode",
                )
            except ApiError:
                if strict:
                    raise
                pick_mode = pick_mode_default

    temperature_default = float(_DEFAULT_RECOMMENDATION["temperature"])
    temperature = temperature_default
    if "temperature" in value:
        temperature = parse_float_clamped(
            value.get("temperature"),
            field="temperature",
            min_value=0.05,
            max_value=100.0,
            strict=strict,
            default=temperature_default,
            invalid_message="Invalid random.recommendation.temperature",
        )

    freshness_half_life_default = float(_DEFAULT_RECOMMENDATION["freshness_half_life_days"])
    freshness_half_life_days = freshness_half_life_default
    if "freshness_half_life_days" in value:
        freshness_half_life_days = parse_float_clamped(
            value.get("freshness_half_life_days"),
            field="freshness_half_life_days",
            min_value=0.1,
            max_value=3650.0,
            strict=strict,
            default=freshness_half_life_default,
            require_finite=True,
            invalid_message="Invalid random.recommendation.freshness_half_life_days",
        )

    velocity_smooth_default = float(_DEFAULT_RECOMMENDATION["velocity_smooth_days"])
    velocity_smooth_days = velocity_smooth_default
    if "velocity_smooth_days" in value:
        velocity_smooth_days = parse_float_clamped(
            value.get("velocity_smooth_days"),
            field="velocity_smooth_days",
            min_value=0.0,
            max_value=3650.0,
            strict=strict,
            default=velocity_smooth_default,
            require_finite=True,
            invalid_message="Invalid random.recommendation.velocity_smooth_days",
        )

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
            score_weights[key] = parse_float_clamped(
                score_weights_obj.get(key),
                field="score_weights",
                min_value=-100.0,
                max_value=100.0,
                strict=strict,
                default=float(default_value),
                invalid_message="Invalid random.recommendation.score_weights",
            )
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
            multipliers[key] = parse_float_clamped(
                multipliers_obj.get(key),
                field="multipliers",
                min_value=0.0,
                max_value=100.0,
                strict=strict,
                default=float(default_value),
                invalid_message="Invalid random.recommendation.multipliers",
            )
        else:
            multipliers[key] = float(default_value)

    return {
        "pick_mode": pick_mode,
        "temperature": temperature,
        "score_weights": score_weights,
        "freshness_half_life_days": float(freshness_half_life_days),
        "velocity_smooth_days": float(velocity_smooth_days),
        "multipliers": multipliers,
    }


async def _load_settings_json(request: Request) -> dict[str, Any]:
    data = await load_json_object(request)

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
        random_defaults["dedup"] = _normalize_dedup(runtime.random_defaults.get("dedup"), strict=False)
    else:
        random_defaults["recommendation"] = _normalize_recommendation(None, strict=False)
        random_defaults["dedup"] = _normalize_dedup(None, strict=False)

    return admin_ok(request, payload={"settings": {
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
        }}, request_id=rid)


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
            v = parse_required_bool(
                proxy.get("enabled"),
                field="proxy.enabled",
                invalid_message="Invalid proxy.enabled",
            )
            proxy_enabled_override = bool(v)
            updates.append(("proxy.enabled", bool(v)))

        if "fail_closed" in proxy:
            v = parse_required_bool(
                proxy.get("fail_closed"),
                field="proxy.fail_closed",
                invalid_message="Invalid proxy.fail_closed",
            )
            proxy_fail_closed_override = bool(v)
            updates.append(("proxy.fail_closed", bool(v)))

        if "route_mode" in proxy:
            route_mode = parse_choice(
                proxy.get("route_mode"),
                field="route_mode",
                choices=frozenset({"pixiv_only", "all", "allowlist", "off"}),
                invalid_message="Invalid proxy.route_mode",
            )
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
                pool_id = parse_positive_int(
                    raw,
                    field="default_pool_id",
                    invalid_message="Invalid proxy.default_pool_id",
                )
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
                    pool_id = parse_positive_int(
                        v,
                        field="route_pools",
                        invalid_message="Invalid proxy.route_pools",
                    )
                    route_pools[key] = int(pool_id)
                updates.append(("proxy.route_pools", route_pools))

    image_proxy = body.get("image_proxy")
    if image_proxy is not None:
        if not isinstance(image_proxy, dict):
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid image_proxy", status_code=400)

        if "use_pixiv_cat" in image_proxy:
            v = parse_required_bool(
                image_proxy.get("use_pixiv_cat"),
                field="image_proxy.use_pixiv_cat",
                invalid_message="Invalid image_proxy.use_pixiv_cat",
            )
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
            if key == "quality_samples":
                defaults[key] = parse_int_in_range(
                    random.get(key),
                    field=key,
                    min_value=1,
                    max_value=200,
                    invalid_message=f"Invalid random.{key}",
                )
            elif key in {"default_attempts", "fail_cooldown_ms"}:
                defaults[key] = parse_int_in_range(
                    random.get(key),
                    field=key,
                    min_value=0,
                    max_value=10_000_000,
                    invalid_message=f"Invalid random.{key}",
                )
            elif key == "strategy":
                defaults[key] = parse_choice(
                    random.get(key),
                    field="strategy",
                    choices=frozenset({"quality", "random"}),
                    invalid_message="Invalid random.strategy",
                )
            else:
                defaults[key] = parse_required_bool(
                    random.get(key),
                    field=f"random.{key}",
                    invalid_message=f"Invalid random.{key}",
                )

        if "recommendation" in random:
            defaults["recommendation"] = _normalize_recommendation(random.get("recommendation"), strict=True)

        if "dedup" in random:
            defaults["dedup"] = _normalize_dedup(random.get("dedup"), strict=True)

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
            v = parse_required_bool(
                security.get("hide_origin_url_in_public_json"),
                field="security.hide_origin_url_in_public_json",
                invalid_message="Invalid security.hide_origin_url_in_public_json",
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

    try:
        cache = getattr(request.app.state, "runtime_config_cache", None)
        if cache is not None:
            cache.invalidate()
        else:
            invalidate_runtime_config_cache()
    except Exception:
        invalidate_runtime_config_cache()

    return admin_ok(request, payload={"updated": len(updates)}, request_id=rid)
