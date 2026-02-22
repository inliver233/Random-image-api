from __future__ import annotations

from typing import Any

from fastapi import Request

from app.core.security import require_admin


def get_admin_claims(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    return require_admin(
        request.headers,
        secret_key=settings.secret_key,
        admin_username=settings.admin_username,
    )

