"""Tests for POST /explore/{doc_id}/summary's streaming behavior — the
SSE endpoint that streams the AI document summary as it generates, served
by the pipeline process (services/explore_summary.py): one generation per
text, shared by every reader of it.

Calls get_explore_document_summary directly rather than through a full
ASGI TestClient, with a stand-in request (_READER) — the endpoint reads
only the caller's address from it.
"""

import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException

from app.api.explore import get_explore_document_summary
from app.models import ExploreDocument
from app.services import explore_summary


@pytest.fixture(autouse=True)
def _fresh_state(throttle_store):
    """The summary state is this process's; each test's in-memory db
    restarts ids at 1, so a hold-off from one test's doc #1 mustn't reach
    the next test's."""
    explore_summary.reset()
    yield throttle_store
    explore_summary.reset()


def _make_doc(db_session, **overrides) -> ExploreDocument:
    fields = {
        "doc_type": "Executive Order",
        "source": "Federal Register",
        "title": "Test Document",
        "body": "Some document body text.",
        "date": "2026-07-01",
        "chamber": "Executive",
        **overrides,
    }
    doc = ExploreDocument(**fields)
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    return doc


def _reader(host: str) -> SimpleNamespace:
    return SimpleNamespace(client=SimpleNamespace(host=host), headers={})


_READER = _reader("203.0.113.7")
_NONE = {"done": True, "summary": "", "keyPoints": [], "impact": ""}


async def _collect_sse_events(response) -> list[dict]:
    events = []
    async for chunk in response.body_iterator:
        for line in chunk.strip().split("\n\n"):
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:"):].strip()))
    return events


async def _ask(doc, reader=_READER, db=None):
    return await get_explore_document_summary(doc.id, reader, db=db)


async def _events(doc, db, reader=_READER) -> list[dict]:
    return await _collect_sse_events(await _ask(doc, reader, db))


async def _refused(doc, db, reader=_READER) -> HTTPException:
    with pytest.raises(HTTPException) as exc_info:
        await _ask(doc, reader, db)
    return exc_info.value


async def _fake_stream(*_args, **_kwargs):
    for delta in ["SUMMARY: A test summary.\n", "KEY POINTS:\n- Point one\n", "IMPACT: Matters."]:
        yield delta


def _llm(stream, cached=None):
    """The LLM and its cache, stubbed: `stream` answers, nothing is cached
    unless `cached` says so; returns the patches and the cache writes."""
    written = {}

    def lookup(version, key, **_kw):
        return (cached or {}).get("value") or written.get(json.dumps(key, sort_keys=True))

    def remember(version, key, data):
        written[json.dumps(key, sort_keys=True)] = data

    patches = (
        patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", side_effect=lookup),
        patch("app.pipeline.analyze.ollama_client.stream_llm", stream),
        patch("app.pipeline.analyze.ollama_client.set_cached_llm_result", side_effect=remember),
    )
    return patches, written


async def _settled():
    tasks = [run.task for run in list(explore_summary._runs.values()) if run.task]
    await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(0)


def _gate():
    """A stream held until released, and its release."""
    release = asyncio.Event()

    async def stream(*_args, **_kwargs):
        yield "SUMMARY: A test summary.\n"
        await release.wait()
        yield "KEY POINTS:\n- One\nIMPACT: Matters."

    return stream, release


class TestStreaming:
    async def test_a_summary_already_made_is_one_event_and_no_generation(self, db_session):
        doc = _make_doc(db_session)
        made = {"summary": "Cached summary.", "keyPoints": ["a"], "impact": "x"}
        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=made),
            patch("app.pipeline.analyze.ollama_client.stream_llm", side_effect=AssertionError("no generation")),
        ):
            for _ in range(3):  # never limited, never held off
                assert await _events(doc, db_session) == [{"done": True, **made}]

    async def test_a_generation_streams_deltas_then_the_parsed_result_and_is_cached(self, db_session):
        doc = _make_doc(db_session)
        patches, written = _llm(_fake_stream)
        with patches[0], patches[1], patches[2]:
            events = await _events(doc, db_session)
            await _settled()
        assert "".join(e["delta"] for e in events if "delta" in e) == (
            "SUMMARY: A test summary.\nKEY POINTS:\n- Point one\nIMPACT: Matters."
        )
        assert events[-1] == {"done": True, "summary": "A test summary.", "keyPoints": ["Point one"],
                              "impact": "Matters."}
        assert list(written.values()) == [{"summary": "A test summary.", "keyPoints": ["Point one"],
                                           "impact": "Matters."}]

    async def test_a_failure_before_any_text_is_no_summary_and_may_be_tried_again_at_once(self, db_session):
        doc = _make_doc(db_session)

        async def _unreachable(*_args, **_kwargs):
            raise ConnectionError("backend unreachable")
            yield  # pragma: no cover

        patches, written = _llm(_unreachable)
        with patches[0], patches[1], patches[2]:
            assert await _events(doc, db_session) == [_NONE]
            assert await _events(doc, db_session) == [_NONE]  # not held off
        assert written == {}

    async def test_a_stream_waiting_on_the_llm_keeps_the_connection_alive(self, db_session, monkeypatch):
        # nginx drops a response silent for its read timeout.
        doc = _make_doc(db_session)
        monkeypatch.setattr(explore_summary, "KEEPALIVE_S", 0.01)

        async def _slow_first_token(*_args, **_kwargs):
            await asyncio.sleep(0.05)
            yield "SUMMARY: s\n"

        patches, _ = _llm(_slow_first_token)
        with patches[0], patches[1], patches[2]:
            chunks = [chunk async for chunk in (await _ask(doc, db=db_session)).body_iterator]
        assert chunks[0].startswith(":") and chunks[-1].startswith("data:")

    async def test_unknown_document_is_a_404(self, db_session):
        with pytest.raises(HTTPException) as exc_info:
            await get_explore_document_summary(999999, _READER, db=db_session)
        assert exc_info.value.status_code == 404

    async def test_the_read_only_api_refuses_rather_than_generates(self, db_session, monkeypatch):
        # nginx sends the route to the pipeline service; one that reached
        # the API anyway doesn't start background work there.
        from app.config import settings

        doc = _make_doc(db_session)
        monkeypatch.setattr(settings, "PROCESS_ROLE", "api")
        with patch("app.pipeline.analyze.ollama_client.stream_llm") as stream:
            assert (await _refused(doc, db_session)).status_code == 503
        stream.assert_not_called()


class TestOneGenerationPerText:
    async def test_readers_of_the_same_text_share_one_generation(self, db_session):
        # A reader arriving mid-generation gets every event so far and the
        # rest, not a refusal and not a second generation.
        doc = _make_doc(db_session)
        stream, release = _gate()
        calls = []

        async def counted(*a, **k):
            calls.append(1)
            async for delta in stream(*a, **k):
                yield delta

        patches, written = _llm(counted)
        with patches[0], patches[1], patches[2]:
            first = await _ask(doc, db=db_session)
            first_events = asyncio.create_task(_collect_sse_events(first))
            await asyncio.sleep(0.01)
            second = await _ask(doc, _reader("198.51.100.2"), db_session)
            release.set()
            a, b = await first_events, await _collect_sse_events(second)
            await _settled()
        assert calls == [1] and a == b and a[-1]["summary"] == "A test summary."
        assert len(written) == 1

    async def test_a_reader_who_leaves_still_gets_the_summary_made(self, db_session):
        # An abandoned stream can't hold the document's summary off: the
        # generation finishes, and the next reader is served from the cache.
        doc = _make_doc(db_session)
        stream, release = _gate()
        patches, written = _llm(stream)
        with patches[0], patches[1], patches[2]:
            body = (await _ask(doc, db=db_session)).body_iterator
            assert "delta" in await body.__anext__()
            await body.aclose()  # the reader leaves
            release.set()
            await _settled()
            assert len(written) == 1
            assert (await _events(doc, db_session))[0]["done"]  # one event: the cache

    async def test_a_summary_is_filed_under_the_text_it_was_made_from(self, db_session):
        # A document changed in place (a body backfilled, a reset reusing
        # its id) is summarised afresh, not served the old text's summary.
        doc = _make_doc(db_session)
        patches, written = _llm(_fake_stream)
        with patches[0], patches[1], patches[2]:
            await _events(doc, db_session)
            await _settled()
            assert len(await _events(doc, db_session)) == 1  # unchanged: served
            doc.body = "A different document now."
            db_session.commit()
            assert any("delta" in e for e in await _events(doc, db_session))  # made afresh
            await _settled()
        assert len(written) == 2


class TestCapAndClients:
    async def test_generations_in_flight_are_capped(self, db_session):
        docs = [_make_doc(db_session, title=f"Doc {i}") for i in range(explore_summary.MAX_GENERATIONS + 1)]
        stream, release = _gate()
        patches, _ = _llm(stream)
        with patches[0], patches[1], patches[2]:
            for i, doc in enumerate(docs[:-1]):
                await _ask(doc, _reader(f"198.51.100.{i}"), db_session)
            refused = await _refused(docs[-1], db_session, _reader("198.51.100.99"))
            assert refused.status_code == 503 and refused.headers["X-Summary-Wait"] == "1"
            release.set()
            await _settled()
            # A slot free again: it starts.
            assert any("delta" in e for e in await _events(docs[-1], db_session, _reader("198.51.100.99")))

    async def test_one_client_starts_one_generation_at_a_time(self, db_session):
        # A generation outlives its reader: one address mustn't hold every
        # slot. Another reader still gets the free one.
        first, second = _make_doc(db_session, title="A"), _make_doc(db_session, title="B")
        stream, release = _gate()
        patches, _ = _llm(stream)
        with patches[0], patches[1], patches[2]:
            await _ask(first, db=db_session)
            assert (await _refused(second, db_session)).status_code == 503
            other = await _ask(second, _reader("198.51.100.1"), db_session)
            release.set()
            assert (await _collect_sse_events(other))[-1]["summary"] == "A test summary."

    async def test_the_one_per_client_rule_keys_clients_in_process(self, db_session, monkeypatch):
        # Not by the throttle store: its keys change at midnight, and its
        # moments of unavailability would hand a client a second key. The
        # rule lives only in the one pipeline process, so it needs neither.
        import uuid

        from app.api import throttle

        # Every store key new — a new day, or the store back from a moment
        # away — as far as anything keyed by the store can tell.
        monkeypatch.setattr(throttle, "client_key", lambda ip, purpose: throttle.ClientKey(uuid.uuid4().hex))
        doc, other = _make_doc(db_session, title="A"), _make_doc(db_session, title="B")
        stream, release = _gate()
        patches, _ = _llm(stream)
        with patches[0], patches[1], patches[2]:
            first = await _ask(doc, db=db_session)
            refused = await _refused(other, db_session)
            assert refused.status_code == 503 and refused.headers["X-Summary-Wait"] == "1"
            elsewhere = await _ask(other, _reader("198.51.100.9"), db_session)  # another address
            release.set()
            for response in (first, elsewhere):
                assert (await _collect_sse_events(response))[-1]["summary"] == "A test summary."
            await _settled()

    async def test_a_request_limit_counts_only_uncached_requests(self, db_session, monkeypatch):
        # Readers behind one address opening many documents: a summary
        # already made is never refused.
        from app.api import explore

        monkeypatch.setattr(explore, "_SUMMARY_REQUESTS_PER_MINUTE", 1)
        doc, other = _make_doc(db_session, title="A"), _make_doc(db_session, title="B")
        made = {"summary": "s", "keyPoints": [], "impact": ""}
        with patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=made):
            for _ in range(5):
                assert await _events(doc, db_session)
        patches, _ = _llm(_fake_stream)
        with patches[0], patches[1], patches[2]:
            await _events(doc, db_session)
            refused = await _refused(other, db_session)
        assert refused.status_code == 429 and refused.headers["X-Summary-Wait"] == "1"


class TestEndings:
    async def test_output_at_the_token_limit_is_kept_less_its_last_section_and_said_so(self, db_session):
        from app.pipeline.analyze.ollama_client import StreamCutOff

        doc = _make_doc(db_session)

        async def _at_limit(*_args, **_kwargs):
            yield "SUMMARY: Whole.\nKEY POINTS:\n- One\nIMPACT: Half a sen"
            raise StreamCutOff()

        patches, written = _llm(_at_limit)
        with patches[0], patches[1], patches[2]:
            events = await _events(doc, db_session)
            await _settled()
        kept = {"summary": "Whole.", "keyPoints": ["One"], "impact": "", "truncated": True}
        assert events[-1] == {"done": True, **kept} and list(written.values()) == [kept]

    async def test_an_unusable_output_is_the_answer_for_a_while(self, db_session):
        doc = _make_doc(db_session)

        async def _garbled(*_args, **_kwargs):
            yield ""

        patches, _ = _llm(_garbled)
        with patches[0], patches[1], patches[2]:
            await _events(doc, db_session)
        with patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None), \
                patch("app.pipeline.analyze.ollama_client.stream_llm") as stream:
            assert await _events(doc, db_session) == [_NONE]
        stream.assert_not_called()

    async def test_an_unusable_output_holds_off_only_that_text(self, db_session):
        doc = _make_doc(db_session)

        async def _garbled(*_args, **_kwargs):
            yield ""

        patches, _ = _llm(_garbled)
        with patches[0], patches[1], patches[2]:
            await _events(doc, db_session)
        doc.body = "The real text, backfilled."
        db_session.commit()
        patches, _ = _llm(_fake_stream)
        with patches[0], patches[1], patches[2]:
            assert any("delta" in e for e in await _events(doc, db_session))

    @pytest.mark.parametrize("error", [ConnectionError("dropped"), httpx.ConnectTimeout("unreachable"),
                                       httpx.HTTPStatusError("bad", request=httpx.Request("POST", "http://llm"),
                                                             response=httpx.Response(400))],
                             ids=["dropped", "connect-timeout", "llm-400"])
    async def test_a_failure_after_some_text_is_shown_partial_and_retried_at_once(self, db_session, error):
        doc = _make_doc(db_session)

        async def _fails(*_args, **_kwargs):
            yield "SUMMARY: The rule would apply.\nKEY POINTS:\n- One\n- Half a"
            raise error

        patches, written = _llm(_fails)
        with patches[0], patches[1], patches[2]:
            events = await _events(doc, db_session)
            assert events[-1] == {"done": True, "summary": "The rule would apply.", "keyPoints": ["One"],
                                  "impact": "", "partial": True}
            assert any("delta" in e for e in await _events(doc, db_session))  # not held off
        assert written == {}

    async def test_a_timeout_holds_the_text_off_briefly_then_twice_is_unusable(self, db_session):
        # Once may be a busy LLM; twice in the window is the prompt.
        doc = _make_doc(db_session)

        async def _no_answer(*_args, **_kwargs):
            raise httpx.ReadTimeout("busy")
            yield  # pragma: no cover

        patches, written = _llm(_no_answer)
        with patches[0], patches[1], patches[2]:
            first = await _events(doc, db_session)
            assert first == [{**_NONE, "retryAfter": 120}]  # this reader: ask again then
            refused = await _refused(doc, db_session, _reader("198.51.100.3"))
            assert refused.status_code == 503 and 110 <= int(refused.headers["Retry-After"]) <= 120
            explore_summary._holds.clear()  # the brief hold lapses
            assert await _events(doc, db_session) == [_NONE]  # the answer now
            assert await _events(doc, db_session) == [_NONE]  # and for a while
        assert written == {}

    async def test_the_deadline_counts_as_running_out_of_time(self, db_session, monkeypatch):
        doc = _make_doc(db_session)
        monkeypatch.setattr(explore_summary, "GENERATION_LIMIT_S", 0.05)

        async def _endless(*_args, **_kwargs):
            yield "SUMMARY: The rule would apply.\nKEY POINTS:\n- One\n- Half a"
            await asyncio.sleep(10)

        patches, written = _llm(_endless)
        with patches[0], patches[1], patches[2]:
            events = await _events(doc, db_session)
        assert events[-1]["partial"] and written == {}
        assert explore_summary._holds[next(iter(explore_summary._holds))][0] == "slow"

    async def test_a_busy_llm_holds_every_new_generation_off_briefly(self, db_session):
        # A fact about the LLM, not the text: every document waits, and
        # this reader is told when to ask again.
        doc, other = _make_doc(db_session, title="A"), _make_doc(db_session, title="B")

        async def _busy(*_args, **_kwargs):
            raise httpx.HTTPStatusError("busy", request=httpx.Request("POST", "http://llm"),
                                        response=httpx.Response(503))
            yield  # pragma: no cover

        patches, _ = _llm(_busy)
        with patches[0], patches[1], patches[2]:
            assert await _events(doc, db_session) == [{**_NONE, "retryAfter": 30}]
            refused = await _refused(other, db_session, _reader("198.51.100.4"))
        assert refused.status_code == 503 and 25 <= int(refused.headers["Retry-After"]) <= 30

    async def test_an_unusable_text_answers_at_once_even_while_the_llm_is_busy(self, db_session):
        # Its answer is known: no need to wait on the LLM to hear it.
        import time as _time

        doc = _make_doc(db_session)

        async def _garbled(*_args, **_kwargs):
            yield ""

        patches, _ = _llm(_garbled)
        with patches[0], patches[1], patches[2]:
            await _events(doc, db_session)
        explore_summary._llm_busy_until = _time.monotonic() + 30
        with patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None):
            assert await _events(doc, db_session) == [_NONE]

    async def test_a_summary_isnt_cached_while_a_data_reset_holds_writes(self, db_session):
        from app.background import exclusive

        doc = _make_doc(db_session)
        patches, written = _llm(_fake_stream)
        with patches[0], patches[1], patches[2], exclusive("data-reset"):
            events = await _events(doc, db_session)
        assert events[-1]["summary"] == "A test summary." and written == {}


class TestCachedSummaryRead:
    """GET .../cached-summary: served by the API and nginx, so a summary
    already made never waits on the pipeline process."""

    async def test_a_summary_already_made_is_read_without_the_pipeline(self, db_session, monkeypatch):
        from app.api.explore import get_cached_explore_summary
        from app.config import settings

        doc = _make_doc(db_session)
        made = {"summary": "s", "keyPoints": [], "impact": ""}
        monkeypatch.setattr(settings, "PROCESS_ROLE", "api")  # the read-only API serves it
        with patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=made) as read:
            resp = await get_cached_explore_summary(doc.id, db=db_session)
        assert resp.status_code == 200 and json.loads(resp.body) == {"done": True, **made}
        assert resp.headers["Cache-Control"].startswith("public")
        # Asked under the same key the pipeline process files it under.
        prompt = explore_summary.prompt_for(doc)
        assert read.call_args.args == (prompt["promptVersion"], explore_summary.cache_key(doc.id, prompt))

    async def test_none_yet_is_a_204_never_kept(self, db_session):
        from app.api.explore import get_cached_explore_summary

        doc = _make_doc(db_session)
        with patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None):
            resp = await get_cached_explore_summary(doc.id, db=db_session)
        assert resp.status_code == 204 and resp.headers["Cache-Control"] == "no-store"

    async def test_an_unreadable_cache_is_a_wait_not_none_yet(self, db_session):
        # "Not made yet" would have the pipeline make again a summary that
        # may well be stored: both ends answer a wait instead.
        from app.api.explore import get_cached_explore_summary

        from unittest.mock import MagicMock

        from sqlalchemy.exc import OperationalError

        doc = _make_doc(db_session)
        # Through the real read (analysis_cache_get), which used to take any
        # error for a miss: only the session is the locked database.
        locked_db = MagicMock()
        locked_db.query.side_effect = OperationalError("SELECT", {}, sqlite3.OperationalError("database is locked"))
        with patch("app.pipeline.analyze.ollama_client.SessionLocal", return_value=locked_db), \
                patch("app.pipeline.analyze.ollama_client.stream_llm", side_effect=AssertionError("generated")):
            with pytest.raises(HTTPException) as read:
                await get_cached_explore_summary(doc.id, db=db_session)
            asked = await _refused(doc, db_session)
        for refusal in (read.value, asked):
            assert refusal.status_code == 503 and refusal.headers["X-Summary-Wait"] == "1"

    async def test_a_document_without_a_body_is_read_not_a_500(self, db_session):
        from app.api.explore import get_cached_explore_summary

        doc = _make_doc(db_session, body=None)
        with patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None):
            resp = await get_cached_explore_summary(doc.id, db=db_session)
        assert resp.status_code == 204

    async def test_no_such_document_is_a_404(self, db_session):
        from app.api.explore import get_cached_explore_summary

        with pytest.raises(HTTPException) as exc_info:
            await get_cached_explore_summary(999999, db=db_session)
        assert exc_info.value.status_code == 404


class TestJoining:
    async def test_joining_counts_against_the_request_limit(self, db_session, monkeypatch):
        # A join starts nothing, but holds a stream open on the one pipeline
        # process: a client can't open them without limit.
        from app.api import explore

        monkeypatch.setattr(explore, "_SUMMARY_REQUESTS_PER_MINUTE", 2)
        doc = _make_doc(db_session)
        stream, release = _gate()
        patches, _ = _llm(stream)
        with patches[0], patches[1], patches[2]:
            first = await _ask(doc, db=db_session)
            joined = await _ask(doc, db=db_session)
            refused = await _refused(doc, db_session)
            assert refused.status_code == 429 and refused.headers["X-Summary-Wait"] == "1"
            # Another reader still joins.
            other = await _ask(doc, _reader("198.51.100.9"), db_session)
            release.set()
            for response in (first, joined, other):
                assert (await _collect_sse_events(response))[-1]["summary"] == "A test summary."
            await _settled()


class TestAfterTheStream:
    async def test_readers_hear_it_is_over_before_the_cache_write(self, db_session):
        # The write can wait on the pipeline's own write lock: the page
        # isn't held on it, and the run no longer counts toward the cap.
        import threading

        doc, other = _make_doc(db_session, title="A"), _make_doc(db_session, title="B")
        writing, release = threading.Event(), threading.Event()

        def slow_write(version, key, data):
            writing.set()
            release.wait(5)

        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _fake_stream),
            patch("app.pipeline.analyze.ollama_client.set_cached_llm_result", side_effect=slow_write),
        ):
            events = await _events(doc, db_session)
            assert events[-1]["summary"] == "A test summary."
            await asyncio.to_thread(writing.wait, 5)
            # Still writing: the same client may start another.
            second = await _ask(other, db=db_session)
            release.set()
            await _collect_sse_events(second)
            await _settled()

    async def test_a_failure_after_the_stream_still_ends_it_properly(self, db_session):
        doc = _make_doc(db_session)
        patches, _ = _llm(_fake_stream)
        with patches[0], patches[1], patches[2], \
                patch("app.pipeline.analyze.prompts.parse_explore_document_summary",
                      side_effect=ValueError("unexpected output")):
            events = await _events(doc, db_session)
        assert events[-1] == _NONE

    async def test_a_reader_whose_cache_read_raced_the_write_is_served_the_run(self, db_session):
        # Its read missed; by the time it looks, the run is over and gone
        # from the running ones: served its result, not a second generation.
        doc = _make_doc(db_session)
        patches, _ = _llm(_fake_stream)
        with patches[0], patches[1], patches[2]:
            await _events(doc, db_session)
            await _settled()
        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", side_effect=AssertionError("regenerated")),
        ):
            events = await _events(doc, db_session, _reader("198.51.100.8"))
        assert events == [{"done": True, "summary": "A test summary.", "keyPoints": ["Point one"],
                           "impact": "Matters."}]


class TestShutdown:
    async def test_shutdown_stops_generations_and_their_readers_streams_end(self, db_session):
        doc = _make_doc(db_session)

        async def _endless(*_args, **_kwargs):
            yield "SUMMARY: partial"
            await asyncio.Event().wait()

        patches, written = _llm(_endless)
        with patches[0], patches[1], patches[2]:
            response = await _ask(doc, db=db_session)
            await asyncio.sleep(0.01)
            await explore_summary.stop()
            events = await _collect_sse_events(response)
        assert events == [{"delta": "SUMMARY: partial"}] and written == {}
        assert explore_summary._runs == {} and explore_summary._by_client == {}


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


async def test_stats_count_every_group_in_one_pass(db_session):
    import json as _json

    from app.api.explore import explore_stats

    _make_doc(db_session)
    for chamber, url, closes in (("Executive", "https://www.regulations.gov/document/X-1", "2999-01-01"),
                                 ("", "https://www.regulations.gov/document/X-2", "2000-01-01")):
        db_session.add(ExploreDocument(doc_type="Proposed Rule", source="Federal Register", title="Rule",
                                       body="b", date="2026-07-01", chamber=chamber, comment_url=url,
                                       comments_close_on=closes))
    db_session.commit()
    body = _json.loads((await explore_stats(db=db_session)).body)
    assert body == {
        "totalDocuments": 3,
        "byType": {"Executive Order": 1, "Proposed Rule": 2},
        "byChamber": {"Executive": 2},
        "openForComment": 1,
    }
