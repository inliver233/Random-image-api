from __future__ import annotations

from pathlib import Path

import pytest

from app.core.cf_worker_deploy import (
    CfWorkerDeployError,
    load_worker_script,
    secrets_for_kind,
    validate_account_id,
    validate_api_token,
    validate_worker_name,
)


def test_validate_worker_name() -> None:
    assert validate_worker_name("ria-api-a") == "ria-api-a"
    with pytest.raises(CfWorkerDeployError):
        validate_worker_name("Bad_Name")
    with pytest.raises(CfWorkerDeployError):
        validate_worker_name("-leading")


def test_validate_account_and_token() -> None:
    assert validate_account_id("0123456789abcdef0123456789abcdef") == "0123456789abcdef0123456789abcdef"
    with pytest.raises(CfWorkerDeployError):
        validate_account_id("not-hex")
    assert validate_api_token("cf-token-abcdefghijklmnopqrstuvwxyz")
    with pytest.raises(CfWorkerDeployError):
        validate_api_token("short")


def test_secrets_for_kind_api_requires_proxy_secret() -> None:
    with pytest.raises(CfWorkerDeployError):
        secrets_for_kind("api", proxy_secret="")
    out = secrets_for_kind("api", proxy_secret="s" * 12)
    assert out == {"PROXY_SECRET": "s" * 12}


def test_secrets_for_kind_image_hmac() -> None:
    with pytest.raises(CfWorkerDeployError):
        secrets_for_kind("image", image_edge_secret="")
    out = secrets_for_kind(
        "image",
        image_edge_secret="edge-secret",
        image_edge_secret_previous="prev-secret",
        prewarm_secret="prewarm",
    )
    assert out["IMAGE_EDGE_SECRET"] == "edge-secret"
    assert out["IMAGE_EDGE_SECRET_PREVIOUS"] == "prev-secret"
    assert out["PREWARM_SECRET"] == "prewarm"


def test_load_worker_script_exists() -> None:
    from app.core.cf_worker_deploy import load_worker_pure_script, resolve_worker_pure_path

    api = load_worker_script("api")
    img = load_worker_script("image")
    assert b"PROXY_SECRET" in api or b"allowed" in api.lower() or len(api) > 100
    assert len(img) > 100
    api_pure = load_worker_pure_script("api")
    img_pure = load_worker_pure_script("image")
    assert len(api_pure) > 20
    assert len(img_pure) > 20
    # index.js ES modules import ./pure.js — both must exist next to scripts.
    assert resolve_worker_pure_path("api").name == "pure.js"
    assert resolve_worker_pure_path("image").is_file()


def test_repo_root_env_override(tmp_path: Path, monkeypatch) -> None:
    """EDGE_WORKER_ROOT / REPO_ROOT must win so Docker can pin /app without path heuristics."""
    from app.core.cf_worker_deploy import repo_root_from_backend, resolve_worker_script_path

    edge_src = tmp_path / "edge" / "api-worker" / "src"
    edge_src.mkdir(parents=True)
    (edge_src / "index.js").write_text("export default {}", encoding="utf-8")
    (edge_src / "pure.js").write_text("export {}", encoding="utf-8")
    monkeypatch.setenv("EDGE_WORKER_ROOT", str(tmp_path))
    monkeypatch.delenv("REPO_ROOT", raising=False)
    assert repo_root_from_backend() == tmp_path.resolve()
    assert resolve_worker_script_path("api").is_file()


def test_repo_root_docker_layout_prefers_edge_beside_app(tmp_path: Path, monkeypatch) -> None:
    """Docker image layout: ``/app/app/core/*.py`` + ``/app/edge/**/src`` (parents[2])."""
    from app.core import cf_worker_deploy as mod

    monkeypatch.delenv("EDGE_WORKER_ROOT", raising=False)
    monkeypatch.delenv("REPO_ROOT", raising=False)

    # Simulate WORKDIR /app with COPY backend/app → /app/app and edge → /app/edge
    docker_root = tmp_path / "app"
    core_dir = docker_root / "app" / "core"
    core_dir.mkdir(parents=True)
    fake_file = core_dir / "cf_worker_deploy.py"
    fake_file.write_text("# stub\n", encoding="utf-8")
    edge_src = docker_root / "edge" / "api-worker" / "src"
    edge_src.mkdir(parents=True)
    (edge_src / "index.js").write_text("export default {}", encoding="utf-8")
    (edge_src / "pure.js").write_text("export {}", encoding="utf-8")

    real_path = mod.Path

    class _PathProxy:
        """Path factory that rewrites __file__ resolution to the docker layout fixture."""

        def __call__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            if args and (args[0] is mod.__file__ or args[0] == mod.__file__ or str(args[0]) == str(mod.__file__)):
                return real_path(fake_file)
            return real_path(*args, **kwargs)

        def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
            return getattr(real_path, name)

    monkeypatch.setattr(mod, "Path", _PathProxy())
    root = mod.repo_root_from_backend()
    assert root == docker_root.resolve()
    assert (root / "edge" / "api-worker" / "src" / "index.js").is_file()
    assert mod.resolve_worker_script_path("api", root=root).is_file()


def test_upload_multipart_includes_pure_js() -> None:
    """CF Workers ES modules require pure.js part alongside main module."""
    import asyncio
    import json
    from typing import Any

    from app.core.cf_worker_deploy import _upload_worker_script

    captured: dict[str, Any] = {}

    class _FakeResp:
        status_code = 200
        content = b'{"success":true}'

    class _FakeClient:
        async def put(self, url: str, headers: dict[str, str], files: dict[str, Any], timeout: float = 60.0):
            captured["url"] = url
            captured["headers"] = headers
            captured["files"] = files
            return _FakeResp()

    async def _run() -> None:
        await _upload_worker_script(
            _FakeClient(),  # type: ignore[arg-type]
            api_token="t" * 24,
            account_id="a" * 32,
            worker_name="ria-api-a",
            kind="api",
            script=b"export default { fetch() {} }",
            bindings=[{"type": "secret_text", "name": "PROXY_SECRET", "text": "s" * 12}],
            pure_script=b"export const x = 1;",
        )

    asyncio.run(_run())
    files = captured["files"]
    assert "pure.js" in files
    assert files["pure.js"][0] == "pure.js"
    assert files["pure.js"][1] == b"export const x = 1;"
    assert "api-worker.js" in files
    meta = json.loads(files["metadata"][1])
    assert meta["main_module"] == "api-worker.js"
    assert captured["headers"]["CF-WORKER-MAIN-MODULE-PART"] == "api-worker.js"
