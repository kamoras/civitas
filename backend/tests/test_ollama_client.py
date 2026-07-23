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
