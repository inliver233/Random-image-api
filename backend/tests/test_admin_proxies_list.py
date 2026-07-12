from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.db.models.proxy_endpoints import ProxyEndpoint
from app.db.session import create_sessionmaker
from app.main import create_app


def test_admin_list_proxy_endpoints_does_not_echo_password(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_list_proxies.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            session.add_all(
                [
                    ProxyEndpoint(
                        scheme="http",
                        host="1.2.3.4",
                        port=8080,
                        username="u",
                        password_enc="enc_dummy",
                        enabled=1,
                        source="manual",
                    ),
                    ProxyEndpoint(
                        scheme="http",
                        host="9.9.9.9",
                        port=2323,
                        username="inliver",
                        password_enc="enc_dummy2",
                        enabled=1,
                        source="easy_proxies",
                        source_ref="http://user:pw_secret@easy.test:15666",
                    ),
                ]
            )
            await session.commit()

        await app.state.engine.dispose()

    asyncio.run(_seed())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.get(
            "/admin/api/proxies/endpoints",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["request_id"] == "req_test"
        assert resp.headers["X-Request-Id"] == "req_test"
        assert len(body["items"]) == 2

        dumped = json.dumps(body, ensure_ascii=False)
        assert "enc_dummy" not in dumped
        assert "enc_dummy2" not in dumped
        assert "password" not in dumped
        assert "pw_secret" not in dumped

        items = body["items"]
        assert all("source" in item for item in items)
        assert all("source_ref" in item for item in items)

        easy = next(i for i in items if i["source"] == "easy_proxies")
        assert easy["source_ref"] == "http://easy.test:15666"


def test_admin_proxies_endpoints_openapi_documents_control_plane() -> None:
    """Proxy endpoints OpenAPI must document residential control-plane surfaces."""
    app = create_app()
    paths = app.openapi()["paths"]

    list_op = paths["/admin/api/proxies/endpoints"]["get"]
    assert list_op.get("summary") == "List proxy endpoints"
    assert "password" in str(list_op.get("description") or "").lower() or "encrypted" in str(
        list_op.get("description") or ""
    ).lower()

    import_op = paths["/admin/api/proxies/endpoints/import"]["post"]
    assert import_op.get("summary") == "Import proxy endpoints from URI text"
    assert "JobQueue" in str(import_op.get("description") or "") or "inline" in str(
        import_op.get("description") or ""
    ).lower()

    update_op = paths["/admin/api/proxies/endpoints/{endpoint_id}"]["put"]
    assert update_op.get("summary") == "Update proxy endpoint enabled flag"

    reset_op = paths["/admin/api/proxies/endpoints/{endpoint_id}/reset-failures"]["post"]
    assert reset_op.get("summary") == "Reset proxy endpoint failure state"

    cleanup_op = paths["/admin/api/proxies/endpoints/cleanup-invalid-hosts"]["post"]
    assert cleanup_op.get("summary") == "Cleanup proxy endpoints with invalid hosts"
