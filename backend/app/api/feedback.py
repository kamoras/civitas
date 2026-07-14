"""
Site feedback form -> GitHub issue.

Visitors can't file GitHub issues directly (the repo is private), so this
endpoint is the actual reporting path advertised on /accessibility and
/feedback: it takes a form submission and creates the issue server-side
using our own token, never exposing any credential to the client.
"""
import logging
import re

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.api.rate_limit import WriteRateLimit
from app.config import settings
from app.schemas import CamelModel

logger = logging.getLogger(__name__)

router = APIRouter()

_CATEGORY_LABELS: dict[str, str] = {
    "bug": "Bug report",
    "idea": "Feature idea",
    "accessibility": "Accessibility barrier",
    "data": "Data question / correction",
    "other": "Other feedback",
}

_MESSAGE_MAX_LEN = 4000
_GITHUB_API_TIMEOUT = 15.0
# Loose on purpose — this only gates what we'll echo into a GitHub issue for
# optional follow-up contact, not a mailbox-existence check.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class FeedbackRequest(BaseModel):
    category: str = Field(..., pattern="^(bug|idea|accessibility|data|other)$")
    message: str = Field(..., min_length=10, max_length=_MESSAGE_MAX_LEN)
    email: str | None = Field(None, max_length=254)
    page_url: str | None = Field(None, max_length=500)

    @field_validator("message")
    @classmethod
    def strip_message(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 10:
            raise ValueError("message must be at least 10 characters")
        return v

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str | None) -> str | None:
        if v is None or v.strip() == "":
            return None
        v = v.strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("not a valid email address")
        return v


class FeedbackResponse(CamelModel):
    ok: bool
    issue_url: str | None = None


def _build_issue_body(body: FeedbackRequest) -> str:
    lines = [
        body.message,
        "",
        "---",
        f"**Category:** {_CATEGORY_LABELS.get(body.category, body.category)}",
    ]
    if body.page_url:
        lines.append(f"**Page:** {body.page_url}")
    if body.email:
        lines.append(f"**Contact:** {body.email}")
    lines.append("")
    lines.append("_Submitted via the site feedback form._")
    return "\n".join(lines)


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(body: FeedbackRequest, _rl: WriteRateLimit) -> FeedbackResponse:
    """Create a GitHub issue from a feedback form submission."""
    if not settings.FEEDBACK_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Feedback submission is temporarily unavailable. Please try again later.",
        )

    title_prefix = _CATEGORY_LABELS.get(body.category, "Feedback")
    title = f"[{title_prefix}] {body.message[:80].strip()}"
    if len(body.message) > 80:
        title += "…"

    payload = {
        "title": title,
        "body": _build_issue_body(body),
        "labels": ["user-feedback"],
    }

    url = f"https://api.github.com/repos/{settings.GITHUB_FEEDBACK_REPO}/issues"
    headers = {
        "Authorization": f"Bearer {settings.FEEDBACK_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        async with httpx.AsyncClient(timeout=_GITHUB_API_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError:
        logger.exception("Feedback submission failed to reach GitHub")
        raise HTTPException(status_code=502, detail="Could not submit feedback right now.")

    if resp.status_code != 201:
        logger.error(
            "Feedback issue creation failed: %s %s", resp.status_code, resp.text[:300],
        )
        raise HTTPException(status_code=502, detail="Could not submit feedback right now.")

    data = resp.json()
    return FeedbackResponse(ok=True, issue_url=data.get("html_url"))
