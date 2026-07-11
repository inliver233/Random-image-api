from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote


@dataclass(frozen=True, slots=True)
class ProxyUriParts:
    scheme: str
    host: str
    port: int
    username: str | None
    password: str | None


_ALLOWED_PROXY_SCHEMES = {"http", "https", "socks4", "socks5"}


def build_proxy_uri(
    encryptor: Any | None,
    *,
    scheme: str,
    host: str,
    port: int,
    username: str,
    password_enc: str,
) -> str:
    """
    Build an absolute proxy URI from stored endpoint fields.

    encryptor may be None only when password_enc is empty; otherwise decrypt requires it.
    """
    scheme_n = (scheme or "").strip().lower()
    host_n = (host or "").strip()
    username_n = (username or "").strip()
    password_enc_n = (password_enc or "").strip()
    if not scheme_n or not host_n or int(port) <= 0:
        raise ValueError("invalid proxy endpoint")

    password = ""
    if password_enc_n:
        if encryptor is None:
            raise ValueError("FIELD_ENCRYPTION_KEY is required to decrypt proxy password")
        password = encryptor.decrypt_text(password_enc_n)

    host_part = host_n
    if ":" in host_part and not host_part.startswith("["):
        host_part = f"[{host_part}]"

    auth = ""
    if username_n:
        user_q = quote(username_n, safe="")
        pass_q = quote(password or "", safe="")
        auth = f"{user_q}:{pass_q}@"

    return f"{scheme_n}://{auth}{host_part}:{int(port)}"


def _strip_authority(rest: str) -> str:
    for sep in ("/", "?", "#"):
        idx = rest.find(sep)
        if idx >= 0:
            return rest[:idx]
    return rest


def _parse_hostport(hostport: str) -> tuple[str, int]:
    hostport = hostport.strip()
    if not hostport:
        raise ValueError("missing hostport")

    if hostport.startswith("["):
        rb = hostport.find("]")
        if rb <= 0:
            raise ValueError("invalid ipv6 hostport")
        host = hostport[1:rb].strip()
        rest = hostport[rb + 1 :].strip()
        if not rest.startswith(":"):
            raise ValueError("missing port")
        port_s = rest[1:].strip()
    else:
        if ":" not in hostport:
            raise ValueError("missing port")
        host, port_s = hostport.rsplit(":", 1)
        host = host.strip()
        port_s = port_s.strip()

    if not host:
        raise ValueError("missing host")

    try:
        port = int(port_s)
    except Exception as exc:
        raise ValueError("invalid port") from exc
    if port <= 0 or port > 65535:
        raise ValueError("invalid port")

    return host, port


def parse_proxy_uri(uri: str) -> ProxyUriParts:
    uri = uri.strip()
    if not uri:
        raise ValueError("uri is required")
    if "://" not in uri:
        raise ValueError("invalid uri")

    scheme_raw, rest_raw = uri.split("://", 1)
    scheme = scheme_raw.strip().lower()
    if scheme not in _ALLOWED_PROXY_SCHEMES:
        raise ValueError("unsupported scheme")

    authority = _strip_authority(rest_raw.strip())
    if not authority:
        raise ValueError("invalid uri")

    username: str | None = None
    password: str | None = None

    if "@" in authority:
        userinfo, hostport = authority.rsplit("@", 1)
        if ":" not in userinfo:
            raise ValueError("invalid userinfo")
        username, password = userinfo.split(":", 1)
        username = unquote(username.strip())
        password = unquote(password)
        if not username:
            raise ValueError("invalid userinfo")
    else:
        hostport = authority

    host, port = _parse_hostport(hostport)

    return ProxyUriParts(
        scheme=scheme,
        host=host,
        port=port,
        username=username,
        password=password,
    )
