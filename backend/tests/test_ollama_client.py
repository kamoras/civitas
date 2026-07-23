"""Tests for call_llm's caching behavior.

Regression coverage for a 2026-07-15 production incident: cache_key=None
was meant to disable caching entirely (several callers pass it explicitly
for time-sensitive or always-fresh generations — role-reversal re-checks,
Bluesky post text, spotlight highlights), but _make_input_hash JSON-
serialized None to the constant string "null", so every call sharing a
prompt_version and model collided on the same cache row regardless of
actual content. A single stale role-check verdict about an unrelated
story got cached under that shared slot and was returned for every
subsequent role-check regardless of topic, silently rejecting every
generated action-center summary for 26+ hours.
"""

import json
from unittest.mock import patch

from app.models import AnalysisCache
from app.pipeline.analyze import ollama_client


def _patched_call(db_session, responses):
    """Patch both backend callers to return successive JSON responses, and
    point the cache helpers' own SessionLocal() at the test's in-memory db
    (they open their own short-lived session rather than reusing the one
    passed in)."""
    it = iter(responses)
    return (
        patch(
            "app.pipeline.analyze.ollama_client._call_llama_server",
            side_effect=lambda *a, **k: json.dumps(next(it)),
        ),
        patch(
            "app.pipeline.analyze.ollama_client._call_ollama",
            side_effect=lambda *a, **k: json.dumps(next(it)),
        ),
        patch("app.pipeline.analyze.ollama_client.SessionLocal", return_value=db_session),
    )


class TestCacheKeyNoneNeverCaches:
    def test_two_calls_with_cache_key_none_each_hit_the_backend(self, db_session):
        """The core regression: two different cache_key=None calls with the
        same prompt_version/model must NOT share a result — each must
        actually invoke the backend and get its own answer."""
        p1, p2, p3 = _patched_call(
            db_session,
            [{"accurate": False, "reason": "unrelated stale reason"}, {"accurate": True}],
        )
        with p1, p2, p3:
            first = ollama_client.call_llm(
                prompt_version="role-check-v1",
                system_prompt="sys",
                user_prompt="check summary A",
                cache_key=None,
                db_session=db_session,
            )
            second = ollama_client.call_llm(
                prompt_version="role-check-v1",
                system_prompt="sys",
                user_prompt="check summary B (completely different topic)",
                cache_key=None,
                db_session=db_session,
            )

        assert first == {"accurate": False, "reason": "unrelated stale reason"}
        assert second == {"accurate": True}
        assert second != first

    def test_cache_key_none_writes_no_row(self, db_session):
        p1, p2, p3 = _patched_call(db_session, [{"ok": True}])
        with p1, p2, p3:
            ollama_client.call_llm(
                prompt_version="never-cached-v1",
                system_prompt="sys",
                user_prompt="anything",
                cache_key=None,
                db_session=db_session,
            )
        assert db_session.query(AnalysisCache).count() == 0


class TestRealCacheKeyStillCaches:
    """Unaffected-behavior guard: explicit cache keys must still work."""

    def test_same_cache_key_is_a_cache_hit(self, db_session):
        p1, p2, p3 = _patched_call(db_session, [{"result": "first"}])
        with p1, p2, p3:
            first = ollama_client.call_llm(
                prompt_version="cached-v1",
                system_prompt="sys",
                user_prompt="prompt",
                cache_key={"id": 42},
                db_session=db_session,
            )
            # Second call would raise StopIteration on the backend mock if
            # it actually reached the network — proves this is a cache hit.
            second = ollama_client.call_llm(
                prompt_version="cached-v1",
                system_prompt="sys",
                user_prompt="prompt",
                cache_key={"id": 42},
                db_session=db_session,
            )
        assert first == second == {"result": "first"}

    def test_different_cache_keys_are_cache_misses(self, db_session):
        p1, p2, p3 = _patched_call(db_session, [{"result": "A"}, {"result": "B"}])
        with p1, p2, p3:
            first = ollama_client.call_llm(
                prompt_version="cached-v2",
                system_prompt="sys",
                user_prompt="prompt",
                cache_key={"id": 1},
                db_session=db_session,
            )
            second = ollama_client.call_llm(
                prompt_version="cached-v2",
                system_prompt="sys",
                user_prompt="prompt",
                cache_key={"id": 2},
                db_session=db_session,
            )
        assert first == {"result": "A"}
        assert second == {"result": "B"}

    def test_real_cache_key_writes_a_row(self, db_session):
        p1, p2, p3 = _patched_call(db_session, [{"result": "written"}])
        with p1, p2, p3:
            ollama_client.call_llm(
                prompt_version="cached-v3",
                system_prompt="sys",
                user_prompt="prompt",
                cache_key={"id": 99},
                db_session=db_session,
            )
        assert db_session.query(AnalysisCache).count() == 1

    def test_no_db_session_never_caches_even_with_real_key(self, db_session):
        p1, p2, p3 = _patched_call(db_session, [{"result": "A"}, {"result": "B"}])
        with p1, p2, p3:
            first = ollama_client.call_llm(
                prompt_version="no-session-v1",
                system_prompt="sys",
                user_prompt="prompt",
                cache_key={"id": 1},
                db_session=None,
            )
            second = ollama_client.call_llm(
                prompt_version="no-session-v1",
                system_prompt="sys",
                user_prompt="prompt",
                cache_key={"id": 1},
                db_session=None,
            )
        assert first == {"result": "A"}
        assert second == {"result": "B"}


class TestStreamingCacheHelpers:
    """get_cached_llm_result/set_cached_llm_result — the public cache
    read/write pair explore.py's streaming summary endpoint uses instead
    of call_llm's own cache logic, since a streaming caller parses its
    own output and can't reuse call_llm's JSON-extraction retry loop."""

    def test_write_then_read_round_trips(self, db_session):
        with patch("app.pipeline.analyze.ollama_client.SessionLocal", return_value=db_session):
            ollama_client.set_cached_llm_result(
                "explore-doc-summary-v4", {"doc_id": 7, "v": 4}, {"summary": "s", "keyPoints": [], "impact": ""},
            )
            result = ollama_client.get_cached_llm_result("explore-doc-summary-v4", {"doc_id": 7, "v": 4})
        assert result == {"summary": "s", "keyPoints": [], "impact": ""}

    def test_miss_returns_none(self, db_session):
        with patch("app.pipeline.analyze.ollama_client.SessionLocal", return_value=db_session):
            result = ollama_client.get_cached_llm_result("explore-doc-summary-v4", {"doc_id": 999, "v": 4})
        assert result is None

    def test_different_cache_keys_do_not_collide(self, db_session):
        with patch("app.pipeline.analyze.ollama_client.SessionLocal", return_value=db_session):
            ollama_client.set_cached_llm_result(
                "explore-doc-summary-v4", {"doc_id": 1, "v": 4}, {"summary": "A", "keyPoints": [], "impact": ""},
            )
            ollama_client.set_cached_llm_result(
                "explore-doc-summary-v4", {"doc_id": 2, "v": 4}, {"summary": "B", "keyPoints": [], "impact": ""},
            )
            first = ollama_client.get_cached_llm_result("explore-doc-summary-v4", {"doc_id": 1, "v": 4})
            second = ollama_client.get_cached_llm_result("explore-doc-summary-v4", {"doc_id": 2, "v": 4})
        assert first["summary"] == "A"
        assert second["summary"] == "B"


class _FakeStreamResponse:
    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStreamCtx:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return _FakeStreamResponse(self._lines)

    async def __aexit__(self, *exc):
        return False


class _FakeAsyncClient:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, **kwargs):
        return _FakeStreamCtx(self._lines)


def _patched_httpx(lines):
    """Patch httpx.AsyncClient so _stream_llama_server/_stream_ollama read
    canned wire-format lines instead of making a real network call — same
    role _patched_call plays for the non-streaming backend callers above."""
    return patch(
        "app.pipeline.analyze.ollama_client.httpx.AsyncClient",
        side_effect=lambda **kw: _FakeAsyncClient(lines),
    )


class TestStreamLlamaServer:
    async def test_yields_deltas_and_stops_at_done_sentinel(self):
        lines = [
            'data: {"choices":[{"delta":{"content":"Hello"}}]}',
            'data: {"choices":[{"delta":{"content":" world"}}]}',
            "data: [DONE]",
            # A line after [DONE] must never be reached.
            'data: {"choices":[{"delta":{"content":"unreachable"}}]}',
        ]
        with _patched_httpx(lines):
            deltas = [d async for d in ollama_client._stream_llama_server("sys", "prompt", 512, 4096, 120)]
        assert deltas == ["Hello", " world"]

    async def test_skips_non_data_lines(self):
        lines = [
            "",
            ": keep-alive comment",
            'data: {"choices":[{"delta":{"content":"ok"}}]}',
            "data: [DONE]",
        ]
        with _patched_httpx(lines):
            deltas = [d async for d in ollama_client._stream_llama_server("sys", "prompt", 512, 4096, 120)]
        assert deltas == ["ok"]


class TestStreamOllama:
    async def test_yields_deltas_and_stops_at_done_true(self):
        lines = [
            '{"response":"Hi","done":false}',
            '{"response":" there","done":false}',
            '{"response":"","done":true}',
            '{"response":"unreachable","done":false}',
        ]
        with _patched_httpx(lines):
            deltas = [
                d async for d in ollama_client._stream_ollama("sys", "prompt", "some-model", 512, 4096, 120)
            ]
        assert deltas == ["Hi", " there"]

    async def test_blank_lines_skipped(self):
        lines = ["", '{"response":"x","done":true}']
        with _patched_httpx(lines):
            deltas = [
                d async for d in ollama_client._stream_ollama("sys", "prompt", "some-model", 512, 4096, 120)
            ]
        assert deltas == ["x"]

    async def test_truncation_logs_warning_but_still_yields_partial_output(self, caplog):
        lines = ['{"response":"partial","done":true,"done_reason":"length"}']
        with _patched_httpx(lines):
            deltas = [
                d async for d in ollama_client._stream_ollama("sys", "prompt", "some-model", 512, 4096, 120)
            ]
        assert deltas == ["partial"]
        assert "truncated" in caplog.text


class TestStreamLlmDispatch:
    async def test_dispatches_to_llama_server_backend(self):
        lines = ['data: {"choices":[{"delta":{"content":"a"}}]}', "data: [DONE]"]
        with (
            patch("app.pipeline.analyze.ollama_client.settings.LLM_BACKEND", "llama-server"),
            _patched_httpx(lines),
        ):
            deltas = [d async for d in ollama_client.stream_llm(system_prompt="s", user_prompt="u")]
        assert deltas == ["a"]

    async def test_dispatches_to_ollama_backend(self):
        lines = ['{"response":"b","done":true}']
        with (
            patch("app.pipeline.analyze.ollama_client.settings.LLM_BACKEND", "ollama"),
            _patched_httpx(lines),
        ):
            deltas = [d async for d in ollama_client.stream_llm(system_prompt="s", user_prompt="u")]
        assert deltas == ["b"]


class TestCacheHelperExceptionHandling:
    def test_get_cached_result_returns_none_on_backend_error(self, db_session):
        with patch("app.pipeline.analyze.ollama_client.SessionLocal", side_effect=RuntimeError("db down")):
            assert ollama_client.get_cached_llm_result("v1", {"id": 1}) is None

    def test_set_cached_result_swallows_backend_error(self, db_session):
        with patch("app.pipeline.analyze.ollama_client.SessionLocal", side_effect=RuntimeError("db down")):
            ollama_client.set_cached_llm_result("v1", {"id": 1}, {"summary": "s"})  # must not raise


class TestExtractJsonRobustness:
    def test_stray_bracket_prefix_before_object(self):
        from app.pipeline.analyze.ollama_client import extract_json
        out = extract_json('[Note] Here is the result: {"summary": "ok", "keyPoints": ["a"]}')
        assert out == {"summary": "ok", "keyPoints": ["a"]}

    def test_unterminated_think_block_stripped(self):
        from app.pipeline.analyze.ollama_client import extract_json
        # A length-truncated reasoning trace never closes its <think> tag.
        out = extract_json('<think>reasoning that got cut off {"a": 1')
        assert out is None  # no complete JSON, but doesn't crash

    def test_plain_object_still_parses(self):
        from app.pipeline.analyze.ollama_client import extract_json
        assert extract_json('{"x": 1}') == {"x": 1}

    def test_array_still_parses(self):
        from app.pipeline.analyze.ollama_client import extract_json
        assert extract_json('prefix [1, 2, 3] suffix') == [1, 2, 3]
