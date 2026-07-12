from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

from app.core.config import parse_csv_urls

PoolKind = Literal["api", "image"]

# Runtime-settings keys for ops-registered CF pool members (merged with env bases).
RUNTIME_KEY_API_BASES = "cf_pool.api.base_urls"
RUNTIME_KEY_IMAGE_BASES = "cf_pool.image.base_urls"
# Deploy-time business enable (OR with env CF_API_PROXY_ENABLED / IMAGE_EDGE_ENABLED).
RUNTIME_KEY_API_ENABLED = "cf_pool.api.enabled"
RUNTIME_KEY_IMAGE_ENABLED = "cf_pool.image.enabled"
# Optional BFF shared secrets when env is empty (never returned by admin pool list).
RUNTIME_KEY_API_SECRET = "cf_pool.api.secret"
RUNTIME_KEY_IMAGE_SECRET = "cf_pool.image.secret"
RUNTIME_KEY_IMAGE_SECRET_PREVIOUS = "cf_pool.image.secret_previous"


@dataclass(frozen=True, slots=True)
class CfPoolMember:
    """One CF Worker base URL in the egress pool (ds2api-style pool member)."""

    kind: PoolKind
    base_url: str
    source: str  # "env" | "runtime" | "register"


def normalize_cf_base_url(raw: str) -> str | None:
    """Accept http(s) base; strip trailing slash; reject empty / non-http."""
    base = (raw or "").strip().rstrip("/")
    if not base:
        return None
    if not (base.startswith("https://") or base.startswith("http://")):
        # Allow bare workers.dev host from CF deploy response.
        host = base
        if "://" in host:
            return None
        if "/" in host or not host:
            return None
        base = f"https://{host}"
    try:
        parsed = urlparse(base)
    except Exception:
        return None
    if (parsed.scheme or "").lower() not in {"http", "https"}:
        return None
    if not (parsed.hostname or "").strip():
        return None
    # Drop path/query/fragment — pool members are origin bases only.
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def merge_base_url_lists(*lists: list[str] | tuple[str, ...] | None) -> list[str]:
    """First-seen order merge of http(s) bases (de-duped, trailing slash stripped)."""
    out: list[str] = []
    seen: set[str] = set()
    for group in lists:
        if not group:
            continue
        for raw in group:
            base = normalize_cf_base_url(str(raw or ""))
            if not base or base in seen:
                continue
            seen.add(base)
            out.append(base)
    return out


def parse_base_urls_payload(value: Any) -> list[str]:
    """Parse runtime JSON / body field into base URL list.

    Accepts: list[str], CSV string, or single URL string.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return merge_base_url_lists([str(x) for x in value])
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if "," in raw or ";" in raw:
            return parse_csv_urls(raw)
        one = normalize_cf_base_url(raw)
        return [one] if one else []
    return []


def register_base_url(existing: list[str] | None, base_url: str) -> list[str]:
    """Append base if new; return new list (never mutates input)."""
    return merge_base_url_lists(existing or [], [base_url])


def unregister_base_url(existing: list[str] | None, base_url: str) -> list[str]:
    """Remove matching base (normalized); return new list."""
    target = normalize_cf_base_url(base_url)
    if not target:
        return merge_base_url_lists(existing or [])
    return [b for b in merge_base_url_lists(existing or []) if b != target]


def pool_members_from_bases(
    *,
    kind: PoolKind,
    env_bases: list[str] | None,
    runtime_bases: list[str] | None,
) -> list[CfPoolMember]:
    """Build member list with source tags for admin/ops display."""
    env_set = set(merge_base_url_lists(env_bases or []))
    runtime_set = set(merge_base_url_lists(runtime_bases or []))
    merged = merge_base_url_lists(env_bases or [], runtime_bases or [])
    out: list[CfPoolMember] = []
    for base in merged:
        if base in env_set and base in runtime_set:
            source = "env+runtime"
        elif base in env_set:
            source = "env"
        else:
            source = "runtime"
        out.append(CfPoolMember(kind=kind, base_url=base, source=source))
    return out
