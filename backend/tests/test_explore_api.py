"""Tests for GET/POST /explore/{doc_id}/summary's streaming behavior —
the SSE endpoint added to stream the AI document summary instead of
blocking on the full generation (issue #258).

Calls get_explore_document_summary directly rather than through a full
ASGI TestClient, with a stand-in request (_READER) — the endpoint reads
only the caller's address from it, for the write limit it charges when a
generation starts.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
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


# The summary endpoint reads only the caller's address from its request.
def _reader(host: str) -> SimpleNamespace:
    return SimpleNamespace(client=SimpleNamespace(host=host), headers={})


_READER = _reader("203.0.113.7")


def _document_claimed(doc) -> bool:
    """Whether any generation claim is live for `doc` (keyed on the
    document and the text it read)."""
    import time

    from app.api import throttle

    with throttle._using() as conn:
        return conn.execute(
            "SELECT 1 FROM claims WHERE bucket = 'explore-summary' AND key LIKE ? AND expires_at > ?",
            (f"{doc.id}:%", time.time()),
        ).fetchone() is not None


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
            response = await get_explore_document_summary(doc.id, _READER, db=db_session)
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
            response = await get_explore_document_summary(doc.id, _READER, db=db_session)
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
            response = await get_explore_document_summary(doc.id, _READER, db=db_session)
            events = await _collect_sse_events(response)

        assert events == [{"done": True, "summary": "", "keyPoints": [], "impact": ""}]
        assert not mock_set_cache.called


class TestSummaryEndpointGuards:
    async def test_unknown_doc_id_raises_404(self, db_session):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await get_explore_document_summary(999999, _READER, db=db_session)
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
            first = await get_explore_document_summary(doc.id, _READER, db=db_session)
            with pytest.raises(HTTPException) as exc_info:
                await get_explore_document_summary(doc.id, _READER, db=db_session)
            assert exc_info.value.status_code == 429
            assert exc_info.value.headers["Retry-After"] == "10"  # the page asks again
            finish.set()
            await _collect_sse_events(first)
            await asyncio.gather(*list(explore._generations))
            # Over and cached: the claim is given back.
            await _collect_sse_events(await get_explore_document_summary(doc.id, _READER, db=db_session))

    async def test_an_unusable_output_is_the_answer_for_a_while(self, db_session):
        # The same prompt at temperature 0 would come out the same way: not
        # generated again for a while — and not a 429 "being written",
        # which the page would wait out for nothing.
        doc = _make_doc(db_session)

        async def _garbled(*_args, **_kwargs):
            yield ""

        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _garbled),
            patch("app.pipeline.analyze.ollama_client.set_cached_llm_result"),
        ):
            await _collect_sse_events(await get_explore_document_summary(doc.id, _READER, db=db_session))
        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm") as stream,
        ):
            events = await _collect_sse_events(await get_explore_document_summary(doc.id, _READER, db=db_session))
        assert events == [{"done": True, "summary": "", "keyPoints": [], "impact": ""}]
        stream.assert_not_called()

    async def test_generations_in_flight_are_capped(self, db_session):
        # Each finishes whether or not its reader stays: uncapped, a client
        # abandoning streams across documents would queue up LLM work.
        import asyncio

        from fastapi import HTTPException

        from app.api import explore

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
            for i, doc in enumerate(docs[:-1]):
                await get_explore_document_summary(doc.id, _reader(f"198.51.100.{i}"), db=db_session)
            with pytest.raises(HTTPException) as exc_info:
                await get_explore_document_summary(docs[-1].id, _reader("198.51.100.99"), db=db_session)
            assert exc_info.value.status_code == 503
            # Refused before it started: its claim was given back.
            assert not _document_claimed(docs[-1])
            finish.set()
            await asyncio.gather(*list(explore._generations))

    async def test_one_client_holds_one_generation_not_every_slot(self, db_session):
        # A generation outlives its reader: one address starting and
        # leaving generations must not hold the whole site's slots.
        import asyncio

        from fastapi import HTTPException

        from app.api import explore

        first, second = _make_doc(db_session), _make_doc(db_session)
        finish = asyncio.Event()

        async def _held_stream(*_args, **_kwargs):
            await finish.wait()
            yield "SUMMARY: s\n"

        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _held_stream),
            patch("app.pipeline.analyze.ollama_client.set_cached_llm_result"),
        ):
            await get_explore_document_summary(first.id, _READER, db=db_session)
            with pytest.raises(HTTPException) as exc_info:
                await get_explore_document_summary(second.id, _READER, db=db_session)
            assert exc_info.value.status_code == 503
            # Another reader still gets the free slot.
            response = await get_explore_document_summary(second.id, _reader("198.51.100.1"), db=db_session)
            finish.set()
            assert any("delta" in e for e in await _collect_sse_events(response))
            await asyncio.gather(*list(explore._generations))

    async def test_the_one_per_client_rule_holds_across_midnight(self, db_session, monkeypatch):
        # A generation claimed under yesterday's key still counts once the
        # day turns (as throttle.claim's rules do).
        import asyncio

        from fastapi import HTTPException

        from app.api import explore, throttle

        first, second = _make_doc(db_session), _make_doc(db_session)
        finish = asyncio.Event()

        async def _held_stream(*_args, **_kwargs):
            await finish.wait()
            yield "SUMMARY: s\n"

        yesterday = throttle.client_key("203.0.113.7", "explore-summary-client")
        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _held_stream),
            patch("app.pipeline.analyze.ollama_client.set_cached_llm_result"),
        ):
            await get_explore_document_summary(first.id, _READER, db=db_session)
            # The day turns: today's key is new, yesterday's is `previous`.
            today = throttle.ClientKey("a-new-days-key")
            today.previous = str(yesterday)
            monkeypatch.setattr(throttle, "client_key", lambda ip, purpose, scope="": today)
            with pytest.raises(HTTPException) as exc_info:
                await get_explore_document_summary(second.id, _READER, db=db_session)
            assert exc_info.value.status_code == 503
            finish.set()
            await asyncio.gather(*list(explore._generations))

    async def test_a_text_that_times_out_twice_is_held_off_as_unusable(self, db_session):
        # Once may be a busy LLM; twice in the window is the prompt.
        import httpx

        from fastapi import HTTPException

        doc = _make_doc(db_session)

        async def _no_answer(*_args, **_kwargs):
            raise httpx.ReadTimeout("busy")
            yield  # pragma: no cover

        from app.api import throttle

        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _no_answer),
        ):
            first = await _collect_sse_events(await get_explore_document_summary(doc.id, _READER, db=db_session))
            assert first[-1]["retryAfter"] == 120
            with pytest.raises(HTTPException):  # held off: slow
                await get_explore_document_summary(doc.id, _READER, db=db_session)
            with throttle._using() as conn:  # the slow hold-off lapses
                conn.execute("DELETE FROM claims WHERE bucket = 'explore-summary-slow'")
            second = await _collect_sse_events(await get_explore_document_summary(doc.id, _READER, db=db_session))
            assert "retryAfter" not in second[-1]  # the answer now, not a wait
            third = await _collect_sse_events(await get_explore_document_summary(doc.id, _READER, db=db_session))
        assert third == [{"done": True, "summary": "", "keyPoints": [], "impact": ""}]

    async def test_shutdown_stops_generations_and_gives_their_claims_back(self, db_session):
        import asyncio

        from app.api import explore

        doc = _make_doc(db_session)

        async def _endless(*_args, **_kwargs):
            yield "SUMMARY: partial"
            await asyncio.Event().wait()

        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _endless),
            patch("app.pipeline.analyze.ollama_client.set_cached_llm_result") as mock_set_cache,
        ):
            response = await get_explore_document_summary(doc.id, _READER, db=db_session)
            await asyncio.sleep(0)
            await explore.stop_generations()
            events = await _collect_sse_events(response)
        assert not explore._generations and not mock_set_cache.called
        assert events == [{"delta": "SUMMARY: partial"}]  # the stream ends
        assert not _document_claimed(doc)

    async def test_a_summary_already_made_is_never_held_off(self, db_session):
        doc = _make_doc(db_session)
        cached = {"summary": "s", "keyPoints": [], "impact": ""}
        with patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=cached):
            for _ in range(3):
                events = await _collect_sse_events(await get_explore_document_summary(doc.id, _READER, db=db_session))
                assert events == [{"done": True, **cached}]

    async def test_a_missing_document_takes_no_cooldown(self, db_session):
        from fastapi import HTTPException

        from app.api import throttle

        with pytest.raises(HTTPException):
            await get_explore_document_summary(999999, _READER, db=db_session)
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
            await _collect_sse_events(await get_explore_document_summary(doc.id, _READER, db=db_session))
            # The next reader may try at once rather than meet a 429.
            await _collect_sse_events(await get_explore_document_summary(doc.id, _READER, db=db_session))

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
            response = await get_explore_document_summary(doc.id, _READER, db=db_session)
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
            yield "SUMMARY: The rule would apply.\nKEY POINTS:\n- One\n- Half a"
            if ending == "fails":
                raise ConnectionError("dropped")
            await asyncio.sleep(10)

        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _cut_off),
            patch("app.pipeline.analyze.ollama_client.set_cached_llm_result") as mock_set_cache,
        ):
            events = await _collect_sse_events(await get_explore_document_summary(doc.id, _READER, db=db_session))
            # Shown without the sentence it stopped in.
            assert events[-1] == {"done": True, "summary": "The rule would apply.", "keyPoints": ["One"],
                                  "impact": "", "partial": True}
            assert not mock_set_cache.called
            if ending == "fails":
                # The LLM may be back: the next reader may make it afresh at once.
                again = await _collect_sse_events(await get_explore_document_summary(doc.id, _READER, db=db_session))
                assert any("delta" in event for event in again)
            else:
                # Held off for a while rather than generated over and over —
                # as a refusal to wait out (the LLM may only have been busy),
                # not an answer.
                from fastapi import HTTPException

                with pytest.raises(HTTPException) as exc_info:
                    await get_explore_document_summary(doc.id, _READER, db=db_session)
                assert exc_info.value.status_code == 503 and exc_info.value.headers["Retry-After"] == "120"

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
            events = await _collect_sse_events(await get_explore_document_summary(doc.id, _READER, db=db_session))
        # Said to be truncated — to this reader and, through the cache, to
        # every later one.
        kept = {"summary": "Whole.", "keyPoints": ["One"], "impact": "", "truncated": True}
        assert events[-1] == {"done": True, **kept}
        assert mock_set_cache.call_args.args[2] == kept

    @pytest.mark.parametrize("error,held_off,retry_after", [
        (ConnectionError("unreachable"), False, None),
        (httpx.ConnectTimeout("unreachable"), False, None),
        (httpx.ReadTimeout("no answer"), True, 120),
        # Busy is the LLM's state, not the document's: every request is
        # held off for the busy wait (refused, not charged), this reader
        # included.
        (httpx.HTTPStatusError("busy", request=httpx.Request("POST", "http://llm"),
                               response=httpx.Response(503)), "busy", 30),
        (httpx.HTTPStatusError("bad", request=httpx.Request("POST", "http://llm"),
                               response=httpx.Response(400)), False, None),
    ], ids=["unreachable", "connect-timeout", "llm-read-timeout", "llm-busy-503", "llm-400"])
    async def test_an_llm_that_stops_answering_is_slow_one_unreachable_a_failure(
        self, db_session, error, held_off, retry_after,
    ):
        # A read timeout means the LLM is taking too long on this text:
        # held off briefly, so waiting readers don't each start a generation
        # that queues behind it. Anything else may be tried again at once.
        from fastapi import HTTPException

        doc = _make_doc(db_session)

        async def _stops(*_args, **_kwargs):
            raise error
            yield  # pragma: no cover

        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _stops),
            patch("app.pipeline.analyze.ollama_client.set_cached_llm_result"),
        ):
            first = await _collect_sse_events(await get_explore_document_summary(doc.id, _READER, db=db_session))
            assert first[-1].get("retryAfter") == retry_after
            if held_off == "busy":
                other = _make_doc(db_session)  # any document, not just this one
                with pytest.raises(HTTPException) as exc_info:
                    await get_explore_document_summary(other.id, _reader("198.51.100.5"), db=db_session)
                assert exc_info.value.status_code == 503 and exc_info.value.headers["Retry-After"] == "30"
            elif held_off:
                with pytest.raises(HTTPException) as exc_info:
                    await get_explore_document_summary(doc.id, _READER, db=db_session)
                assert exc_info.value.status_code == 503
                assert 110 <= int(exc_info.value.headers["Retry-After"]) <= 120  # what the hold has left
            else:
                again = await _collect_sse_events(
                    await get_explore_document_summary(doc.id, _READER, db=db_session))
                assert again[-1]["done"]  # generated again, not refused

    async def test_a_summary_is_filed_under_the_text_it_was_made_from(self, db_session):
        # A document changed in place (a body backfilled, a data reset
        # reusing the id) is summarised afresh, not served the old text's
        # summary — including one whose generation outlasted the change.
        doc = _make_doc(db_session)
        written = {}

        def remember(version, key, data):
            written[json.dumps(key, sort_keys=True)] = data

        def lookup(version, key):
            return written.get(json.dumps(key, sort_keys=True))

        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", side_effect=lookup),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _fake_stream),
            patch("app.pipeline.analyze.ollama_client.set_cached_llm_result", side_effect=remember),
        ):
            await _collect_sse_events(await get_explore_document_summary(doc.id, _READER, db=db_session))
            first = dict(written)
            again = await _collect_sse_events(await get_explore_document_summary(doc.id, _READER, db=db_session))
            assert again == [{"done": True, **next(iter(first.values()))}]  # unchanged: served
            doc.body = "A different document now."
            db_session.commit()
            changed = await _collect_sse_events(await get_explore_document_summary(doc.id, _READER, db=db_session))
        assert any("delta" in event for event in changed)  # made afresh
        assert len(written) == 2

    async def test_a_claim_made_as_the_generation_is_stopped_is_still_given_back(self, db_session, monkeypatch):
        import asyncio
        import threading

        from app.api import explore, throttle

        doc = _make_doc(db_session)
        entered, go_on = threading.Event(), threading.Event()
        real_hold = throttle.hold

        def slow_hold(*args, **kwargs):
            entered.set()
            go_on.wait(5)
            return real_hold(*args, **kwargs)

        monkeypatch.setattr(throttle, "hold", slow_hold)
        with patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None):
            request = asyncio.create_task(get_explore_document_summary(doc.id, _READER, db=db_session))
            await asyncio.to_thread(entered.wait, 5)
            stopping = asyncio.create_task(explore.stop_generations())
            await asyncio.sleep(0)
            go_on.set()
            await stopping
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
        monkeypatch.setattr(throttle, "hold", real_hold)
        assert not _document_claimed(doc)

    async def test_waiting_out_a_generation_spends_none_of_the_write_budget(self, db_session, monkeypatch):
        # A page waiting on another reader's generation must not spend the
        # reader's budget for votes and comments: every request is charged
        # up front (so one over budget claims nothing), and given back when
        # no generation starts.
        import asyncio

        from fastapi import HTTPException

        from app.api import explore, rate_limit, throttle

        doc = _make_doc(db_session)
        finish = asyncio.Event()

        async def _held_stream(*_args, **_kwargs):
            yield "SUMMARY: s\n"
            await finish.wait()

        def spent():
            return 20 - throttle.hit("write", throttle.client_key("203.0.113.7", "write"), limit=20,
                                     period=60, cost=0).remaining

        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _held_stream),
            patch("app.pipeline.analyze.ollama_client.set_cached_llm_result"),
        ):
            first = await get_explore_document_summary(doc.id, _READER, db=db_session)
            for _ in range(5):
                with pytest.raises(HTTPException) as exc_info:
                    await get_explore_document_summary(doc.id, _READER, db=db_session)
                assert exc_info.value.headers["X-Summary-Wait"] == "1"
            finish.set()
            await _collect_sse_events(first)
            await asyncio.gather(*list(explore._generations))
        assert spent() == 1  # the one generation

        from app.api.throttle import Decision

        monkeypatch.setattr(rate_limit, "charge_write", lambda ip: Decision(False, 0, 9e9))
        other = _make_doc(db_session)
        with patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                await get_explore_document_summary(other.id, _READER, db=db_session)
        assert exc_info.value.status_code == 429
        assert exc_info.value.headers["X-Summary-Wait"] == "1"  # not counted, so worth asking again later
        # Its claims were given back: nobody else is held off.
        assert throttle.hold("explore-summary-slot", ["0", "1"], period=300) is not None

    async def test_a_summary_already_made_is_never_refused_by_the_request_limit(self, db_session, monkeypatch):
        # Readers behind one address (a school, a carrier NAT) opening many
        # documents: the per-request limit counts only uncached requests.
        from app.api import explore

        monkeypatch.setattr(explore, "_SUMMARY_REQUESTS_PER_MINUTE", 1)
        doc = _make_doc(db_session)
        cached = {"summary": "s", "keyPoints": [], "impact": ""}
        with patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=cached):
            for _ in range(5):
                assert await _collect_sse_events(await get_explore_document_summary(doc.id, _READER, db=db_session))

    async def test_a_reader_whose_own_generation_timed_out_is_told_when_to_ask_again(self, db_session):
        # Not "no summary": the document is held off only briefly, and every
        # other reader is told to wait it out — so is this one.
        import httpx

        doc = _make_doc(db_session)

        async def _no_answer(*_args, **_kwargs):
            raise httpx.ReadTimeout("busy")
            yield  # pragma: no cover

        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _no_answer),
        ):
            events = await _collect_sse_events(await get_explore_document_summary(doc.id, _READER, db=db_session))
        assert events == [{"done": True, "summary": "", "keyPoints": [], "impact": "", "retryAfter": 120}]

    async def test_a_busy_llm_after_some_text_is_a_wait_not_a_partial_answer(self, db_session):
        # The text before "busy" is not an answer: this reader is told to
        # ask again, when it can be had whole.
        doc = _make_doc(db_session)

        async def _then_busy(*_args, **_kwargs):
            yield "SUMMARY: half"
            raise httpx.HTTPStatusError("busy", request=httpx.Request("POST", "http://llm"),
                                        response=httpx.Response(503))

        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _then_busy),
            patch("app.pipeline.analyze.ollama_client.set_cached_llm_result") as mock_set_cache,
        ):
            events = await _collect_sse_events(await get_explore_document_summary(doc.id, _READER, db=db_session))
        assert events[-1] == {"done": True, "summary": "", "keyPoints": [], "impact": "", "retryAfter": 30}
        assert not mock_set_cache.called

    async def test_a_hold_off_is_on_the_text_not_the_document(self, db_session):
        # An unusable output from an empty body doesn't hold off the
        # document once its body is backfilled.
        doc = _make_doc(db_session)

        async def _garbled(*_args, **_kwargs):
            yield ""

        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _garbled),
            patch("app.pipeline.analyze.ollama_client.set_cached_llm_result"),
        ):
            await _collect_sse_events(await get_explore_document_summary(doc.id, _READER, db=db_session))
        doc.body = "The real text, backfilled."
        db_session.commit()
        with (
            patch("app.pipeline.analyze.ollama_client.get_cached_llm_result", return_value=None),
            patch("app.pipeline.analyze.ollama_client.stream_llm", _fake_stream),
            patch("app.pipeline.analyze.ollama_client.set_cached_llm_result"),
        ):
            events = await _collect_sse_events(await get_explore_document_summary(doc.id, _READER, db=db_session))
        assert any("delta" in event for event in events)

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

    async def test_a_blocking_claim_refuses_in_the_same_step_and_claims_nothing(self, db_session):
        from app.api import throttle

        throttle.hold("unusable", ["9"], period=300)
        blocked = throttle.hold("doc", ["9"], period=300, blocked_by=(("unusable", "9", 300),))
        assert isinstance(blocked, throttle.Blocked) and blocked.bucket == "unusable"
        assert throttle.hold("doc", ["9"], period=300) is not None  # nothing was claimed

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
            events = await _collect_sse_events(await get_explore_document_summary(doc.id, _READER, db=db_session))
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
            request = asyncio.create_task(get_explore_document_summary(doc.id, _READER, db=db_session))
            while not explore._generations:  # the generation has started claiming
                await asyncio.sleep(0)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            await asyncio.gather(*list(explore._generations))
        mock_set_cache.assert_called_once()  # finished without its reader
        assert not _document_claimed(doc)
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
            response = await get_explore_document_summary(doc.id, _READER, db=db_session)
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
                await get_explore_document_summary(doc.id, _READER, db=db_session)
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
