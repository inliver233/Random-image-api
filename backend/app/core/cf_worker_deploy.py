from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import httpx

from app.core.cf_pool_registry import normalize_cf_base_url
from app.core.logging import get_logger

log = get_logger(__name__)

WorkerKind = Literal["api", "image"]

CF_API_BASE = "https://api.cloudflare.com/client/v4"
_DEFAULT_COMPAT_DATE = "2026-01-01"
_WORKER_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

_MODULE_NAME: dict[WorkerKind, str] = {
    "api": "api-worker.js",
    "image": "img-worker.js",
}

# Non-secret vars embedded at deploy time (match edge/*/wrangler.toml defaults).
_DEFAULT_VARS: dict[WorkerKind, dict[str, str]] = {
    "api": {
        "ALLOWED_HOSTS": "oauth.secure.pixiv.net,app-api.pixiv.net,public-api.secure.pixiv.net",
        "RATE_LIMIT_RPM": "600",
    },
    "image": {
        "ORIGIN_HOST": "i.pximg.net",
        "FALLBACK_MIRROR_HOST": "",
        "FALLBACK_MIRROR_HOSTS": "",
        "CACHE_TTL_SECONDS": "604800",
        "ERROR_CACHE_TTL_SECONDS": "30",
        "ORIGIN_403_CIRCUIT_THRESHOLD": "8",
        "ORIGIN_403_CIRCUIT_WINDOW_MS": "60000",
        "ORIGIN_403_CIRCUIT_OPEN_MS": "30000",
        "R2_MODE": "read_through",
        "RATE_LIMIT_RPM": "3000",
    },
}


@dataclass(frozen=True, slots=True)
class CfWorkerDeployResult:
    kind: WorkerKind
    worker_name: str
    worker_host: str
    base_url: str
    secrets_set: list[str]
    deployed: bool = True
    # Admin deploy never attaches R2 bucket bindings (wrangler/dashboard only).
    # R2_MODE plain var is set for image workers but is a no-op until env.R2 exists.
    r2_binding: bool = False
    r2_note: str = (
        "Admin deploy does not attach R2 bucket binding; "
        "R2_MODE is ignored until bound via wrangler/dashboard."
    )


class CfWorkerDeployError(RuntimeError):
    """User-facing deploy failure (mapped to 4xx/502 by admin handler)."""

    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = int(status_code)


def repo_root_from_backend() -> Path:
    """backend/app/core → repo root."""
    return Path(__file__).resolve().parents[3]


def resolve_worker_script_path(kind: WorkerKind, *, root: Path | None = None) -> Path:
    base = root or repo_root_from_backend()
    if kind == "api":
        return base / "edge" / "api-worker" / "src" / "index.js"
    return base / "edge" / "img-worker" / "src" / "index.js"


def resolve_worker_pure_path(kind: WorkerKind, *, root: Path | None = None) -> Path:
    """Sibling pure.js imported by index.js (must ship with Admin CF API upload)."""
    return resolve_worker_script_path(kind, root=root).parent / "pure.js"


def load_worker_script(kind: WorkerKind, *, root: Path | None = None) -> bytes:
    path = resolve_worker_script_path(kind, root=root)
    if not path.is_file():
        raise CfWorkerDeployError(f"Worker script not found: {path}", status_code=500)
    raw = path.read_bytes()
    if not raw.strip():
        raise CfWorkerDeployError(f"Worker script empty: {path}", status_code=500)
    return raw


def load_worker_pure_script(kind: WorkerKind, *, root: Path | None = None) -> bytes:
    """Load pure.js module required by index.js ES module imports."""
    path = resolve_worker_pure_path(kind, root=root)
    if not path.is_file():
        raise CfWorkerDeployError(f"Worker pure.js not found: {path}", status_code=500)
    raw = path.read_bytes()
    if not raw.strip():
        raise CfWorkerDeployError(f"Worker pure.js empty: {path}", status_code=500)
    return raw


def validate_worker_name(name: str) -> str:
    n = (name or "").strip().lower()
    if not n or not _WORKER_NAME_RE.match(n):
        raise CfWorkerDeployError(
            "worker_name must be 1–63 chars: lowercase alnum/hyphen, start/end alnum",
            status_code=400,
        )
    return n


def validate_account_id(account_id: str) -> str:
    a = (account_id or "").strip()
    if not a or len(a) > 64 or not re.fullmatch(r"[a-fA-F0-9]+", a):
        raise CfWorkerDeployError(
            "account_id must be a non-empty hex Cloudflare account id",
            status_code=400,
        )
    return a


def validate_api_token(token: str) -> str:
    t = (token or "").strip()
    if not t or len(t) < 20 or len(t) > 200:
        raise CfWorkerDeployError("api_token is required (Cloudflare API token)", status_code=400)
    return t


def _cf_account_url(account_id: str, suffix: str) -> str:
    return f"{CF_API_BASE}/accounts/{quote(account_id, safe='')}{suffix}"


def _cf_script_url(account_id: str, worker_name: str) -> str:
    return _cf_account_url(account_id, f"/workers/scripts/{quote(worker_name, safe='')}")


def _parse_cf_error(body: bytes, *, fallback: str) -> str:
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return fallback
    if not isinstance(data, dict):
        return fallback
    errors = data.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            msg = str(first.get("message") or "").strip()
            if msg:
                return msg
    messages = data.get("messages")
    if isinstance(messages, list) and messages:
        first = messages[0]
        if isinstance(first, dict):
            msg = str(first.get("message") or "").strip()
            if msg:
                return msg
    return fallback


def _cf_success(body: bytes) -> bool:
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return False
    return bool(isinstance(data, dict) and data.get("success") is True)


def _build_bindings(
    *,
    kind: WorkerKind,
    plain_vars: dict[str, str] | None,
    secrets: dict[str, str] | None,
) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    vars_map = dict(_DEFAULT_VARS.get(kind) or {})
    if plain_vars:
        for k, v in plain_vars.items():
            key = str(k or "").strip()
            if not key:
                continue
            vars_map[key] = str(v)
    for k, v in vars_map.items():
        bindings.append({"type": "plain_text", "name": str(k), "text": str(v)})
    if secrets:
        for k, v in secrets.items():
            key = str(k or "").strip()
            val = str(v or "").strip()
            if not key or not val:
                continue
            bindings.append({"type": "secret_text", "name": key, "text": val})
    return bindings


def secrets_for_kind(
    kind: WorkerKind,
    *,
    proxy_secret: str = "",
    image_edge_secret: str = "",
    prewarm_secret: str = "",
    image_edge_secret_previous: str = "",
) -> dict[str, str]:
    """Build secret_text bindings (Pixiv-hardened — never open whole-site proxy)."""
    out: dict[str, str] = {}
    if kind == "api":
        secret = (proxy_secret or "").strip()
        if not secret:
            raise CfWorkerDeployError(
                "proxy_secret required for api-worker (fail-closed X-Proxy-Secret)",
                status_code=400,
            )
        out["PROXY_SECRET"] = secret
        return out
    secret = (image_edge_secret or "").strip()
    if not secret:
        raise CfWorkerDeployError(
            "image_edge_secret required for img-worker (HMAC IMAGE_EDGE_SECRET)",
            status_code=400,
        )
    out["IMAGE_EDGE_SECRET"] = secret
    prev = (image_edge_secret_previous or "").strip()
    if prev and prev != secret:
        out["IMAGE_EDGE_SECRET_PREVIOUS"] = prev
    pre = (prewarm_secret or "").strip()
    if pre:
        out["PREWARM_SECRET"] = pre
    return out


async def _upload_worker_script(
    client: httpx.AsyncClient,
    *,
    api_token: str,
    account_id: str,
    worker_name: str,
    kind: WorkerKind,
    script: bytes,
    bindings: list[dict[str, Any]],
    pure_script: bytes | None = None,
) -> None:
    module_name = _MODULE_NAME[kind]
    meta = {
        "main_module": module_name,
        "compatibility_date": _DEFAULT_COMPAT_DATE,
        "bindings": bindings,
    }
    # index.js imports "./pure.js"; Admin CF API multipart must include both modules.
    pure_bytes = pure_script if pure_script is not None else load_worker_pure_script(kind)
    files = {
        "metadata": ("metadata", json.dumps(meta, separators=(",", ":")), "application/json"),
        module_name: (module_name, script, "application/javascript+module"),
        "pure.js": ("pure.js", pure_bytes, "application/javascript+module"),
    }
    url = _cf_script_url(account_id, worker_name)
    headers = {
        "Authorization": f"Bearer {api_token}",
        "CF-WORKER-MAIN-MODULE-PART": module_name,
    }
    resp = await client.put(url, headers=headers, files=files, timeout=60.0)
    body = resp.content
    if not _cf_success(body):
        msg = _parse_cf_error(body, fallback=f"CF script upload failed status={resp.status_code}")
        log.warning(
            "cf_worker_upload_failed kind=%s name=%s status=%s err=%s",
            kind,
            worker_name,
            resp.status_code,
            msg,
        )
        raise CfWorkerDeployError(msg, status_code=502)


async def _enable_workers_dev_subdomain(
    client: httpx.AsyncClient,
    *,
    api_token: str,
    account_id: str,
    worker_name: str,
) -> None:
    url = _cf_script_url(account_id, worker_name) + "/subdomain"
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }
    resp = await client.post(url, headers=headers, content=b'{"enabled":true}', timeout=30.0)
    body = resp.content
    if not _cf_success(body):
        msg = _parse_cf_error(body, fallback="failed to enable workers.dev subdomain")
        raise CfWorkerDeployError(msg, status_code=502)


async def _get_account_workers_dev_host(
    client: httpx.AsyncClient,
    *,
    api_token: str,
    account_id: str,
) -> str:
    url = _cf_account_url(account_id, "/workers/subdomain")
    headers = {"Authorization": f"Bearer {api_token}"}
    resp = await client.get(url, headers=headers, timeout=30.0)
    body = resp.content
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except Exception as exc:
        raise CfWorkerDeployError(
            "failed to parse workers.dev subdomain response", status_code=502
        ) from exc
    if not isinstance(data, dict) or not data.get("success"):
        msg = _parse_cf_error(body, fallback="failed to resolve workers.dev subdomain")
        raise CfWorkerDeployError(msg, status_code=502)
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    sub = str((result or {}).get("subdomain") or "").strip()
    if not sub:
        raise CfWorkerDeployError("workers.dev subdomain is empty for this account", status_code=502)
    if sub.endswith(".workers.dev"):
        return sub
    return f"{sub}.workers.dev"


async def deploy_cf_worker(
    *,
    kind: WorkerKind,
    api_token: str,
    account_id: str,
    worker_name: str,
    proxy_secret: str = "",
    image_edge_secret: str = "",
    prewarm_secret: str = "",
    image_edge_secret_previous: str = "",
    plain_vars: dict[str, str] | None = None,
    root: Path | None = None,
    client: httpx.AsyncClient | None = None,
) -> CfWorkerDeployResult:
    """Upload hardened Worker via CF API, enable workers.dev, return pool base_url.

    Does **not** flip CF_API_PROXY_ENABLED / IMAGE_EDGE_ENABLED — probe then enable.
    Secrets are sent only as CF secret_text bindings (never stored by this helper).
    """
    kind_norm: WorkerKind = "api" if kind == "api" else "image"
    token = validate_api_token(api_token)
    acc = validate_account_id(account_id)
    name = validate_worker_name(worker_name)
    secrets = secrets_for_kind(
        kind_norm,
        proxy_secret=proxy_secret,
        image_edge_secret=image_edge_secret,
        prewarm_secret=prewarm_secret,
        image_edge_secret_previous=image_edge_secret_previous,
    )
    script = load_worker_script(kind_norm, root=root)
    pure_script = load_worker_pure_script(kind_norm, root=root)
    bindings = _build_bindings(kind=kind_norm, plain_vars=plain_vars, secrets=secrets)

    owns_client = client is None
    http = client or httpx.AsyncClient()
    try:
        await _upload_worker_script(
            http,
            api_token=token,
            account_id=acc,
            worker_name=name,
            kind=kind_norm,
            script=script,
            bindings=bindings,
            pure_script=pure_script,
        )
        await _enable_workers_dev_subdomain(
            http, api_token=token, account_id=acc, worker_name=name
        )
        account_host = await _get_account_workers_dev_host(
            http, api_token=token, account_id=acc
        )
    finally:
        if owns_client:
            await http.aclose()

    worker_host = f"{name}.{account_host}"
    base = normalize_cf_base_url(f"https://{worker_host}")
    if not base:
        raise CfWorkerDeployError("failed to normalize worker base_url", status_code=500)

    log.info(
        "cf_worker_deployed kind=%s name=%s base=%s secrets=%s",
        kind_norm,
        name,
        base,
        sorted(secrets.keys()),
    )
    return CfWorkerDeployResult(
        kind=kind_norm,
        worker_name=name,
        worker_host=worker_host,
        base_url=base,
        secrets_set=sorted(secrets.keys()),
        deployed=True,
        r2_binding=False,
        r2_note=(
            "Admin deploy does not attach R2 bucket binding; "
            "R2_MODE is ignored until bound via wrangler/dashboard."
            if kind_norm == "image"
            else "R2 not applicable for api-worker."
        ),
    )
