from __future__ import annotations

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
    api = load_worker_script("api")
    img = load_worker_script("image")
    assert b"PROXY_SECRET" in api or b"allowed" in api.lower() or len(api) > 100
    assert len(img) > 100
