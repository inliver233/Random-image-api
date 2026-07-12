from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.core.cf_pool_overlay import reset_overlay_for_tests
from app.core.cf_worker_deploy import CfWorkerDeployResult
from app.core.security import create_jwt
from app.db.models.base import Base
from app.main import create_app


def _prepare(tmp_path: Path, monkeypatch, *, name: str) -> object:
    db_path = tmp_path / f"{name}.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.delenv("CF_API_PROXY_ENABLED", raising=False)
    monkeypatch.delenv("IMAGE_EDGE_ENABLED", raising=False)
    reset_overlay_for_tests()
    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())
    return app


def test_cf_workers_pool_and_register(tmp_path: Path, monkeypatch) -> None:
    app = _prepare(tmp_path, monkeypatch, name="admin_cf_workers_pool")
    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"}
        resp = client.get("/admin/api/cf-workers/pool", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "api" in body and "image" in body
        assert body["egress_policy"]["residential_egress_emergency_only"] is True
        assert isinstance(body["api"].get("base_cooldown"), list)
        assert isinstance(body["image"].get("base_cooldown"), list)
        assert "30s" in str(body.get("note") or "") or "exponential" in str(body.get("note") or "")

        reg = client.post(
            "/admin/api/cf-workers/register",
            headers=headers,
            json={"kind": "api", "base_url": "https://api-a.example.workers.dev/"},
        )
        assert reg.status_code == 200
        rbody = reg.json()
        assert rbody["registered"] is True
        assert rbody["base_url"] == "https://api-a.example.workers.dev"
        assert "https://api-a.example.workers.dev" in rbody["runtime_base_urls"]

        pool = client.get("/admin/api/cf-workers/pool", headers=headers).json()
        assert "https://api-a.example.workers.dev" in pool["api"]["merged_base_urls"]

        un = client.post(
            "/admin/api/cf-workers/unregister",
            headers=headers,
            json={"kind": "api", "base_url": "https://api-a.example.workers.dev"},
        )
        assert un.status_code == 200
        assert un.json()["unregistered"] is True
    reset_overlay_for_tests()


def test_cf_workers_deploy_registers_without_leaking_token(tmp_path: Path, monkeypatch) -> None:
    result = CfWorkerDeployResult(
        kind="api",
        worker_name="ria-api-a",
        worker_host="ria-api-a.acct.workers.dev",
        base_url="https://ria-api-a.acct.workers.dev",
        secrets_set=["PROXY_SECRET"],
        deployed=True,
    )
    app = _prepare(tmp_path, monkeypatch, name="admin_cf_workers_deploy")
    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"}
        with patch(
            "app.api.admin.cf_workers.deploy_cf_worker",
            new_callable=AsyncMock,
            return_value=result,
        ) as deploy:
            resp = client.post(
                "/admin/api/cf-workers/deploy",
                headers=headers,
                json={
                    "kind": "api",
                    "api_token": "cf-token-abcdefghijklmnopqrstuvwxyz",
                    "account_id": "0123456789abcdef0123456789abcdef",
                    "worker_name": "ria-api-a",
                    "proxy_secret": "proxy-secret-value",
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["deployed"] is True
            assert body["base_url"] == "https://ria-api-a.acct.workers.dev"
            assert body["registered"] is True
            # Living spec: deploy defaults to auto-enable business egress.
            assert body["business_enabled"] is True
            assert body["ready"] is True
            assert "PROXY_SECRET" in body["secrets_set"]
            assert "message" in body and body["message"]
            raw = resp.text
            assert "cf-token-abcdefghijklmnopqrstuvwxyz" not in raw
            assert "proxy-secret-value" not in raw
            deploy.assert_awaited_once()

            # Pool + maintenance status should reflect runtime enable without env flag.
            pool = client.get("/admin/api/cf-workers/pool", headers=headers).json()
            assert pool["api"]["runtime_enabled"] is True
            assert "https://ria-api-a.acct.workers.dev" in pool["api"]["merged_base_urls"]
            status = client.get("/admin/api/maintenance/cf-api-proxy", headers=headers).json()
            assert status["enabled_flag"] is True
            assert status["runtime_enabled_flag"] is True
            assert status["ready"] is True
            assert status["has_secret"] is True
    reset_overlay_for_tests()


def test_cf_workers_deploy_auto_generates_secret_once(tmp_path: Path, monkeypatch) -> None:
    result = CfWorkerDeployResult(
        kind="api",
        worker_name="ria-api-gen",
        worker_host="ria-api-gen.acct.workers.dev",
        base_url="https://ria-api-gen.acct.workers.dev",
        secrets_set=["PROXY_SECRET"],
        deployed=True,
    )
    app = _prepare(tmp_path, monkeypatch, name="admin_cf_workers_deploy_gen_secret")
    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"}
        with patch(
            "app.api.admin.cf_workers.deploy_cf_worker",
            new_callable=AsyncMock,
            return_value=result,
        ) as deploy:
            resp = client.post(
                "/admin/api/cf-workers/deploy",
                headers=headers,
                json={
                    "kind": "api",
                    "api_token": "cf-token-abcdefghijklmnopqrstuvwxyz",
                    "account_id": "0123456789abcdef0123456789abcdef",
                    "worker_name": "ria-api-gen",
                    # no proxy_secret → auto-generate
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["deployed"] is True
            assert body["business_enabled"] is True
            assert body["secret_generated"] is True
            gen = body.get("generated_secret")
            assert isinstance(gen, str) and len(gen) >= 16
            assert "cf-token-abcdefghijklmnopqrstuvwxyz" not in resp.text
            # Deploy helper must receive the generated secret for Worker upload.
            kwargs = deploy.await_args.kwargs if deploy.await_args else {}
            assert kwargs.get("proxy_secret") == gen
            status = client.get("/admin/api/maintenance/cf-api-proxy", headers=headers).json()
            assert status["has_secret"] is True
            assert status["ready"] is True
            # Admin status never echoes the secret value.
            assert gen not in str(status)
    reset_overlay_for_tests()


def test_cf_workers_deploy_enable_business_false_skips_enable(tmp_path: Path, monkeypatch) -> None:
    result = CfWorkerDeployResult(
        kind="api",
        worker_name="ria-api-off",
        worker_host="ria-api-off.acct.workers.dev",
        base_url="https://ria-api-off.acct.workers.dev",
        secrets_set=["PROXY_SECRET"],
        deployed=True,
    )
    app = _prepare(tmp_path, monkeypatch, name="admin_cf_workers_deploy_no_enable")
    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"}
        with patch(
            "app.api.admin.cf_workers.deploy_cf_worker",
            new_callable=AsyncMock,
            return_value=result,
        ):
            resp = client.post(
                "/admin/api/cf-workers/deploy",
                headers=headers,
                json={
                    "kind": "api",
                    "api_token": "cf-token-abcdefghijklmnopqrstuvwxyz",
                    "account_id": "0123456789abcdef0123456789abcdef",
                    "worker_name": "ria-api-off",
                    "proxy_secret": "proxy-secret-value",
                    "enable_business": False,
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["deployed"] is True
            assert body["registered"] is True
            assert body["business_enabled"] is False
            pool = client.get("/admin/api/cf-workers/pool", headers=headers).json()
            assert pool["api"]["runtime_enabled"] is False
            assert "https://ria-api-off.acct.workers.dev" in pool["api"]["merged_base_urls"]
    reset_overlay_for_tests()


def test_cf_workers_deploy_validation(tmp_path: Path, monkeypatch) -> None:
    app = _prepare(tmp_path, monkeypatch, name="admin_cf_workers_validation")
    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"}
        resp = client.post(
            "/admin/api/cf-workers/deploy",
            headers=headers,
            json={
                "kind": "api",
                "api_token": "short",
                "account_id": "not-hex",
                "worker_name": "Bad_Name",
            },
        )
        assert resp.status_code == 400
    reset_overlay_for_tests()


def test_cf_workers_probe_empty_body_and_override(tmp_path: Path, monkeypatch) -> None:
    app = _prepare(tmp_path, monkeypatch, name="admin_cf_workers_probe")
    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    fake_api = [
        {
            "kind": "api",
            "base_url": "https://api-a.example.workers.dev",
            "ok": True,
            "status_code": 200,
            "latency_ms": 12.5,
            "error": None,
            "service": "random-image-api-proxy",
            "secret_configured": True,
            "body_ok": True,
        }
    ]
    fake_img = [
        {
            "kind": "image",
            "base_url": "https://img-a.example.workers.dev",
            "ok": False,
            "status_code": 503,
            "latency_ms": 4.0,
            "error": "status=503",
            "service": None,
            "secret_configured": None,
            "body_ok": None,
        }
    ]
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"}
        with patch(
            "app.api.admin.cf_workers.probe_cf_pool_bases",
            new_callable=AsyncMock,
            side_effect=[fake_api, fake_img],
        ) as probe:
            # Empty body → kind=all, probes both pools (may be empty if no members).
            resp = client.post("/admin/api/cf-workers/probe", headers=headers)
            assert resp.status_code == 200
            body = resp.json()
            assert body["ok"] is True
            assert body["probed"] is True
            assert body["kind"] == "all"
            assert body["api"]["summary"]["total"] == 1
            assert body["image"]["summary"]["fail"] == 1
            assert probe.await_count == 2

        with patch(
            "app.api.admin.cf_workers.probe_cf_pool_bases",
            new_callable=AsyncMock,
            return_value=fake_api,
        ) as probe_one:
            resp2 = client.post(
                "/admin/api/cf-workers/probe",
                headers=headers,
                json={
                    "kind": "api",
                    "base_urls": ["https://api-a.example.workers.dev/"],
                    "timeout_s": 2,
                },
            )
            assert resp2.status_code == 200
            body2 = resp2.json()
            assert body2["kind"] == "api"
            assert body2["api"]["summary"]["ok"] == 1
            assert body2["image"]["summary"]["total"] == 0
            probe_one.assert_awaited_once()
            kwargs = probe_one.await_args.kwargs
            assert kwargs["kind"] == "api"
            assert kwargs["bases"] == ["https://api-a.example.workers.dev"]
            assert kwargs["timeout_s"] == 2.0
    reset_overlay_for_tests()


def test_cf_workers_probe_rejects_bad_kind(tmp_path: Path, monkeypatch) -> None:
    app = _prepare(tmp_path, monkeypatch, name="admin_cf_workers_probe_bad_kind")
    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"}
        resp = client.post(
            "/admin/api/cf-workers/probe",
            headers=headers,
            json={"kind": "bogus"},
        )
        assert resp.status_code == 400
    reset_overlay_for_tests()


def test_cf_workers_probe_rejects_all_with_base_urls_override(tmp_path: Path, monkeypatch) -> None:
    app = _prepare(tmp_path, monkeypatch, name="admin_cf_workers_probe_all_override")
    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"}
        resp = client.post(
            "/admin/api/cf-workers/probe",
            headers=headers,
            json={
                "kind": "all",
                "base_urls": ["https://a.example.workers.dev"],
            },
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body.get("ok") is False
        assert str(body.get("code") or "").upper() == "BAD_REQUEST"
    reset_overlay_for_tests()


def test_cf_workers_egress_policy_force_residential(tmp_path: Path, monkeypatch) -> None:
    from app.core.egress_policy import (
        is_force_residential_emergency,
        reset_force_residential_emergency_for_tests,
    )

    reset_force_residential_emergency_for_tests()
    app = _prepare(tmp_path, monkeypatch, name="admin_cf_workers_egress_force")
    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"}
        get_resp = client.get("/admin/api/cf-workers/egress-policy", headers=headers)
        assert get_resp.status_code == 200
        gbody = get_resp.json()
        assert gbody["ok"] is True
        assert gbody["residential_egress_emergency_only"] is True
        assert gbody["force_residential_emergency"] is False

        set_resp = client.post(
            "/admin/api/cf-workers/egress-policy",
            headers=headers,
            json={"force_residential_emergency": True},
        )
        assert set_resp.status_code == 200
        sbody = set_resp.json()
        assert sbody["updated"] is True
        assert sbody["force_residential_emergency"] is True
        assert sbody["pixiv_api_allows_residential_when_cf_ready"] is True
        assert is_force_residential_emergency() is True

        off = client.post(
            "/admin/api/cf-workers/egress-policy",
            headers=headers,
            json={"force_residential_emergency": False},
        )
        assert off.status_code == 200
        assert off.json()["force_residential_emergency"] is False
        assert is_force_residential_emergency() is False
    reset_force_residential_emergency_for_tests()
    reset_overlay_for_tests()
