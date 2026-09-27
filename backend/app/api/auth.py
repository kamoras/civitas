"""The pipeline-trigger token check, shared by every trigger route.

One implementation because four copies drifted: three of them skipped the
check entirely when PIPELINE_TRIGGER_TOKEN was unset, leaving their routes
open to any caller (2026-07 audit), and they disagreed on the status code.
It fails closed — no configured token, no trigger — and compares in
constant time.
"""

import secrets

from fastapi import HTTPException

from app.config import settings


def check_pipeline_token(authorization: str | None) -> None:
    """503 when no token is configured, 401 unless `authorization` is
    "Bearer <PIPELINE_TRIGGER_TOKEN>"."""
    if not settings.PIPELINE_TRIGGER_TOKEN:
        raise HTTPException(status_code=503, detail="Pipeline trigger token not configured")
    expected = f"Bearer {settings.PIPELINE_TRIGGER_TOKEN}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing authorization token")
