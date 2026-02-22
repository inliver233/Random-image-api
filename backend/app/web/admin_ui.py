from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from starlette.staticfiles import StaticFiles


def mount_admin_ui(app: FastAPI, *, dist_dir: str | Path = "/app/web/dist") -> bool:
    dist_path = Path(dist_dir)
    index_file = dist_path / "index.html"
    if not index_file.is_file():
        return False

    assets_dir = dist_path / "assets"
    if assets_dir.is_dir():
        app.mount(
            "/admin/assets",
            StaticFiles(directory=str(assets_dir)),
            name="admin-assets",
        )

    @app.get("/", include_in_schema=False)
    async def _root() -> RedirectResponse:  # type: ignore[no-redef]
        return RedirectResponse(url="/admin", status_code=302)

    @app.get("/admin", include_in_schema=False)
    async def _admin_index() -> FileResponse:  # type: ignore[no-redef]
        return FileResponse(index_file)

    @app.get("/admin/{path:path}", include_in_schema=False)
    async def _admin_spa(path: str) -> FileResponse:  # type: ignore[no-redef]
        p = (path or "").lstrip("/")
        if p.startswith("api") or p.startswith("assets"):
            raise HTTPException(status_code=404)
        return FileResponse(index_file)

    return True

