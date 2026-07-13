"""Auth must fail CLOSED when PIPELINE_TRIGGER_TOKEN isn't configured.

presidents.py, justices.py, and explore.py previously only checked the
token `if settings.PIPELINE_TRIGGER_TOKEN:` — an unset token skipped the
check entirely and left the endpoint open to any caller (2026-07 audit;
explore.py was missed in the first pass and caught in a follow-up audit).
pipeline.py already had the correct fail-closed pattern; this pins that
all four now match it.
"""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.config import settings


@pytest.mark.asyncio
async def test_presidents_trigger_fails_closed_when_unconfigured(monkeypatch):
    from app.api.presidents import trigger_pipeline
    monkeypatch.setattr(settings, "PIPELINE_TRIGGER_TOKEN", "")
    with pytest.raises(HTTPException) as exc:
        await trigger_pipeline(background_tasks=MagicMock(), authorization=None)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_justices_trigger_fails_closed_when_unconfigured(monkeypatch):
    from app.api.justices import trigger_pipeline
    monkeypatch.setattr(settings, "PIPELINE_TRIGGER_TOKEN", "")
    with pytest.raises(HTTPException) as exc:
        await trigger_pipeline(background_tasks=MagicMock(), authorization=None)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_presidents_trigger_rejects_wrong_token_when_configured(monkeypatch):
    from app.api.presidents import trigger_pipeline
    monkeypatch.setattr(settings, "PIPELINE_TRIGGER_TOKEN", "real-token")
    with pytest.raises(HTTPException) as exc:
        await trigger_pipeline(background_tasks=MagicMock(), authorization="Bearer wrong")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_presidents_trigger_accepts_correct_token(monkeypatch):
    from app.api.presidents import trigger_pipeline
    monkeypatch.setattr(settings, "PIPELINE_TRIGGER_TOKEN", "real-token")
    bg = MagicMock()
    result = await trigger_pipeline(background_tasks=bg, authorization="Bearer real-token")
    assert result["status"] == "started"
    bg.add_task.assert_called_once()


@pytest.mark.asyncio
async def test_explore_trigger_fails_closed_when_unconfigured(monkeypatch):
    from app.api.explore import trigger_explore_pipeline
    monkeypatch.setattr(settings, "PIPELINE_TRIGGER_TOKEN", "")
    with pytest.raises(HTTPException) as exc:
        await trigger_explore_pipeline(background_tasks=MagicMock(), authorization=None)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_explore_trigger_rejects_wrong_token_when_configured(monkeypatch):
    from app.api.explore import trigger_explore_pipeline
    monkeypatch.setattr(settings, "PIPELINE_TRIGGER_TOKEN", "real-token")
    with pytest.raises(HTTPException) as exc:
        await trigger_explore_pipeline(background_tasks=MagicMock(), authorization="Bearer wrong")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_explore_trigger_accepts_correct_token(monkeypatch):
    from app.api.explore import trigger_explore_pipeline
    monkeypatch.setattr(settings, "PIPELINE_TRIGGER_TOKEN", "real-token")
    bg = MagicMock()
    result = await trigger_explore_pipeline(background_tasks=bg, authorization="Bearer real-token")
    assert result["status"] == "started"
    bg.add_task.assert_called_once()
