from __future__ import annotations

from fastapi import APIRouter

from app.api.admin.auth import router as auth_router
from app.api.admin.api_keys import router as api_keys_router
from app.api.admin.audit import router as audit_router
from app.api.admin.bindings import router as bindings_router
from app.api.admin.hydration_runs import router as hydration_runs_router
from app.api.admin.images import router as images_router
from app.api.admin.imports import router as imports_router
from app.api.admin.jobs import router as jobs_router
from app.api.admin.maintenance import router as maintenance_router
from app.api.admin.proxies import router as proxies_router
from app.api.admin.proxy_pools import router as proxy_pools_router
from app.api.admin.settings import router as settings_router
from app.api.admin.stats import router as stats_router
from app.api.admin.summary import router as summary_router
from app.api.admin.tokens import router as tokens_router

router = APIRouter(prefix="/admin/api")
router.include_router(auth_router)
router.include_router(api_keys_router)
router.include_router(audit_router)
router.include_router(bindings_router)
router.include_router(hydration_runs_router)
router.include_router(images_router)
router.include_router(imports_router)
router.include_router(jobs_router)
router.include_router(maintenance_router)
router.include_router(proxies_router)
router.include_router(proxy_pools_router)
router.include_router(settings_router)
router.include_router(stats_router)
router.include_router(summary_router)
router.include_router(tokens_router)
