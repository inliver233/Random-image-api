from __future__ import annotations

import asyncio
from pathlib import Path

import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.db.models.images import Image
from app.db.models.imports import Import
from app.db.models.jobs import JobRow
from app.main import create_app
from app.worker import build_default_dispatcher, poll_and_execute_jobs


def test_admin_imports_json_happy_path(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_imports.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        text = "\n".join(
            [
                "https://i.pximg.net/img-original/img/2023/01/01/00/00/00/12345678_p0.jpg",
                "https://i.pximg.net/img-original/img/2023/01/01/00/00/00/12345678_p0.jpg",
                "https://i.pximg.net/img-original/img/2023/01/01/00/00/00/12345678_p1.png",
                "https://www.pixiv.net/artworks/12345678",
            ]
        )

        resp = client.post(
            "/admin/api/imports",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"text": text, "dry_run": False, "hydrate_on_import": False, "source": "manual"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["accepted"] == 2
        assert body["deduped"] == 1
        assert len(body["errors"]) == 1
        assert body["import_id"].isdigit()
        assert body["job_id"].isdigit()
        executed_inline = bool(body.get("executed_inline"))
        assert body["request_id"] == "req_test"
        assert resp.headers["X-Request-Id"] == "req_test"

        if not executed_inline:
            async def _run_worker() -> None:
                dispatcher = build_default_dispatcher(app.state.engine)
                ran = await poll_and_execute_jobs(app.state.engine, dispatcher, worker_id="test-worker", max_jobs=10)
                assert ran >= 1

            asyncio.run(_run_worker())

        async def _fetch_import_counts() -> tuple[int, int, int, int]:
            async with app.state.engine.connect() as conn:
                result = await conn.exec_driver_sql("SELECT total, accepted, success, failed FROM imports")
                row = result.fetchone()
                assert row is not None
                return (int(row[0]), int(row[1]), int(row[2]), int(row[3]))

        assert asyncio.run(_fetch_import_counts()) == (4, 2, 2, 1)


def test_admin_imports_multipart_happy_path(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_imports_multipart.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        text = "\n".join(
            [
                "https://i.pximg.net/img-original/img/2023/01/01/00/00/00/12345678_p0.jpg",
                "https://i.pximg.net/img-original/img/2023/01/01/00/00/00/12345678_p0.jpg",
                "https://i.pximg.net/img-original/img/2023/01/01/00/00/00/12345678_p1.png",
                "https://www.pixiv.net/artworks/12345678",
            ]
        )

        resp = client.post(
            "/admin/api/imports",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            data={"dry_run": "false", "hydrate_on_import": "false", "source": "manual"},
            files={"file": ("urls.txt", text.encode("utf-8"), "text/plain")},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["accepted"] == 2
        assert body["deduped"] == 1
        assert len(body["errors"]) == 1
        assert body["import_id"].isdigit()
        assert body["job_id"].isdigit()
        executed_inline = bool(body.get("executed_inline"))
        assert body["request_id"] == "req_test"
        assert resp.headers["X-Request-Id"] == "req_test"

        import_id = int(body["import_id"])

        if not executed_inline:
            async def _run_worker() -> None:
                dispatcher = build_default_dispatcher(app.state.engine)
                ran = await poll_and_execute_jobs(app.state.engine, dispatcher, worker_id="test-worker", max_jobs=10)
                assert ran >= 1

            asyncio.run(_run_worker())

        async def _fetch_import_counts() -> tuple[int, int, int, int]:
            async with app.state.engine.connect() as conn:
                result = await conn.exec_driver_sql(
                    "SELECT total, accepted, success, failed FROM imports WHERE id = ?",
                    (import_id,),
                )
                row = result.fetchone()
                assert row is not None
                return (int(row[0]), int(row[1]), int(row[2]), int(row[3]))

    assert asyncio.run(_fetch_import_counts()) == (4, 2, 2, 1)


def test_admin_imports_multipart_pixiv_batch_downloader_json_imports_metadata(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_imports_pbd_json.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        payload = (
            b"["
            + b"{"
            + b"\"id\":\"12345678_p0\","
            + b"\"index\":0,"
            + b"\"original\":\"https://i.pximg.net/img-original/img/2023/01/01/00/00/00/12345678_p0.jpg\","
            + b"\"fullWidth\":1000,\"fullHeight\":1500,"
            + b"\"xRestrict\":0,\"aiType\":1,\"type\":0,"
            + b"\"userId\":\"42\",\"user\":\"alice\",\"title\":\"hello\","
            + b"\"date\":\"2023-01-01T00:00:00+09:00\","
            + b"\"bmk\":12,\"viewCount\":34,\"commentCount\":5,"
            + b"\"tags\":[\"loli\",\"cute\"]"
            + b"}"
            + b"]"
        )

        resp = client.post(
            "/admin/api/imports",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            data={"dry_run": "false", "hydrate_on_import": "true", "source": "manual"},
            files={"file": ("result.json", payload, "application/json")},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["accepted"] == 1
        assert body["deduped"] == 0
        assert body["errors"] == []
        assert body["import_id"].isdigit()
        assert body["job_id"].isdigit()
        executed_inline = bool(body.get("executed_inline"))

        import_id = int(body["import_id"])

        if not executed_inline:
            async def _run_worker() -> None:
                dispatcher = build_default_dispatcher(app.state.engine)
                ran = await poll_and_execute_jobs(app.state.engine, dispatcher, worker_id="test-worker", max_jobs=10)
                assert ran >= 1

            asyncio.run(_run_worker())

        async def _fetch_image_and_tag_counts() -> tuple[int, int, int]:
            async with app.state.engine.connect() as conn:
                img = await conn.exec_driver_sql(
                    "SELECT width,height,orientation,ai_type,x_restrict,user_id,bookmark_count FROM images WHERE illust_id=12345678 AND page_index=0"
                )
                row = img.fetchone()
                assert row is not None
                tag_count = int((await conn.exec_driver_sql("SELECT COUNT(*) FROM tags")).scalar_one())
                link_count = int((await conn.exec_driver_sql("SELECT COUNT(*) FROM image_tags")).scalar_one())
                return (int(row[0]), int(row[1]), tag_count, link_count)

        assert asyncio.run(_fetch_image_and_tag_counts()) == (1000, 1500, 2, 2)

        async def _hydrate_job_count() -> int:
            async with app.state.engine.connect() as conn:
                return int((await conn.exec_driver_sql("SELECT COUNT(*) FROM jobs WHERE type='hydrate_metadata'")).scalar_one())

        assert asyncio.run(_hydrate_job_count()) == 0

        async def _fetch_import_counts() -> tuple[int, int, int, int]:
            async with app.state.engine.connect() as conn:
                result = await conn.exec_driver_sql(
                    "SELECT total, accepted, success, failed FROM imports WHERE id = ?",
                    (import_id,),
                )
                row = result.fetchone()
                assert row is not None
                return (int(row[0]), int(row[1]), int(row[2]), int(row[3]))

        assert asyncio.run(_fetch_import_counts()) == (1, 1, 1, 0)


def test_admin_imports_dry_run_preview_does_not_write_db(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_imports_dry_run.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        text = "\n".join(
            [
                "https://i.pximg.net/img-original/img/2023/01/01/00/00/00/12345678_p0.jpg",
                "https://i.pximg.net/img-original/img/2023/01/01/00/00/00/12345678_p0.jpg",
                "https://i.pximg.net/img-original/img/2023/01/01/00/00/00/12345678_p1.png",
                "https://www.pixiv.net/artworks/12345678",
            ]
        )

        resp = client.post(
            "/admin/api/imports",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"text": text, "dry_run": True, "hydrate_on_import": False, "source": "manual"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["accepted"] == 2
        assert body["deduped"] == 1
        assert len(body["errors"]) == 1
        assert len(body["preview"]) == 2
        assert body["import_id"] == ""
        assert body["job_id"] == ""
        assert body["request_id"] == "req_test"

        async def _count_images() -> int:
            async with app.state.engine.connect() as conn:
                result = await conn.exec_driver_sql("SELECT COUNT(*) FROM images")
                return int(result.scalar_one())

        assert asyncio.run(_count_images()) == 0


def test_admin_imports_rollback_disable_and_delete(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_imports_rollback.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        text = "\n".join(
            [
                "https://i.pximg.net/img-original/img/2023/01/01/00/00/00/12345678_p0.jpg",
                "https://i.pximg.net/img-original/img/2023/01/01/00/00/00/12345678_p1.png",
            ]
        )

        create_resp = client.post(
            "/admin/api/imports",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"text": text, "dry_run": False, "hydrate_on_import": False, "source": "manual"},
        )
        assert create_resp.status_code == 200
        create_body = create_resp.json()
        import_id = int(create_body["import_id"])
        executed_inline = bool(create_body.get("executed_inline"))

        if not executed_inline:
            async def _run_worker() -> None:
                dispatcher = build_default_dispatcher(app.state.engine)
                ran = await poll_and_execute_jobs(app.state.engine, dispatcher, worker_id="test-worker", max_jobs=10)
                assert ran >= 1

            asyncio.run(_run_worker())

        disable_resp = client.post(
            f"/admin/api/imports/{import_id}/rollback",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"mode": "disable"},
        )
        assert disable_resp.status_code == 200
        assert disable_resp.json()["updated"] == 2

        async def _count_status(status: int) -> int:
            async with app.state.engine.connect() as conn:
                result = await conn.exec_driver_sql(
                    "SELECT COUNT(*) FROM images WHERE created_import_id = ? AND status = ?",
                    (import_id, status),
                )
                return int(result.scalar_one())

        assert asyncio.run(_count_status(2)) == 2

        delete_resp = client.post(
            f"/admin/api/imports/{import_id}/rollback",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"mode": "delete"},
        )
        assert delete_resp.status_code == 200
        assert delete_resp.json()["updated"] == 2
        assert asyncio.run(_count_status(4)) == 2


def test_admin_imports_duplicate_rollback_preserves_owner_and_syncs_engine(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_imports_duplicate_rollback.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    published_upserts: list[list[int]] = []
    published_deletes: list[list[int]] = []

    async def fake_upserts(_engine, *, image_ids, **_kwargs):  # type: ignore[no-untyped-def]
        published_upserts.append([int(v) for v in image_ids])
        return {"ok": True}

    async def fake_deletes(*, image_ids, **_kwargs):  # type: ignore[no-untyped-def]
        published_deletes.append([int(v) for v in image_ids])
        return {"ok": True}

    monkeypatch.setattr("app.api.admin.imports.maybe_publish_engine_upserts", fake_upserts, raising=False)
    monkeypatch.setattr("app.api.admin.imports.maybe_publish_engine_deletes", fake_deletes, raising=False)

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())
    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    headers = {"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"}
    url = "https://i.pximg.net/img-original/img/2023/01/01/00/00/00/987654_p0.jpg"

    with TestClient(app) as client:
        first = client.post(
            "/admin/api/imports",
            headers=headers,
            json={"text": url, "dry_run": False, "hydrate_on_import": False, "source": "first"},
        )
        second = client.post(
            "/admin/api/imports",
            headers=headers,
            json={"text": url, "dry_run": False, "hydrate_on_import": False, "source": "second"},
        )
        assert first.status_code == 200
        assert second.status_code == 200
        first_id = int(first.json()["import_id"])
        second_id = int(second.json()["import_id"])

        async def _image_state() -> tuple[int, int, int]:
            async with app.state.sessionmaker() as session:
                image = (await session.execute(sa.select(Image).where(Image.illust_id == 987654))).scalar_one()
                return int(image.id), int(image.created_import_id or 0), int(image.status)

        image_id, owner_id, status = asyncio.run(_image_state())
        assert owner_id == first_id
        assert status == 1

        duplicate_rollback = client.post(
            f"/admin/api/imports/{second_id}/rollback",
            headers=headers,
            json={"mode": "disable"},
        )
        assert duplicate_rollback.status_code == 200
        assert duplicate_rollback.json()["updated"] == 0
        assert asyncio.run(_image_state()) == (image_id, first_id, 1)
        assert published_upserts == []
        assert published_deletes == []

        owner_disable = client.post(
            f"/admin/api/imports/{first_id}/rollback",
            headers=headers,
            json={"mode": "disable"},
        )
        assert owner_disable.status_code == 200
        assert owner_disable.json()["updated"] == 1
        assert asyncio.run(_image_state()) == (image_id, first_id, 2)
        assert published_upserts == [[image_id]]

        owner_delete = client.post(
            f"/admin/api/imports/{first_id}/rollback",
            headers=headers,
            json={"mode": "delete"},
        )
        assert owner_delete.status_code == 200
        assert owner_delete.json()["updated"] == 1
        assert asyncio.run(_image_state()) == (image_id, first_id, 4)
        assert published_deletes == [[image_id]]


def test_admin_imports_rollback_tombstone_blocks_active_import_job(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_imports_active_rollback.db"
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
    headers = {"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"}

    with TestClient(app) as client:
        created = client.post(
            "/admin/api/imports",
            headers=headers,
            json={
                "text": "https://i.pximg.net/img-original/img/2023/01/01/00/00/00/765432_p0.jpg",
                "dry_run": False,
                "hydrate_on_import": True,
                "source": "pending",
            },
        )
        assert created.status_code == 200
        assert created.json()["executed_inline"] is False
        import_id = int(created.json()["import_id"])

        rollback = client.post(
            f"/admin/api/imports/{import_id}/rollback",
            headers=headers,
            json={"mode": "disable"},
        )
        assert rollback.status_code == 200
        assert rollback.json()["updated"] == 0

        async def _run_worker_and_read() -> tuple[int, int, str, str | None]:
            dispatcher = build_default_dispatcher(app.state.engine)
            ran = await poll_and_execute_jobs(app.state.engine, dispatcher, worker_id="test-worker", max_jobs=10)
            assert ran >= 1
            async with app.state.sessionmaker() as session:
                image_count = int(await session.scalar(sa.select(sa.func.count()).select_from(Image)) or 0)
                hydrate_count = int(
                    await session.scalar(
                        sa.select(sa.func.count())
                        .select_from(JobRow)
                        .where(JobRow.type == "hydrate_metadata")
                    )
                    or 0
                )
                job_status = str(
                    await session.scalar(
                        sa.select(JobRow.status).where(
                            JobRow.ref_type == "import",
                            JobRow.ref_id == str(import_id),
                        )
                    )
                    or ""
                )
                rollback_mode = await session.scalar(
                    sa.select(Import.rollback_mode).where(Import.id == import_id)
                )
                return image_count, hydrate_count, job_status, str(rollback_mode) if rollback_mode is not None else None

        assert asyncio.run(_run_worker_and_read()) == (0, 0, "dlq", "disable")


def test_admin_imports_invalid_body_returns_400(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_imports_invalid_body.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.post(
            "/admin/api/imports",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"text": "", "dry_run": False},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "BAD_REQUEST"
        assert body["request_id"] == "req_test"


def test_admin_imports_rollback_not_found_returns_404(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_imports_rollback_not_found.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.post(
            "/admin/api/imports/999/rollback",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"mode": "disable"},
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "NOT_FOUND"
        assert body["request_id"] == "req_test"


def test_admin_imports_rollback_invalid_id_returns_400(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_imports_rollback_invalid_id.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.post(
            "/admin/api/imports/0/rollback",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"mode": "disable"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "BAD_REQUEST"
        assert body["request_id"] == "req_test"
