"""Auth must fail CLOSED when PIPELINE_TRIGGER_TOKEN isn't configured.

presidents.py, justices.py, and explore.py previously only checked the
token `if settings.PIPELINE_TRIGGER_TOKEN:` — an unset token skipped the
check entirely and left the endpoint open to any caller (2026-07 audit;
explore.py was missed in the first pass and caught in a follow-up audit).
pipeline.py already had the correct fail-closed pattern; this pins that
all four now match it.
"""

import importlib
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.config import settings


def _trigger(module: str, name: str):
    return getattr(importlib.import_module(f"app.api.{module}"), name)


_PRESIDENTS = ("presidents", "trigger_pipeline")
_JUSTICES = ("justices", "trigger_pipeline")
_EXPLORE = ("explore", "trigger_explore_pipeline")


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [_PRESIDENTS, _JUSTICES, _EXPLORE], ids=lambda e: e[0])
async def test_trigger_fails_closed_when_unconfigured(monkeypatch, endpoint):
    monkeypatch.setattr(settings, "PIPELINE_TRIGGER_TOKEN", "")
    with pytest.raises(HTTPException) as exc:
        await _trigger(*endpoint)(authorization=None)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [_PRESIDENTS, _JUSTICES, _EXPLORE], ids=lambda e: e[0])
async def test_trigger_rejects_wrong_token_when_configured(monkeypatch, endpoint):
    monkeypatch.setattr(settings, "PIPELINE_TRIGGER_TOKEN", "real-token")
    with pytest.raises(HTTPException) as exc:
        await _trigger(*endpoint)(authorization="Bearer wrong")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_presidents_trigger_accepts_correct_token(monkeypatch):
    from app.api.presidents import trigger_pipeline
    monkeypatch.setattr(settings, "PIPELINE_TRIGGER_TOKEN", "real-token")
    with patch("app.background.start_writer") as start:
        result = await trigger_pipeline(authorization="Bearer real-token")
    assert result["status"] == "started"
    start.assert_called_once()


@pytest.mark.asyncio
async def test_a_trigger_during_a_data_reset_is_refused_not_silently_dropped(monkeypatch):
    from app.api.presidents import trigger_pipeline
    from app.background import WritesHeld, exclusive

    monkeypatch.setattr(settings, "PIPELINE_TRIGGER_TOKEN", "real-token")
    with exclusive("test-reset"):
        with pytest.raises(WritesHeld):  # answered 409 by main's handler
            await trigger_pipeline(authorization="Bearer real-token")


@pytest.mark.asyncio
async def test_explore_trigger_accepts_correct_token(monkeypatch):
    from app.api.explore import trigger_explore_pipeline
    monkeypatch.setattr(settings, "PIPELINE_TRIGGER_TOKEN", "real-token")
    with patch("app.api.pipeline_runner.start_writer") as start:
        result = await trigger_explore_pipeline(authorization="Bearer real-token")
    assert result["status"] == "started"
    start.assert_called_once()
