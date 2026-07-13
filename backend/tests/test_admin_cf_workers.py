from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from app.core.cf_pool_overlay import reset_overlay_for_tests
from app.core.cf_worker_deploy import CfWorkerDeleteResult, CfWorkerDeployError, CfWorkerDeployResult
from app.core.security import create_jwt
from app.db.cf_worker_deployments import reserve_cf_worker_deployment
from app.db.models.base import Base
from app.db.models.cf_worker_deployments import CfWorkerDeployment
from app.db.session import create_sessionmaker
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
    monkeypatch.setenv("CF_API_PROXY_BASE_URLS", "https://api-a.example.workers.dev")
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


def test_cf_workers_register_rejects_unverified_or_unsafe_base(tmp_path: Path, monkeypatch) -> None:
    app = _prepare(tmp_path, monkeypatch, name="admin_cf_workers_register_reject")
    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"}
        for base_url in (
            "http://127.0.0.1",
            "https://169.254.169.254",
            "https://user:pass@evil.example",
            "https://attacker-owned.example.workers.dev",
        ):
            resp = client.post(
                "/admin/api/cf-workers/register",
                headers=headers,
                json={"kind": "api", "base_url": base_url},
            )
            assert resp.status_code == 400
    assert reset_overlay_for_tests() is None


def test_cf_workers_deploy_api_default_does_not_enable_business(tmp_path: Path, monkeypatch) -> None:
    """API deploy defaults enable_business=false (Pixiv API path does not depend on CF)."""
    result = CfWorkerDeployResult(
        kind="api",
        worker_name="ria-api-a",
        worker_host="ria-api-a.acct.workers.dev",
        base_url="https://ria-api-a.acct.workers.dev",
        secrets_set=["PROXY_SECRET"],
        deployed=True,
    )
    app = _prepare(tmp_path, monkeypatch, name="admin_cf_workers_deploy_api_default")
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
                    # no enable_business → api default false
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["deployed"] is True
            assert body["base_url"] == "https://ria-api-a.acct.workers.dev"
            assert body["registered"] is True
            assert body["business_enabled"] is False
            assert body["ready"] is False
            assert "PROXY_SECRET" in body["secrets_set"]
            assert "message" in body and body["message"]
            raw = resp.text
            assert "cf-token-abcdefghijklmnopqrstuvwxyz" not in raw
            assert "proxy-secret-value" not in raw
            deploy.assert_awaited_once()

            pool = client.get("/admin/api/cf-workers/pool", headers=headers).json()
            assert pool["api"]["runtime_enabled"] is False
            assert "https://ria-api-a.acct.workers.dev" in pool["api"]["merged_base_urls"]
            status = client.get("/admin/api/maintenance/cf-api-proxy", headers=headers).json()
            assert status["enabled_flag"] is False
            assert status["runtime_enabled_flag"] is False
            assert status["ready"] is False
    reset_overlay_for_tests()


def test_cf_workers_deploy_api_opt_in_enable_business(tmp_path: Path, monkeypatch) -> None:
    """API path can still enable CF proxy when ops sets enable_business=true."""
    result = CfWorkerDeployResult(
        kind="api",
        worker_name="ria-api-on",
        worker_host="ria-api-on.acct.workers.dev",
        base_url="https://ria-api-on.acct.workers.dev",
        secrets_set=["PROXY_SECRET"],
        deployed=True,
    )
    app = _prepare(tmp_path, monkeypatch, name="admin_cf_workers_deploy_api_opt_in")
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
                    "worker_name": "ria-api-on",
                    "proxy_secret": "proxy-secret-value",
                    "enable_business": True,
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["deployed"] is True
            assert body["registered"] is True
            assert body["business_enabled"] is True
            assert body["ready"] is True
            raw = resp.text
            assert "cf-token-abcdefghijklmnopqrstuvwxyz" not in raw
            assert "proxy-secret-value" not in raw
            deploy.assert_awaited_once()

            pool = client.get("/admin/api/cf-workers/pool", headers=headers).json()
            assert pool["api"]["runtime_enabled"] is True
            status = client.get("/admin/api/maintenance/cf-api-proxy", headers=headers).json()
            assert status["enabled_flag"] is True
            assert status["runtime_enabled_flag"] is True
            assert status["ready"] is True
            assert status["has_secret"] is True
    reset_overlay_for_tests()


def test_cf_workers_deploy_image_default_enables_business(tmp_path: Path, monkeypatch) -> None:
    """Image deploy defaults enable_business=true (public Image Edge)."""
    result = CfWorkerDeployResult(
        kind="image",
        worker_name="ria-img-a",
        worker_host="ria-img-a.acct.workers.dev",
        base_url="https://ria-img-a.acct.workers.dev",
        secrets_set=["IMAGE_EDGE_SECRET"],
        deployed=True,
    )
    app = _prepare(tmp_path, monkeypatch, name="admin_cf_workers_deploy_image_default")
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
                    "kind": "image",
                    "api_token": "cf-token-abcdefghijklmnopqrstuvwxyz",
                    "account_id": "0123456789abcdef0123456789abcdef",
                    "worker_name": "ria-img-a",
                    "image_edge_secret": "image-edge-secret-value",
                    # no enable_business → image default true
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["deployed"] is True
            assert body["registered"] is True
            assert body["business_enabled"] is True
            assert body["ready"] is True
            assert "IMAGE_EDGE_SECRET" in body["secrets_set"]
            raw = resp.text
            assert "cf-token-abcdefghijklmnopqrstuvwxyz" not in raw
            assert "image-edge-secret-value" not in raw
            deploy.assert_awaited_once()

            pool = client.get("/admin/api/cf-workers/pool", headers=headers).json()
            assert pool["image"]["runtime_enabled"] is True
            assert "https://ria-img-a.acct.workers.dev" in pool["image"]["merged_base_urls"]
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
                    "enable_business": True,
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


def test_cf_workers_deploy_image_enable_business_false_skips_enable(tmp_path: Path, monkeypatch) -> None:
    result = CfWorkerDeployResult(
        kind="image",
        worker_name="ria-img-off",
        worker_host="ria-img-off.acct.workers.dev",
        base_url="https://ria-img-off.acct.workers.dev",
        secrets_set=["IMAGE_EDGE_SECRET"],
        deployed=True,
    )
    app = _prepare(tmp_path, monkeypatch, name="admin_cf_workers_deploy_image_no_enable")
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
                    "kind": "image",
                    "api_token": "cf-token-abcdefghijklmnopqrstuvwxyz",
                    "account_id": "0123456789abcdef0123456789abcdef",
                    "worker_name": "ria-img-off",
                    "image_edge_secret": "image-edge-secret-value",
                    "enable_business": False,
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["deployed"] is True
            assert body["registered"] is True
            assert body["business_enabled"] is False
            pool = client.get("/admin/api/cf-workers/pool", headers=headers).json()
            assert pool["image"]["runtime_enabled"] is False
            assert "https://ria-img-off.acct.workers.dev" in pool["image"]["merged_base_urls"]
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


def test_cf_workers_deploy_rejects_cross_kind_name_before_cloudflare(tmp_path: Path, monkeypatch) -> None:
    result = CfWorkerDeployResult(
        kind="api",
        worker_name="shared-name",
        worker_host="shared-name.acct.workers.dev",
        base_url="https://shared-name.acct.workers.dev",
        secrets_set=["PROXY_SECRET"],
        deployed=True,
    )
    app = _prepare(tmp_path, monkeypatch, name="admin_cf_workers_cross_kind")
    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"}
        with patch(
            "app.api.admin.cf_workers.deploy_cf_worker",
            new_callable=AsyncMock,
            return_value=result,
        ) as deploy:
            first = client.post(
                "/admin/api/cf-workers/deploy",
                headers=headers,
                json={
                    "kind": "api",
                    "api_token": "cf-token-abcdefghijklmnopqrstuvwxyz",
                    "account_id": "0123456789abcdef0123456789abcdef",
                    "worker_name": "shared-name",
                    "proxy_secret": "proxy-secret-value",
                },
            )
            assert first.status_code == 200
            assert first.json()["deployment_state"] == "deployed"
            same_kind = client.post(
                "/admin/api/cf-workers/deploy",
                headers=headers,
                json={
                    "kind": "api",
                    "api_token": "cf-token-abcdefghijklmnopqrstuvwxyz",
                    "account_id": "0123456789abcdef0123456789abcdef",
                    "worker_name": "shared-name",
                    "proxy_secret": "different-secret-value",
                },
            )
            assert same_kind.status_code == 409
            second = client.post(
                "/admin/api/cf-workers/deploy",
                headers=headers,
                json={
                    "kind": "image",
                    "api_token": "cf-token-abcdefghijklmnopqrstuvwxyz",
                    "account_id": "0123456789abcdef0123456789abcdef",
                    "worker_name": "shared-name",
                    "image_edge_secret": "image-secret-value",
                },
            )
            assert second.status_code == 409
            assert deploy.await_count == 1

    async def _assert_state() -> None:
        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            row = await session.get(CfWorkerDeployment, 1)
            assert row is not None
            assert row.kind == "api"
            assert row.state == "deployed"

    asyncio.run(_assert_state())
    reset_overlay_for_tests()


def test_cf_workers_deploy_failure_leaves_durable_failed_intent(tmp_path: Path, monkeypatch) -> None:
    app = _prepare(tmp_path, monkeypatch, name="admin_cf_workers_failed_intent")
    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"}
        with patch(
            "app.api.admin.cf_workers.deploy_cf_worker",
            new_callable=AsyncMock,
            side_effect=CfWorkerDeployError("upload failed", status_code=502),
        ):
            resp = client.post(
                "/admin/api/cf-workers/deploy",
                headers=headers,
                json={
                    "kind": "api",
                    "api_token": "cf-token-abcdefghijklmnopqrstuvwxyz",
                    "account_id": "0123456789abcdef0123456789abcdef",
                    "worker_name": "failed-name",
                    "proxy_secret": "proxy-secret-value",
                },
            )
            assert resp.status_code == 502

    async def _assert_state() -> None:
        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            row = await session.get(CfWorkerDeployment, 1)
            assert row is not None
            assert row.state == "failed"
            assert row.last_error == "upload failed"

    asyncio.run(_assert_state())
    reset_overlay_for_tests()


def test_cf_workers_transport_failure_is_retryable(tmp_path: Path, monkeypatch) -> None:
    app = _prepare(tmp_path, monkeypatch, name="admin_cf_workers_transport_failed")
    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"}
        with patch(
            "app.api.admin.cf_workers.deploy_cf_worker",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("Bearer transport-secret api_token=token-secret"),
        ):
            resp = client.post(
                "/admin/api/cf-workers/deploy",
                headers=headers,
                json={
                    "kind": "api",
                    "api_token": "cf-token-abcdefghijklmnopqrstuvwxyz",
                    "account_id": "0123456789abcdef0123456789abcdef",
                    "worker_name": "transport-failed",
                    "proxy_secret": "proxy-secret-value",
                },
            )
            assert resp.status_code == 502
            assert "transport-secret" not in resp.text
            assert "token-secret" not in resp.text

    async def _assert_retry() -> None:
        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            row = await session.get(CfWorkerDeployment, 1)
            assert row is not None
            assert row.state == "failed"
            assert "transport-secret" not in str(row.last_error)
            assert "token-secret" not in str(row.last_error)
        deployment_id, version = await reserve_cf_worker_deployment(
            app.state.engine,
            account_id="0123456789abcdef0123456789abcdef",
            worker_name="transport-failed",
            kind="api",
        )
        assert deployment_id == 1
        assert version == 2

    asyncio.run(_assert_retry())
    reset_overlay_for_tests()


def test_cf_workers_probe_empty_body_and_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CF_API_PROXY_BASE_URLS", "https://api-a.example.workers.dev")
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


def test_cf_workers_probe_rejects_untrusted_override(tmp_path: Path, monkeypatch) -> None:
    app = _prepare(tmp_path, monkeypatch, name="admin_cf_workers_probe_untrusted")
    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"}
        with patch("app.api.admin.cf_workers.probe_cf_pool_bases", new_callable=AsyncMock) as probe:
            resp = client.post(
                "/admin/api/cf-workers/probe",
                headers=headers,
                json={
                    "kind": "api",
                    "base_urls": ["https://attacker-owned.example.workers.dev"],
                },
            )
        assert resp.status_code == 400
        probe.assert_not_awaited()
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


def test_cf_workers_delete_script_and_unregister_pool(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CF_API_PROXY_BASE_URLS", "https://ria-api-a.acct.workers.dev")
    app = _prepare(tmp_path, monkeypatch, name="admin_cf_workers_delete_script")
    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    del_result = CfWorkerDeleteResult(worker_name="ria-api-a", deleted=True, already_absent=False)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"}
        reg = client.post(
            "/admin/api/cf-workers/register",
            headers=headers,
            json={"kind": "api", "base_url": "https://ria-api-a.acct.workers.dev"},
        )
        assert reg.status_code == 200
        with patch(
            "app.api.admin.cf_workers.delete_cf_worker_script",
            new_callable=AsyncMock,
            return_value=del_result,
        ) as delete_fn:
            resp = client.post(
                "/admin/api/cf-workers/delete-script",
                headers=headers,
                json={
                    "kind": "api",
                    "api_token": "cf-token-abcdefghijklmnopqrstuvwxyz",
                    "account_id": "0123456789abcdef0123456789abcdef",
                    "worker_name": "ria-api-a",
                    "base_url": "https://ria-api-a.acct.workers.dev",
                    "unregister_pool": True,
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["ok"] is True
            assert body["deleted"] is True
            assert body["already_absent"] is False
            assert body["unregistered"] is True
            assert body["base_url"] == "https://ria-api-a.acct.workers.dev"
            raw = resp.text
            assert "cf-token-abcdefghijklmnopqrstuvwxyz" not in raw
            delete_fn.assert_awaited_once()

        pool = client.get("/admin/api/cf-workers/pool", headers=headers).json()
        assert "https://ria-api-a.acct.workers.dev" not in pool["api"]["runtime_base_urls"]
        assert "https://ria-api-a.acct.workers.dev" in pool["api"]["merged_base_urls"]
    reset_overlay_for_tests()


def test_cf_workers_delete_script_already_absent(tmp_path: Path, monkeypatch) -> None:
    app = _prepare(tmp_path, monkeypatch, name="admin_cf_workers_delete_absent")
    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    del_result = CfWorkerDeleteResult(worker_name="gone-worker", deleted=False, already_absent=True)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"}
        with patch(
            "app.api.admin.cf_workers.delete_cf_worker_script",
            new_callable=AsyncMock,
            return_value=del_result,
        ):
            resp = client.post(
                "/admin/api/cf-workers/delete-script",
                headers=headers,
                json={
                    "api_token": "cf-token-abcdefghijklmnopqrstuvwxyz",
                    "account_id": "0123456789abcdef0123456789abcdef",
                    "worker_name": "gone-worker",
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["deleted"] is False
            assert body["already_absent"] is True
            assert body["unregistered"] is False
    reset_overlay_for_tests()
