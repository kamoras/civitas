"""Tests for GET/POST /explore/{doc_id}/summary's streaming behavior —
the SSE endpoint added to stream the AI document summary instead of
blocking on the full generation (issue #258).

Calls get_explore_document_summary directly rather than through a full
ASGI TestClient: WriteRateLimit (_rl) is Annotated[None, Depends(...)],
so passing None bypasses the dependency the same way FastAPI would after
resolving it, without standing up app-level test infrastructure this
repo doesn't otherwise have.
"""

import json
from unittest.mock import patch

import pytest

import app.api.explore as explore_module
from app.api.explore import get_explore_document_summary
from app.models import ExploreDocument


@pytest.fixture(autouse=True)
def _reset_summary_cooldown():
    """_summary_timestamps is module-level state keyed by doc_id — each
    test's in-memory db restarts autoincrement at 1, so without this a
    cooldown set by one test's doc #1 leaks into the next test's doc #1."""
    explore_module._summary_timestamps.clear()
    yield
    explore_module._summary_timestamps.clear()


def _make_doc(db_session, **overrides) -> ExploreDocument:
    doc = ExploreDocument(
        doc_type="Executive Order",
        source="Federal Register",
        title="Test Document",
        body="Some document body text.",
        date="2026-07-01",
        chamber="Executive",
        **overrides,
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    return doc


async def _collect_sse_events(response) -> list[dict]:
    events = []
    async for chunk in response.body_iterator:
        for line in chunk.strip().split("\n\n"):
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:"):].strip()))
    return events


async def _fake_stream(*_args, **_kwargs):
    for delta in ["SUMMARY: A test summary.\n", "KEY POINTS:\n- Point one\n", "IMPACT: Matters."]:
        yield delta


class TestSummaryEndpointCacheHit:
    async def test_cache_hit_sends_single_done_event_no_deltas(self, db_session):
        doc = _make_doc(db_session)
        cached = {"summary": "Cached summary.", "keyPoints": ["a"], "impact": "x"}
        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=cached),
            patch("app.pipeline.analyze.ollama_client.stream_llm", side_effect=AssertionError("must not stream on a cache hit")),
        ):
            response = await get_explore_document_summary(doc.id, None, db=db_session)
            events = await _collect_sse_events(response)
        assert events == [{"done": True, **cached}]


class TestSummaryEndpointStreaming:
    async def test_cache_miss_streams_deltas_then_final_parsed_result(self, db_session):
        doc = _make_doc(db_session)
        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _fake_stream),
            patch("app.pipeline.analyze.ollama_client.set_cached_llm_result") as mock_set_cache,
        ):
            response = await get_explore_document_summary(doc.id, None, db=db_session)
            events = await _collect_sse_events(response)

        delta_events = [e for e in events if "delta" in e]
        assert "".join(e["delta"] for e in delta_events) == "SUMMARY: A test summary.\nKEY POINTS:\n- Point one\nIMPACT: Matters."

        final = events[-1]
        assert final == {
            "done": True,
            "summary": "A test summary.",
            "keyPoints": ["Point one"],
            "impact": "Matters.",
        }
        assert mock_set_cache.called

    async def test_generation_failure_before_any_text_sends_empty_result(self, db_session):
        doc = _make_doc(db_session)

        async def _raising_stream(*_args, **_kwargs):
            raise ConnectionError("backend unreachable")
            yield  # pragma: no cover - makes this an async generator function

        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _raising_stream),
            patch("app.pipeline.analyze.ollama_client.set_cached_llm_result") as mock_set_cache,
        ):
            response = await get_explore_document_summary(doc.id, None, db=db_session)
            events = await _collect_sse_events(response)

        assert events == [{"done": True, "summary": "", "keyPoints": [], "impact": ""}]
        assert not mock_set_cache.called


class TestSummaryEndpointGuards:
    async def test_unknown_doc_id_raises_404(self, db_session):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await get_explore_document_summary(999999, None, db=db_session)
        assert exc_info.value.status_code == 404

    async def test_cooldown_blocks_repeat_request_for_same_doc(self, db_session):
        from fastapi import HTTPException

        doc = _make_doc(db_session)
        with patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value={"summary": "s", "keyPoints": [], "impact": ""}):
            await get_explore_document_summary(doc.id, None, db=db_session)
            with pytest.raises(HTTPException) as exc_info:
                await get_explore_document_summary(doc.id, None, db=db_session)
        assert exc_info.value.status_code == 429
