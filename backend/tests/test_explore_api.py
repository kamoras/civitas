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

from app.api.explore import get_explore_document_summary
from app.models import ExploreDocument


@pytest.fixture(autouse=True)
def _summary_cooldown_store(throttle_store):
    """The cooldown is keyed by doc_id, and each test's in-memory db
    restarts autoincrement at 1 — a store per test keeps a cooldown set by
    one test's doc #1 out of the next test's doc #1."""
    yield throttle_store


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

    async def test_a_generation_under_way_holds_off_another_for_the_same_doc(self, db_session):
        import asyncio

        from fastapi import HTTPException

        from app.api import explore

        doc = _make_doc(db_session)
        finish = asyncio.Event()

        async def _held_stream(*_args, **_kwargs):
            yield "SUMMARY: A test summary.\n"
            await finish.wait()

        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _held_stream),
            patch("app.pipeline.analyze.ollama_client.set_cached_llm_result"),
        ):
            first = await get_explore_document_summary(doc.id, None, db=db_session)
            with pytest.raises(HTTPException) as exc_info:
                await get_explore_document_summary(doc.id, None, db=db_session)
            assert exc_info.value.status_code == 429
            assert exc_info.value.headers["Retry-After"] == "10"  # the page asks again
            finish.set()
            await _collect_sse_events(first)
            await asyncio.gather(*list(explore._generations))
            # Over and cached: the claim is given back.
            await _collect_sse_events(await get_explore_document_summary(doc.id, None, db=db_session))

    async def test_an_unusable_output_holds_the_document_off(self, db_session):
        # Asked again at once, it would most likely come out the same way.
        from fastapi import HTTPException

        doc = _make_doc(db_session)

        async def _garbled(*_args, **_kwargs):
            yield ""

        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _garbled),
            patch("app.pipeline.analyze.ollama_client.set_cached_llm_result"),
        ):
            await _collect_sse_events(await get_explore_document_summary(doc.id, None, db=db_session))
            with pytest.raises(HTTPException) as exc_info:
                await get_explore_document_summary(doc.id, None, db=db_session)
        assert exc_info.value.status_code == 429

    async def test_generations_in_flight_are_capped(self, db_session):
        # Each finishes whether or not its reader stays: uncapped, a client
        # abandoning streams across documents would queue up LLM work.
        import asyncio

        from fastapi import HTTPException

        from app.api import explore, throttle

        docs = [_make_doc(db_session) for _ in range(explore._MAX_GENERATIONS + 1)]
        finish = asyncio.Event()

        async def _held_stream(*_args, **_kwargs):
            await finish.wait()
            yield "SUMMARY: s\n"

        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _held_stream),
            patch("app.pipeline.analyze.ollama_client.set_cached_llm_result"),
        ):
            for doc in docs[:-1]:
                await get_explore_document_summary(doc.id, None, db=db_session)
            with pytest.raises(HTTPException) as exc_info:
                await get_explore_document_summary(docs[-1].id, None, db=db_session)
            assert exc_info.value.status_code == 503
            # Refused before it started: its claim was given back.
            assert throttle.claim("explore-summary", str(docs[-1].id), period=30)
            finish.set()
            await asyncio.gather(*list(explore._generations))

    async def test_shutdown_stops_generations_and_gives_their_claims_back(self, db_session):
        import asyncio

        from app.api import explore, throttle

        doc = _make_doc(db_session)

        async def _endless(*_args, **_kwargs):
            yield "SUMMARY: partial"
            await asyncio.Event().wait()

        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _endless),
            patch("app.pipeline.analyze.ollama_client.set_cached_llm_result") as mock_set_cache,
        ):
            response = await get_explore_document_summary(doc.id, None, db=db_session)
            await asyncio.sleep(0)
            await explore.stop_generations()
            events = await _collect_sse_events(response)
        assert not explore._generations and not mock_set_cache.called
        assert events == [{"delta": "SUMMARY: partial"}]  # the stream ends
        assert throttle.claim("explore-summary", str(doc.id), period=30)

    async def test_a_summary_already_made_is_never_held_off(self, db_session):
        doc = _make_doc(db_session)
        cached = {"summary": "s", "keyPoints": [], "impact": ""}
        with patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=cached):
            for _ in range(3):
                events = await _collect_sse_events(await get_explore_document_summary(doc.id, None, db=db_session))
                assert events == [{"done": True, **cached}]

    async def test_a_missing_document_takes_no_cooldown(self, db_session):
        from fastapi import HTTPException

        from app.api import throttle

        with pytest.raises(HTTPException):
            await get_explore_document_summary(999999, None, db=db_session)
        assert throttle.claim("explore-summary", "999999", period=30)

    async def test_a_failed_generation_gives_the_cooldown_back(self, db_session):
        doc = _make_doc(db_session)

        async def _raising_stream(*_args, **_kwargs):
            raise ConnectionError("backend unreachable")
            yield  # pragma: no cover

        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _raising_stream),
        ):
            await _collect_sse_events(await get_explore_document_summary(doc.id, None, db=db_session))
            # The next reader may try at once rather than meet a 429.
            await _collect_sse_events(await get_explore_document_summary(doc.id, None, db=db_session))

    async def test_a_reader_who_leaves_mid_stream_still_gets_the_summary_made(self, db_session):
        """Stopped with its reader, a generation cached nothing while its
        claim held every other reader off: a client abandoning a stream
        each cooldown could keep a document's summary from ever existing."""
        import asyncio

        from app.api import explore

        doc = _make_doc(db_session)
        second_delta = asyncio.Event()

        async def _slow_stream(*_args, **_kwargs):
            yield "SUMMARY: A test summary.\n"
            await second_delta.wait()
            yield "IMPACT: Matters."

        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _slow_stream),
            patch("app.pipeline.analyze.ollama_client.set_cached_llm_result") as mock_set_cache,
        ):
            response = await get_explore_document_summary(doc.id, None, db=db_session)
            body = response.body_iterator
            assert "delta" in await body.__anext__()
            await body.aclose()  # the reader leaves
            second_delta.set()
            await asyncio.gather(*list(explore._generations))
        mock_set_cache.assert_called_once()
        assert mock_set_cache.call_args.args[2]["summary"].startswith("A test summary.")

    @pytest.mark.parametrize("ending", ["fails", "times out"])
    async def test_a_generation_cut_off_is_shown_but_never_cached(self, db_session, monkeypatch, ending):
        import asyncio

        from app.api import explore

        doc = _make_doc(db_session)
        monkeypatch.setattr(explore, "_SUMMARY_GENERATION_LIMIT_S", 0.05)

        async def _cut_off(*_args, **_kwargs):
            yield "SUMMARY: The rule would"
            if ending == "fails":
                raise ConnectionError("dropped")
            await asyncio.sleep(10)

        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _cut_off),
            patch("app.pipeline.analyze.ollama_client.set_cached_llm_result") as mock_set_cache,
        ):
            events = await _collect_sse_events(await get_explore_document_summary(doc.id, None, db=db_session))
            assert events[-1]["done"] and events[-1]["summary"].startswith("The rule would")
            assert not mock_set_cache.called
            # And the next reader may make it afresh at once.
            await _collect_sse_events(await get_explore_document_summary(doc.id, None, db=db_session))

    async def test_a_generation_at_its_token_limit_is_cached_without_the_cut_section(self, db_session):
        # The same prompt stops at the same place every time: what came out
        # is kept, less the sentence it stopped in.
        from app.pipeline.analyze.ollama_client import StreamCutOff

        doc = _make_doc(db_session)

        async def _at_limit(*_args, **_kwargs):
            yield "SUMMARY: Whole.\nKEY POINTS:\n- One\nIMPACT: Half a sen"
            raise StreamCutOff()

        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _at_limit),
            patch("app.pipeline.analyze.ollama_client.set_cached_llm_result") as mock_set_cache,
        ):
            events = await _collect_sse_events(await get_explore_document_summary(doc.id, None, db=db_session))
        kept = {"summary": "Whole.", "keyPoints": ["One"], "impact": ""}
        assert events[-1] == {"done": True, **kept}
        assert mock_set_cache.call_args.args[2] == kept

    async def test_a_lapsed_claim_is_given_back_only_by_its_holder(self, db_session):
        # Once lapsed it may be another generation's: the late holder's
        # release must leave the new one alone.
        from app.api import throttle

        first = throttle.hold("explore-summary", ["7"], period=300)
        second = throttle.hold("explore-summary", ["7"], period=0)  # first's reads as lapsed
        assert first and second and first[1] != second[1]
        throttle.release("explore-summary", "7", token=first[1])
        assert throttle.hold("explore-summary", ["7"], period=300) is None
        throttle.release("explore-summary", "7", token=second[1])
        assert throttle.hold("explore-summary", ["7"], period=300) is not None

    async def test_the_cap_takes_the_first_free_slot_in_one_step(self, db_session):
        from app.api import throttle

        assert throttle.hold("slots", ["0", "1"], period=300)[0] == "0"
        assert throttle.hold("slots", ["0", "1"], period=300)[0] == "1"
        assert throttle.hold("slots", ["0", "1"], period=300) is None

    async def test_a_summary_made_while_claiming_is_served_not_made_again(self, db_session):
        doc = _make_doc(db_session)
        made = {"summary": "s", "keyPoints": [], "impact": ""}
        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", side_effect=[None, made]),
            patch("app.pipeline.analyze.ollama_client.stream_llm") as stream,
        ):
            events = await _collect_sse_events(await get_explore_document_summary(doc.id, None, db=db_session))
        assert events == [{"done": True, **made}]
        stream.assert_not_called()

    async def test_a_request_cancelled_while_claiming_leaves_no_claim_behind(self, db_session):
        # The claim is the generation's to give back, not the request's.
        import asyncio

        from app.api import explore, throttle

        doc = _make_doc(db_session)
        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _fake_stream),
            patch("app.pipeline.analyze.ollama_client.set_cached_llm_result") as mock_set_cache,
        ):
            request = asyncio.create_task(get_explore_document_summary(doc.id, None, db=db_session))
            await asyncio.sleep(0)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            await asyncio.gather(*list(explore._generations))
        mock_set_cache.assert_called_once()  # finished without its reader
        assert throttle.claim("explore-summary", str(doc.id), period=30)
        assert throttle.claim("explore-summary-slot", "0", period=30)

    async def test_a_stream_waiting_on_the_llm_keeps_the_connection_alive(self, db_session, monkeypatch):
        # nginx drops a response silent for proxy_read_timeout.
        import asyncio

        from app.api import explore

        doc = _make_doc(db_session)
        monkeypatch.setattr(explore, "_KEEPALIVE_S", 0.01)

        async def _slow_first_token(*_args, **_kwargs):
            await asyncio.sleep(0.05)
            yield "SUMMARY: s\n"

        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _slow_first_token),
            patch("app.pipeline.analyze.ollama_client.set_cached_llm_result"),
        ):
            response = await get_explore_document_summary(doc.id, None, db=db_session)
            chunks = [chunk async for chunk in response.body_iterator]
        assert chunks[0].startswith(":") and chunks[-1].startswith("data:")

    async def test_an_unavailable_cooldown_refuses_rather_than_generates(self, db_session, tmp_path):
        """The cooldown fails closed: without it every POST is a fresh
        generation on the device's one LLM."""
        from fastapi import HTTPException

        from app.api import throttle

        doc = _make_doc(db_session)
        throttle.use_path(str(tmp_path / "missing-dir" / "throttle.db"))
        with patch("app.pipeline.analyze.ollama_client.stream_llm") as stream:
            with pytest.raises(HTTPException) as exc_info:
                await get_explore_document_summary(doc.id, None, db=db_session)
        assert exc_info.value.status_code == 503
        stream.assert_not_called()


class TestCommentsCaching:
    """Comments are fetched live from regulations.gov: an error is this
    moment's, and must not be cached for every later visitor."""

    async def _get(self, db_session, result):
        from unittest.mock import AsyncMock

        from app.api.explore import get_document_comments

        doc = _make_doc(db_session, comment_url="https://www.regulations.gov/document/EPA-1")
        with patch("app.pipeline.fetch.regulations_gov.fetch_comments", AsyncMock(return_value=result)):
            return await get_document_comments(None, doc.id, page=1, page_size=25, db=db_session)

    async def test_a_failed_fetch_is_never_stored(self, db_session):
        resp = await self._get(db_session, {"comments": [], "totalElements": 0, "error": "Rate limit reached",
                                            "retryable": True})
        assert resp.headers["Cache-Control"] == "public, max-age=30"

    async def test_an_unknown_document_is_cached_like_an_answer(self, db_session):
        # Not this moment's failure: asking again can't succeed, and
        # uncached every repeat would spend the shared budget.
        resp = await self._get(db_session, {"comments": [], "totalElements": 0,
                                            "error": "Document not found on Regulations.gov", "retryable": False})
        assert "Cache-Control" not in resp.headers  # cacheable, like an answer

    async def test_a_good_fetch_is_cacheable(self, db_session):
        # No header of its own: the middleware's default applies.
        resp = await self._get(db_session, {"comments": [{"id": "1"}], "totalElements": 1})
        assert "Cache-Control" not in resp.headers
