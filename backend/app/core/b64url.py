from __future__ import annotations

import base64


def b64url_encode(raw: bytes) -> str:
    """URL-safe base64 without padding (JWT / imgproxy / image-edge signatures)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode(data: str) -> bytes:
    """URL-safe base64 decode; accepts missing padding."""
    data = data.strip()
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)
