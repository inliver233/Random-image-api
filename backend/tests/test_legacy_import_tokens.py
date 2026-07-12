from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text

# Script lives outside backend package; load by path for unit tests.
_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "legacy" / "migrate_legacy_catalog.py"
if str(_REPO / "backend") not in sys.path:
    sys.path.insert(0, str(_REPO / "backend"))
if str(_SCRIPT.parent) not in sys.path:
    sys.path.insert(0, str(_SCRIPT.parent))

import migrate_legacy_catalog as mig  # noqa: E402


def test_parse_refresh_token_payload_list_and_objects() -> None:
    items = mig.parse_refresh_token_payload(
        [
            "rt_plain_one",
            {"refresh_token": "rt_two", "label": "acc-b", "enabled": False, "weight": 2.5},
            {"token": "rt_three", "label": "acc-c"},
            "",
            {"label": "skip-me"},
        ]
    )
    assert [i["refresh_token"] for i in items] == ["rt_plain_one", "rt_two", "rt_three"]
    assert items[0]["label"] == "legacy-1"
    assert items[1]["label"] == "acc-b"
    assert items[1]["enabled"] is False
    assert items[1]["weight"] == 2.5
    assert items[2]["enabled"] is True


def test_parse_refresh_token_payload_env_shapes() -> None:
    wrapped = mig.parse_refresh_token_payload({"tokens": ["a", {"refresh_token": "b", "label": "x"}]})
    assert [i["refresh_token"] for i in wrapped] == ["a", "b"]
    assert wrapped[1]["label"] == "x"

    csv_shape = mig.parse_refresh_token_payload("rt_a, rt_b\nrt_c")
    assert [i["refresh_token"] for i in csv_shape] == ["rt_a", "rt_b", "rt_c"]

    json_str = mig.parse_refresh_token_payload(json.dumps(["rt_j1", "rt_j2"]))
    assert [i["refresh_token"] for i in json_str] == ["rt_j1", "rt_j2"]


def test_load_from_env_and_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REFRESH_TOKENS", json.dumps(["env_rt"]))
    from_env = mig.load_refresh_token_items(from_env="REFRESH_TOKENS")
    assert from_env[0]["refresh_token"] == "env_rt"

    p = tmp_path / "tokens.json"
    p.write_text(json.dumps([{"refresh_token": "file_rt", "label": "f1"}]), encoding="utf-8")
    from_file = mig.load_refresh_token_items(tokens_json=str(p))
    assert from_file[0]["label"] == "f1"

    with pytest.raises(ValueError, match="only one"):
        mig.load_refresh_token_items(tokens_json=str(p), from_env="REFRESH_TOKENS")

    with pytest.raises(ValueError, match="not set"):
        mig.load_refresh_token_items(from_env="MISSING_TOKENS_VAR_XYZ")


def test_cmd_import_tokens_dry_run_and_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from app.core.crypto import FieldEncryptor
    from app.db.models.base import Base

    field_key = Fernet.generate_key().decode("ascii")
    db_path = tmp_path / "tokens.db"
    url = f"sqlite:///{db_path.as_posix()}"
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)

    tokens_path = tmp_path / "t.json"
    tokens_path.write_text(
        json.dumps(
            [
                "rt_secret_alpha",
                {"refresh_token": "rt_secret_beta", "label": "beta", "weight": 3},
            ]
        ),
        encoding="utf-8",
    )

    ns = type(
        "NS",
        (),
        {
            "tokens_json": str(tokens_path),
            "from_env": "",
            "target_url": "",
            "field_encryption_key": "",
            "dry_run": True,
        },
    )()
    assert mig.cmd_import_tokens(ns) == 0
    out = capsys.readouterr().out
    assert "dry-run: 2 token(s)" in out
    assert "rt_secret_alpha" not in out  # only short preview prefix
    assert "rt_secr" in out or "token=" in out

    ns_write = type(
        "NS",
        (),
        {
            "tokens_json": str(tokens_path),
            "from_env": "",
            "target_url": url,
            "field_encryption_key": field_key,
            "dry_run": False,
        },
    )()
    assert mig.cmd_import_tokens(ns_write) == 0

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT label, enabled, refresh_token_enc, refresh_token_masked, weight FROM pixiv_tokens ORDER BY id")
        ).fetchall()
    assert len(rows) == 2
    assert rows[0][0] == "legacy-1"
    assert rows[1][0] == "beta"
    assert rows[0][3] == "***"
    assert rows[1][3] == "***"
    assert FieldEncryptor.from_key(field_key).decrypt_text(rows[0][2]) == "rt_secret_alpha"
    assert FieldEncryptor.from_key(field_key).decrypt_text(rows[1][2]) == "rt_secret_beta"
    assert float(rows[1][4]) == 3.0

    dumped = capsys.readouterr().out
    assert "rt_secret_alpha" not in dumped
    assert "rt_secret_beta" not in dumped
