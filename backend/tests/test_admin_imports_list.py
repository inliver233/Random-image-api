from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.main import create_app


def test_admin_list_imports_cursor(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_list_imports.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("IMPORT_INLINE_MAX_ACCEPTED", "0")

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    headers = {"Authorization": f"Bearer {token}", "X-Request-Id": "req_list_imports"}

    with TestClient(app) as client:
        urls = [
            "https://i.pximg.net/img-original/img/2023/01/01/00/00/00/11111111_p0.jpg",
            "https://i.pximg.net/img-original/img/2023/01/01/00/00/00/22222222_p0.jpg",
            "https://i.pximg.net/img-original/img/2023/01/01/00/00/00/33333333_p0.jpg",
        ]
        import_ids: list[int] = []
        for url in urls:
            resp = client.post(
                "/admin/api/imports",
                headers=headers,
                json={"text": url, "dry_run": False, "hydrate_on_import": False, "source": "manual"},
            )
            assert resp.status_code == 200
            import_ids.append(int(resp.json()["import_id"]))

        resp1 = client.get("/admin/api/imports", params={"limit": 2}, headers=headers)
        assert resp1.status_code == 200
        body1 = resp1.json()
        assert body1["ok"] is True
        assert body1["request_id"] == "req_list_imports"
        assert len(body1["items"]) == 2
        assert body1["next_cursor"].isdigit()
        assert int(body1["items"][0]["id"]) > int(body1["items"][1]["id"])
        assert body1["items"][0]["job"] is not None
        assert body1["items"][0]["job"]["type"] == "import_images"

        resp2 = client.get(
            "/admin/api/imports",
            params={"limit": 2, "cursor": body1["next_cursor"]},
            headers=headers,
        )
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["ok"] is True
        assert len(body2["items"]) == 1
        assert body2["next_cursor"] == ""
        assert int(body2["items"][0]["id"]) == min(import_ids)

        bad = client.get("/admin/api/imports", params={"limit": 0}, headers=headers)
        assert bad.status_code == 400


def test_admin_imports_openapi_documents_job_queue_coupling() -> None:
    """Import OpenAPI must document enqueue/CatalogStore coupling (not Title-Case auto only)."""
    app = create_app()
    schema = app.openapi()
    paths = schema["paths"]

    create_op = paths["/admin/api/imports"]["post"]
    assert create_op.get("summary") == "Create import"
    create_desc = str(create_op.get("description") or "")
    assert "enqueue" in create_desc.lower() or "JobQueuePort" in create_desc
    assert "import_images" in create_desc

    list_op = paths["/admin/api/imports"]["get"]
    assert list_op.get("summary") == "List imports"
    assert "import" in str(list_op.get("description") or "").lower()

    get_op = paths["/admin/api/imports/{import_id}"]["get"]
    assert get_op.get("summary") == "Get import detail"

    rb_op = paths["/admin/api/imports/{import_id}/rollback"]["post"]
    assert rb_op.get("summary") == "Rollback import"
    assert "CatalogStore" in str(rb_op.get("description") or "")
