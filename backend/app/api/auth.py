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


# The value .env.example ships for both tokens. It is public (it is in the
# repository), so a server that copied the template and never changed it has
# no secret at all: read as unset, which fails closed, rather than as a token
# anyone can send. The public trigger route is reachable from the internet.
EXAMPLE_TOKEN = "change-me-to-a-random-token"


def configured_token(value: str) -> str:
    """The token as configured, or "" when it is unset or the template's."""
    return "" if value == EXAMPLE_TOKEN else value


def check_pipeline_token(authorization: str | None) -> None:
    """503 when no token is configured, 401 unless `authorization` is
    "Bearer <PIPELINE_TRIGGER_TOKEN>"."""
    token = configured_token(settings.PIPELINE_TRIGGER_TOKEN)
    if not token:
        raise HTTPException(status_code=503, detail="Pipeline trigger token not configured")
    expected = f"Bearer {token}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing authorization token")
