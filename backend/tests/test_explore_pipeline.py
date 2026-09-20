"""Tests for explore_pipeline's document-identity hashing.

_stable_hash exists specifically because Python's built-in hash() on
strings is randomized per-process (PYTHONHASHSEED) — using it to build
ExploreDocument.external_id meant the same real floor speech got a
different ID after every container restart, silently defeating the
dedup check and re-inserting a duplicate row on every deploy (2026-07
audit: 1,758 exact-duplicate rows, 31% of the table).
"""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.models import ExploreDocument
from app.pipeline.explore_pipeline import (
    _backfill_rulemaking_bodies,
    _stable_hash,
    run_explore_pipeline,
)

_BACKEND_ROOT = str(Path(__file__).resolve().parent.parent)


class TestStableHash:
    def test_same_input_same_output(self):
        text = "Mr. Speaker, I rise today to commend the bipartisan effort..."
        assert _stable_hash(text) == _stable_hash(text)

    def test_different_input_different_output(self):
        a = _stable_hash("Remarks about infrastructure funding.")
        b = _stable_hash("Remarks about veterans healthcare.")
        assert a != b

    def test_returns_eight_hex_chars(self):
        result = _stable_hash("some remark text")
        assert len(result) == 8
        int(result, 16)  # raises if not valid hex

    def test_immune_to_pythonhashseed(self):
        """The whole point: unlike hash(), this must not depend on
        PYTHONHASHSEED. Force two different seeds explicitly (rather than
        relying on the default per-process random seed, which could
        coincidentally agree) and confirm the digest is identical."""
        script = (
            f"import sys; sys.path.insert(0, {_BACKEND_ROOT!r}); "
            "from app.pipeline.explore_pipeline import _stable_hash; "
            "print(_stable_hash('a floor speech about the budget'))"
        )
        outputs = set()
        for seed in ("0", "12345"):
            env = {**os.environ, "PYTHONHASHSEED": seed}
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True, text=True, env=env,
            )
            assert result.returncode == 0, result.stderr
            outputs.add(result.stdout.strip())
        assert len(outputs) == 1, f"hash varied by PYTHONHASHSEED: {outputs}"


def _regulatory_doc(body: str, doc_id: int = 1) -> ExploreDocument:
    """A Federal Register notice shaped the way the backfill query finds
    them: Regulatory, has a URL with a dated path, body under 2000 chars."""
    return ExploreDocument(
        id=doc_id,
        doc_type="Rule",
        source="Federal Register",
        title="A short notice",
        summary="summary",
        body=body,
        date="2026-09-01",
        url="https://www.federalregister.gov/documents/2026/09/01/2026-12345/a-short-notice",
        chamber="Regulatory",
        external_id=f"fr-reg-2026-1234{doc_id}",
    )


class TestRulemakingBackfillChangeDetection:
    """A re-fetch returning the text already stored is not a refresh.

    The query selects on `length(body) < 2000`, and a short Federal
    Register notice whose real full text is genuinely under that never
    stops matching it: 469 real documents did on 2026-09-20, every one
    already complete (longest body: 1,999 characters). The ids returned
    here drive step 7's re-embed, so reporting an unchanged document as
    refreshed re-chunked and re-encoded it into byte-identical vectors
    every single night, forever, for zero new information.
    """

    def _run(self, db_session, doc, fetched_body):
        db_session.add(doc)
        db_session.commit()
        with patch(
            "app.pipeline.fetch.fr_rulemaking._fetch_body_text",
            new_callable=AsyncMock,
        ) as fetch:
            fetch.return_value = fetched_body
            return asyncio.run(_backfill_rulemaking_bodies(db_session))

    def test_an_unchanged_body_is_not_reported_as_refreshed(self, db_session):
        complete = "Document Headings — a complete but short notice."
        doc = _regulatory_doc(complete)
        assert self._run(db_session, doc, complete) == []
        assert doc.body == complete

    def test_a_genuinely_longer_body_is_reported(self, db_session):
        doc = _regulatory_doc("stub")
        assert self._run(db_session, doc, "the real, much longer rule text") == [1]
        assert doc.body == "the real, much longer rule text"

    def test_a_failed_fetch_never_blanks_a_stored_body(self, db_session):
        doc = _regulatory_doc("already stored text")
        assert self._run(db_session, doc, "") == []
        assert doc.body == "already stored text"


class TestCpuWorkDoesNotBlockTheEventLoop:
    """The regression that actually broke production for 19 nights.

    `embed_explore_documents` is synchronous and pure CPU (sentence-
    transformers), and ran for 23 MINUTES in one call against the real
    corpus. Awaited inline it froze the whole FastAPI event loop, so
    /health stopped answering, Swarm's healthcheck (30s interval, 10s
    timeout) failed, and the container was SIGKILLed mid-run (exit 137,
    "unhealthy container"). The killed process left its run row "active",
    so House/Stock/Election -- chained behind this phase -- never ran
    again from 2026-09-01 onward.

    What must hold is not "to_thread appears in the source" but the
    property that broke: while the CPU step runs, other coroutines (the
    /health handler among them) still get scheduled.
    """

    @pytest.mark.asyncio
    async def test_the_pipelines_own_embed_step_does_not_freeze_the_loop(self, db_session):
        """Drives the REAL run_explore_pipeline, with only its outside
        world stubbed, and a deliberately blocking embed standing in for
        the real encode. Reverting the `asyncio.to_thread` at that call
        site fails this: the heartbeat stops dead for the whole call."""
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.01)

        embed_started = asyncio.Event()

        def blocking_embed(_docs):
            loop.call_soon_threadsafe(embed_started.set)
            time.sleep(0.4)  # stands in for the real 23-minute encode
            return 0

        loop = asyncio.get_running_loop()
        empty = AsyncMock(return_value={})

        with patch("app.pipeline.explore_pipeline.SessionLocal", return_value=db_session), \
             patch("app.pipeline.explore_pipeline.fetch_floor_remarks", empty), \
             patch("app.pipeline.explore_pipeline.fetch_house_floor_remarks",
                   new_callable=AsyncMock, return_value=[]), \
             patch("app.pipeline.explore_pipeline.fetch_recent_presidential_actions",
                   new_callable=AsyncMock, return_value=[]), \
             patch("app.pipeline.explore_pipeline.fetch_scotus_cases",
                   new_callable=AsyncMock, return_value=[]), \
             patch("app.pipeline.explore_pipeline.fetch_fr_rulemaking",
                   new_callable=AsyncMock, return_value=[]), \
             patch("app.pipeline.explore_pipeline.embed_explore_documents", blocking_embed), \
             patch("app.pipeline.explore_pipeline.rebuild_index", return_value=0), \
             patch("app.pipeline.explore_pipeline.update_document_authority",
                   return_value={"documents": 0, "cited": 0}), \
             patch("app.pipeline.explore_pipeline.calibrate_and_store", return_value={}), \
             patch("app.pipeline.explore_pipeline.api_cache_set"):
            beat = asyncio.create_task(heartbeat())
            run = asyncio.create_task(run_explore_pipeline(days_back=1))
            await embed_started.wait()
            before = ticks
            await run
            after = ticks
            beat.cancel()

        assert after - before > 5, (
            f"event loop only ticked {after - before} times while the CPU-bound "
            "embed step ran — it is blocking the loop, which is what got the "
            "container healthcheck-killed (exit 137) in production"
        )
