from fastapi import APIRouter

from app.api.action import router as action_router
from app.api.admin import router as admin_router
from app.api.explore import router as explore_router
from app.api.health import router as health_router
from app.api.justices import router as justices_router
from app.api.pipeline import router as pipeline_router
from app.api.presidents import router as presidents_router
from app.api.representatives import router as representatives_router
from app.api.senators import router as senators_router

api_router = APIRouter(prefix="/api")

api_router.include_router(health_router, tags=["health"])
api_router.include_router(senators_router, tags=["senators"])
api_router.include_router(representatives_router, tags=["representatives"])
api_router.include_router(presidents_router, tags=["presidents"])
api_router.include_router(justices_router, tags=["justices"])
api_router.include_router(explore_router, tags=["explore"])
api_router.include_router(action_router, tags=["action"])
api_router.include_router(pipeline_router, tags=["pipeline"])
api_router.include_router(admin_router, tags=["admin"])
