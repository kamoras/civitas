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
