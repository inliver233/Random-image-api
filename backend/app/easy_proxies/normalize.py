from __future__ import annotations

from urllib.parse import urlparse

_INVALID_EXPORT_HOSTS = {
    "0.0.0.0",
    "127.0.0.1",
    "localhost",
    "::",
    "::1",
}


def resolve_export_host(*, base_url: str, host_override: str | None = None) -> str | None:
    override = str(host_override or "").strip()
    if override:
        host = override
    else:
        try:
            parsed = urlparse(str(base_url or "").strip())
        except Exception:
            return None
        host = str(parsed.hostname or "").strip()

    if not host:
        return None
    if host.lower() in _INVALID_EXPORT_HOSTS:
        return None
    return host


def normalize_exported_proxy_host(*, exported_host: str, export_host: str | None) -> tuple[str, bool]:
    host = str(exported_host or "").strip()
    if not host or not export_host:
        return host, False
    if host.lower() in _INVALID_EXPORT_HOSTS:
        return str(export_host), True
    return host, False

