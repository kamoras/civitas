from fastapi import APIRouter

from app.api.health import router as health_router
from app.api.pipeline import router as pipeline_router
from app.api.senators import router as senators_router

api_router = APIRouter(prefix="/api")

api_router.include_router(health_router, tags=["health"])
api_router.include_router(senators_router, tags=["senators"])
api_router.include_router(pipeline_router, tags=["pipeline"])
