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
# Add a StreamHandler to root so app.* loggers have somewhere to write
_root = logging.getLogger()
if not _root.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    _root.addHandler(_handler)
_root.setLevel(_level)
for _n in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi", "app"):
    logging.getLogger(_n).setLevel(_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup
    init_db()
    start_scheduler()
    yield
    # Shutdown
    stop_scheduler()


app = FastAPI(
    title="Civitas API",
    description="Backend API for the Civitas senator representation tracker",
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
