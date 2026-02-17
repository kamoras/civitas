from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import logging
from app.config import settings

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.database import init_db
from app.scheduler import start_scheduler, stop_scheduler

# Configure logging level from PIPELINE_LOG_LEVEL env setting
_level_name = (settings.PIPELINE_LOG_LEVEL or "info").upper()
_level = getattr(logging, _level_name, logging.INFO)
# Set root + common framework loggers so pipeline DEBUG messages are visible
logging.getLogger().setLevel(_level)
for _n in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi", "app"):
    logging.getLogger(_n).setLevel(_level)
logging.getLogger(__name__).info("Logging level set to %s", _level_name)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup
    init_db()
    start_scheduler()
    yield
    # Shutdown
    stop_scheduler()


app = FastAPI(
    title="Modern Punk API",
    description="Backend API for the Modern Punk senator corruption tracker",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
