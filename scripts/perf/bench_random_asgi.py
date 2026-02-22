from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import platform
import random
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.models.base import Base
from app.main import create_app


@dataclass(frozen=True, slots=True)
class BenchResult:
    total: int
    ok: int
    no_match: int
    other_error: int
    durations_s: list[float]


def _p(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    p = max(0.0, min(float(p), 100.0))
    if p == 0.0:
        return sorted_values[0]
    if p == 100.0:
        return sorted_values[-1]
    k = (len(sorted_values) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    d0 = sorted_values[f] * (c - k)
    d1 = sorted_values[c] * (k - f)
    return d0 + d1


async def _migrate_and_seed(*, app, seed_images: int) -> None:
    engine = app.state.engine
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        seed_images_i = max(1, int(seed_images))
        values: list[tuple[Any, ...]] = []
        for i in range(seed_images_i):
            illust_id = 500_000_000 + i
            random_key = (i + 0.5) / seed_images_i
            values.append(
                (
                    illust_id,
                    0,
                    "jpg",
                    f"https://i.pximg.net/img-original/img/2023/01/01/00/00/00/{illust_id}_p0.jpg",
                    f"/i/seed_{illust_id}.jpg",
                    float(random_key),
                    1000,
                    1000,
                    1.0,
                    3,
                    0,
                    0,
                    1,
                    0,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    now,
                    now,
                )
            )

        await conn.exec_driver_sql(
            """
INSERT INTO images (
  illust_id, page_index, ext, original_url, proxy_path, random_key,
  width, height, aspect_ratio, orientation, x_restrict, ai_type,
  status, fail_count, created_import_id,
  last_fail_at, last_ok_at, last_error_code, last_error_msg,
  user_id, user_name, title, created_at_pixiv,
  added_at, updated_at
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);
""".strip(),
            values,
        )


async def _run_bench(*, app, total_requests: int, concurrency: int, endpoint: str) -> BenchResult:
    total_i = max(1, int(total_requests))
    conc_i = max(1, int(concurrency))

    transport = httpx.ASGITransport(app=app)
    timeout = httpx.Timeout(30.0, connect=10.0)
    client = httpx.AsyncClient(transport=transport, base_url="http://bench.local", timeout=timeout)

    semaphore = asyncio.Semaphore(conc_i)
    durations_s: list[float] = []
    ok = 0
    no_match = 0
    other_error = 0

    async def one(i: int) -> None:
        nonlocal ok, no_match, other_error
        _ = i
        async with semaphore:
            started = time.perf_counter()
            try:
                resp = await client.get(endpoint)
                if resp.status_code == 200:
                    ok += 1
                elif resp.status_code == 404:
                    no_match += 1
                else:
                    other_error += 1
            except Exception:
                other_error += 1
            finally:
                durations_s.append(time.perf_counter() - started)

    try:
        tasks = [asyncio.create_task(one(i)) for i in range(total_i)]
        await asyncio.gather(*tasks)
    finally:
        await client.aclose()

    return BenchResult(
        total=total_i,
        ok=ok,
        no_match=no_match,
        other_error=other_error,
        durations_s=durations_s,
    )


def _build_report(*, args, result: BenchResult, elapsed_s: float) -> dict[str, Any]:
    ds = sorted(float(x) for x in result.durations_s if x is not None and x >= 0)
    mean = statistics.fmean(ds) if ds else 0.0
    p50 = _p(ds, 50.0)
    p90 = _p(ds, 90.0)
    p99 = _p(ds, 99.0)

    report: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "endpoint": args.endpoint,
        "requests": {
            "total": result.total,
            "concurrency": args.concurrency,
            "ok": result.ok,
            "no_match": result.no_match,
            "other_error": result.other_error,
        },
        "latency_s": {
            "min": ds[0] if ds else 0.0,
            "p50": p50,
            "p90": p90,
            "p99": p99,
            "max": ds[-1] if ds else 0.0,
            "mean": mean,
        },
        "throughput": {
            "elapsed_s": float(elapsed_s),
            "rps": float(result.total / elapsed_s) if elapsed_s > 0 else 0.0,
        },
        "seed_images": int(args.seed_images),
        "env": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
    }
    return report


async def main_async(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="In-process /random load test using httpx ASGITransport.")
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--seed-images", type=int, default=5000)
    parser.add_argument("--endpoint", type=str, default="/random?format=json&attempts=1")
    parser.add_argument("--output", type=str, default="")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="new_pixiv_bench_") as td:
        db_path = Path(td) / "bench.db"
        db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

        os.environ["APP_ENV"] = os.environ.get("APP_ENV") or "dev"
        os.environ["DATABASE_URL"] = db_url
        os.environ["SECRET_KEY"] = os.environ.get("SECRET_KEY") or "dev-secret-key"
        os.environ["ADMIN_USERNAME"] = os.environ.get("ADMIN_USERNAME") or "admin"

        app = create_app()
        try:
            logging.getLogger("httpx").setLevel(logging.WARNING)
            logging.getLogger("httpcore").setLevel(logging.WARNING)

            await _migrate_and_seed(app=app, seed_images=args.seed_images)

            # Warm-up: one request to prime import paths/DB connections.
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://bench.local",
            ) as c:
                await c.get("/healthz")

            started = time.perf_counter()
            result = await _run_bench(
                app=app,
                total_requests=args.requests,
                concurrency=args.concurrency,
                endpoint=args.endpoint,
            )
            elapsed_s = time.perf_counter() - started

            report = _build_report(args=args, result=result, elapsed_s=elapsed_s)

            out_path = (args.output or "").strip()
            if out_path:
                out = Path(out_path)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(f"[bench_random_asgi] wrote report: {out}")
            else:
                print(json.dumps(report, ensure_ascii=False, indent=2))
        finally:
            engine = getattr(app.state, "engine", None)
            if engine is not None:
                await engine.dispose()

    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
