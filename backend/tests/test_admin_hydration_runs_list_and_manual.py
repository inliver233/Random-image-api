from __future__ import annotations

import asyncio
import json
from pathlib import Path

import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.db.models.hydration_runs import HydrationRun
from app.db.models.images import Image
from app.db.models.jobs import JobRow
from app.db.session import create_sessionmaker
from app.main import create_app


def test_admin_list_hydration_runs_and_detail(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_hydration_runs_list.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()
    ids: dict[str, int] = {}

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            run1 = HydrationRun(
                type="backfill",
                status="running",
                criteria_json=json.dumps({"missing": ["geometry", "tags"]}, separators=(",", ":"), ensure_ascii=False),
                cursor_json=json.dumps({"image_id": 1001}, separators=(",", ":"), ensure_ascii=False),
                total=None,
                processed=12,
                success=10,
                failed=2,
                started_at="2026-02-13T01:00:00Z",
                finished_at=None,
                last_error="sample_error",
                updated_at="2026-02-13T01:10:00Z",
            )
            run2 = HydrationRun(
                type="manual",
                status="completed",
                criteria_json=json.dumps({}, separators=(",", ":"), ensure_ascii=False),
                cursor_json=None,
                total=1,
                processed=1,
                success=1,
                failed=0,
                started_at="2026-02-13T02:00:00Z",
                finished_at="2026-02-13T02:00:10Z",
                last_error=None,
                updated_at="2026-02-13T02:00:10Z",
            )
            session.add_all([run1, run2])
            await session.flush()

            job1 = JobRow(
                type="hydrate_metadata",
                status="running",
                priority=0,
                run_after=None,
                attempt=1,
                max_attempts=3,
                payload_json=json.dumps({"hydration_run_id": int(run1.id)}, separators=(",", ":"), ensure_ascii=False),
                last_error=None,
                locked_by="worker_1",
                locked_at="2026-02-13T01:00:01Z",
                ref_type="hydration_run",
                ref_id=str(int(run1.id)),
                updated_at="2026-02-13T01:10:00Z",
            )
            job2 = JobRow(
                type="hydrate_metadata",
                status="completed",
                priority=0,
                run_after=None,
                attempt=1,
                max_attempts=3,
                payload_json=json.dumps({"hydration_run_id": int(run2.id)}, separators=(",", ":"), ensure_ascii=False),
                last_error=None,
                locked_by=None,
                locked_at=None,
                ref_type="hydration_run",
                ref_id=str(int(run2.id)),
                updated_at="2026-02-13T02:00:10Z",
            )
            session.add_all([job1, job2])
            await session.commit()

            nonlocal ids
            ids = {"run1": int(run1.id), "run2": int(run2.id)}

    asyncio.run(_seed())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        listed = client.get(
            "/admin/api/hydration-runs",
            params={"limit": 10},
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_list"},
        )
        assert listed.status_code == 200
        body = listed.json()
        assert body["ok"] is True
        assert body["next_cursor"] == ""
        assert body["request_id"] == "req_list"
        assert isinstance(body["items"], list)
        assert len(body["items"]) == 2

        items_by_id = {str(item["id"]): item for item in body["items"]}
        run1_item = items_by_id[str(ids["run1"])]
        assert run1_item["criteria"] == {"missing": ["geometry", "tags"]}
        assert run1_item["cursor"] == {"image_id": 1001}
        assert run1_item["latest_job"]["status"] == "running"

        detail = client.get(
            f"/admin/api/hydration-runs/{ids['run1']}",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_detail"},
        )
        assert detail.status_code == 200
        item = detail.json()["item"]
        assert item["id"] == str(ids["run1"])
        assert item["status"] == "running"
        assert item["latest_job"]["locked_by"] == "worker_1"


def test_admin_create_manual_hydration_job_by_image_or_illust(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_hydration_manual.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()
    seeded_image_id = 0

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            image = Image(
                illust_id=987654,
                page_index=0,
                ext="jpg",
                original_url="https://i.pximg.net/img-original/img/2026/02/14/00/00/00/987654_p0.jpg",
                proxy_path="/i/1.jpg",
                random_key=0.1234,
                status=1,
            )
            session.add(image)
            await session.commit()
            await session.refresh(image)

            nonlocal seeded_image_id
            seeded_image_id = int(image.id)

    asyncio.run(_seed())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        created_by_image = client.post(
            "/admin/api/hydration-runs/manual",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_m1"},
            json={"image_id": seeded_image_id},
        )
        assert created_by_image.status_code == 200
        body1 = created_by_image.json()
        assert body1["ok"] is True
        assert body1["created"] is True
        assert body1["illust_id"] == "987654"
        assert body1["job_id"].isdigit()

        duplicate = client.post(
            "/admin/api/hydration-runs/manual",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_m2"},
            json={"illust_id": 987654},
        )
        assert duplicate.status_code == 200
        body2 = duplicate.json()
        assert body2["ok"] is True
        assert body2["created"] is False
        assert body2["illust_id"] == "987654"

    async def _verify() -> None:
        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            rows = (
                (
                    await session.execute(
                        sa.select(JobRow)
                        .where(JobRow.ref_type == "manual_hydrate", JobRow.ref_id == "987654")
                        .order_by(JobRow.id.asc())
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1
            payload = json.loads(rows[0].payload_json)
            assert payload["illust_id"] == 987654
            assert rows[0].status == "pending"

    asyncio.run(_verify())
