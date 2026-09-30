"""Tests for explore_pipeline's document-identity hashing.

_stable_hash exists specifically because Python's built-in hash() on
strings is randomized per-process (PYTHONHASHSEED) — using it to build
ExploreDocument.external_id meant the same real floor speech got a
different ID after every container restart, silently defeating the
dedup check and re-inserting a duplicate row on every deploy (2026-07
audit: 1,758 exact-duplicate rows, 31% of the table).
"""

import asyncio
from types import SimpleNamespace
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import ExploreDocument
from app.pipeline import explore_pipeline
from app.pipeline.explore_pipeline import (
    _backfill_rulemaking_bodies,
    _purge_duplicate_floor_speeches,
    _stable_hash,
    run_explore_pipeline,
)

_BACKEND_ROOT = str(Path(__file__).resolve().parent.parent)



@pytest.fixture(autouse=True)
def _no_real_vector_store(monkeypatch):
    """What the run reads from the vector store, stubbed empty: tests that
    drive the real run_explore_pipeline mustn't depend on a store on disk.
    A test that needs other answers patches over these."""
    monkeypatch.setattr(explore_pipeline, "get_embedded_explore_ids", lambda: set())
    monkeypatch.setattr(explore_pipeline, "get_embedded_hashes", lambda: {})

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
        if doc not in db_session:
            db_session.add(doc)
        db_session.commit()
        return self._rerun(db_session, fetched_body)

    def _rerun(self, db_session, fetched_body):
        # Patched where it is USED, not where it is defined: explore_pipeline
        # binds the name at import, so patching fr_rulemaking's copy leaves
        # the real fetcher in play — and two of the assertions below are
        # `== []`, which a silently-real network call satisfies by accident.
        with patch(
            "app.pipeline.explore_pipeline._fetch_rulemaking_body_text",
            new_callable=AsyncMock,
        ) as fetch:
            fetch.return_value = fetched_body
            self.fetch = fetch
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

    def test_a_complete_short_document_is_fetched_once_ever(self, db_session):
        """The non-convergence, which change-detection alone did not fix.

        Suppressing the re-embed (above) stopped the wasted encoding but
        not the wasted download: the selection matches on body shape, so
        a document whose real full text is genuinely under 2,000 chars
        kept matching after every successful fetch. 476 documents were
        re-downloaded nightly, forever. `body_fetched_at` records the one
        fact the body's shape cannot.
        """
        complete = "Document Headings — a complete but short notice."
        doc = _regulatory_doc(complete)

        assert self._run(db_session, doc, complete) == []
        assert self.fetch.await_count == 1
        assert doc.body_fetched_at is not None

        assert self._rerun(db_session, complete) == []
        assert self.fetch.await_count == 0, "re-fetched an already-fetched document"

    def test_a_failed_fetch_is_retried_on_the_next_run(self, db_session):
        """The other half: convergence must not become giving up.

        A fetch that returns nothing leaves body_fetched_at NULL, so a
        transient Federal Register failure is retried rather than
        permanently marked done.
        """
        doc = _regulatory_doc("stub")

        assert self._run(db_session, doc, "") == []
        assert doc.body_fetched_at is None

        assert self._rerun(db_session, "the real, much longer rule text") == [doc.id]
        assert self.fetch.await_count == 1
        assert doc.body_fetched_at is not None


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

        def blocking_embed(*_args):
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
             patch("app.pipeline.explore_pipeline.top_up_explore_index", blocking_embed), \
             patch("app.pipeline.explore_pipeline._top_up_plan", return_value=([], [])), \
             patch("app.pipeline.explore_pipeline.index_is_whole", return_value=True), \
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


@pytest.mark.asyncio
async def test_an_incomplete_index_is_rebuilt_whole_in_the_run_not_topped_up(db_session):
    # Topped up, it would still not be a complete build — and a background
    # rebuild started after would throw the top-up away and leave the
    # calibration below measuring an empty semantic channel.
    empty = AsyncMock(return_value={})
    rebuild = MagicMock(return_value=3)
    embed = MagicMock(return_value=0)
    with patch("app.pipeline.explore_pipeline.SessionLocal", return_value=db_session), \
         patch("app.pipeline.explore_pipeline.fetch_floor_remarks", empty), \
         patch("app.pipeline.explore_pipeline.fetch_house_floor_remarks", new_callable=AsyncMock, return_value=[]), \
         patch("app.pipeline.explore_pipeline.fetch_recent_presidential_actions",
               new_callable=AsyncMock, return_value=[]), \
         patch("app.pipeline.explore_pipeline.fetch_scotus_cases", new_callable=AsyncMock, return_value=[]), \
         patch("app.pipeline.explore_pipeline.fetch_fr_rulemaking", new_callable=AsyncMock, return_value=[]), \
         patch("app.pipeline.explore_pipeline.index_is_whole", return_value=False), \
         patch("app.pipeline.explore_pipeline.rebuild_explore_index", rebuild), \
         patch("app.pipeline.explore_pipeline.top_up_explore_index", embed), \
         patch("app.pipeline.explore_pipeline.rebuild_index", return_value=0), \
         patch("app.pipeline.explore_pipeline.update_document_authority",
               return_value={"documents": 0, "cited": 0}), \
         patch("app.pipeline.explore_pipeline.calibrate_and_store", return_value={}), \
         patch("app.pipeline.explore_pipeline.api_cache_set"):
        await run_explore_pipeline(days_back=1)
    rebuild.assert_called_once()
    embed.assert_not_called()


def _floor_doc(doc_id: int, ext_id: str, body: str) -> ExploreDocument:
    return ExploreDocument(
        id=doc_id,
        doc_type="Senate Floor Speech",
        source="Congressional Record (GovInfo)",
        title="Floor remarks",
        summary=body[:50],
        body=body,
        date="2026-05-19",
        external_id=ext_id,
    )


class TestDuplicateFloorSpeechPurge:
    """Residue from the hash() bug above, never cleaned up.

    _stable_hash stopped NEW duplicates being created; it did not remove
    the ones already written. Measured on the live corpus: 377
    floor-speech groups, 111 duplicated, 112 redundant documents, 110 of
    them byte-identical. They surface the same speech two or three times
    in one search result page, and skew the corpus sample that
    calibrate_ranking reads its fingerprint length off.
    """

    QUORUM = ("Mr. President, I ask unanimous consent that the order for "
              "the quorum call be rescinded.")

    def test_duplicates_are_removed_keeping_the_earliest(self, db_session):
        body = "Mr. President, this is National Police Week."
        # Same speaker and date, same text, three different legacy hashes —
        # exactly the shape found in production.
        for i, h in enumerate(("2989b8de", "d0f79242", "90a08c74"), start=1):
            db_session.add(_floor_doc(i, f"senate-floor-GRASSLEY-2026-05-19-{h}", body))
        db_session.commit()

        removed = _purge_duplicate_floor_speeches(db_session)

        assert sorted(removed) == [2, 3], "should keep the earliest row only"
        assert db_session.query(ExploreDocument).count() == 1
        assert db_session.query(ExploreDocument).one().id == 1

    def test_identical_boilerplate_from_different_speakers_is_kept(self, db_session):
        """The reason the key is not the body alone.

        Procedural boilerplate is uttered verbatim by different senators
        on the same day. Those are distinct remarks, and deduping on body
        alone would silently delete real content.
        """
        db_session.add(_floor_doc(1, "senate-floor-GRASSLEY-2026-05-19-aaaaaaaa", self.QUORUM))
        db_session.add(_floor_doc(2, "senate-floor-HOEVEN-2026-05-19-bbbbbbbb", self.QUORUM))
        db_session.commit()

        assert _purge_duplicate_floor_speeches(db_session) == []
        assert db_session.query(ExploreDocument).count() == 2

    def test_same_speaker_different_days_is_kept(self, db_session):
        db_session.add(_floor_doc(1, "senate-floor-GRASSLEY-2026-05-19-aaaaaaaa", self.QUORUM))
        db_session.add(_floor_doc(2, "senate-floor-GRASSLEY-2026-05-20-bbbbbbbb", self.QUORUM))
        db_session.commit()

        assert _purge_duplicate_floor_speeches(db_session) == []
        assert db_session.query(ExploreDocument).count() == 2

    def test_a_clean_corpus_is_untouched(self, db_session):
        db_session.add(_floor_doc(1, "senate-floor-GRASSLEY-2026-05-19-aaaaaaaa", "One."))
        db_session.add(_floor_doc(2, "senate-floor-GRASSLEY-2026-05-19-bbbbbbbb", "Two."))
        db_session.commit()

        assert _purge_duplicate_floor_speeches(db_session) == []
        assert db_session.query(ExploreDocument).count() == 2

    def test_non_floor_documents_are_never_touched(self, db_session):
        """Only floor speeches carry a content hash in their id. A Federal
        Register notice's id is the FR document number, so two rows with
        the same body are two real notices, not a duplicate."""
        db_session.add(_regulatory_doc("identical body", doc_id=1))
        db_session.add(_regulatory_doc("identical body", doc_id=2))
        db_session.commit()

        assert _purge_duplicate_floor_speeches(db_session) == []
        assert db_session.query(ExploreDocument).count() == 2


class TestOrphanedVectorPurge:
    """A vector outliving its row is not inert.

    search_explore_documents answers out of vec_explore's own title/
    date/snippet columns without joining back to explore_documents, so
    an orphaned chunk keeps being returned as a hit — with metadata
    nothing can correct. embed_explore_documents only clears vectors for
    documents it is about to re-embed, which by definition still exist,
    so nothing swept these before.
    """

    def test_vectors_without_a_row_are_deleted(self, db_session, monkeypatch):
        db_session.add(_floor_doc(1, "senate-floor-A-2026-05-19-aaaaaaaa", "Kept."))
        db_session.commit()

        deleted: list[set] = []
        monkeypatch.setattr(
            explore_pipeline, "get_embedded_hashes", lambda: dict.fromkeys({1, 2, 3}, ("h", "m")))
        monkeypatch.setattr(
            explore_pipeline, "delete_explore_vectors",
            lambda ids: deleted.append(set(ids)) or len(ids))

        removed = explore_pipeline._purge_orphaned_vectors(db_session)

        assert deleted == [{2, 3}], "must delete only the ids with no row"
        assert removed == 2

    def test_a_consistent_index_is_left_alone(self, db_session, monkeypatch):
        db_session.add(_floor_doc(1, "senate-floor-A-2026-05-19-aaaaaaaa", "Kept."))
        db_session.commit()

        called = []
        monkeypatch.setattr(
            explore_pipeline, "get_embedded_hashes", lambda: dict.fromkeys({1}, ("h", "m")))
        monkeypatch.setattr(
            explore_pipeline, "delete_explore_vectors",
            lambda ids: called.append(ids))

        assert explore_pipeline._purge_orphaned_vectors(db_session) == 0
        assert called == []

    def test_an_unreadable_index_is_not_treated_as_empty(self, db_session, monkeypatch):
        """If the index can't be read, every document looks orphaned.
        Deleting on that basis would wipe the whole vector store."""
        db_session.add(_floor_doc(1, "senate-floor-A-2026-05-19-aaaaaaaa", "Kept."))
        db_session.commit()

        called = []

        def boom():
            raise RuntimeError("index mid-rebuild")

        monkeypatch.setattr(explore_pipeline, "get_embedded_hashes", boom)
        monkeypatch.setattr(
            explore_pipeline, "delete_explore_vectors",
            lambda ids: called.append(ids))

        assert explore_pipeline._purge_orphaned_vectors(db_session) == 0
        assert called == [], "must not delete anything when the index is unreadable"


    @pytest.mark.parametrize("error,raised", [("database is locked", False), ("disk I/O error", True)])
    def test_a_locked_delete_is_left_to_the_next_run(self, db_session, monkeypatch, error, raised):
        import sqlite3

        monkeypatch.setattr(explore_pipeline, "get_embedded_hashes", lambda: {9: ("h", "m")})
        monkeypatch.setattr(
            explore_pipeline, "delete_explore_vectors",
            MagicMock(side_effect=sqlite3.OperationalError(error)))
        if raised:
            with pytest.raises(sqlite3.OperationalError):
                explore_pipeline._purge_orphaned_vectors(db_session)
        else:
            assert explore_pipeline._purge_orphaned_vectors(db_session) == 0

@pytest.mark.asyncio
async def test_a_run_that_waited_out_a_rebuild_purges_again_and_resolves_the_alert(db_session):
    # The start's rebuild reads by id without this run's lease: a document
    # the run deleted may have been embedded after the first purge. And the
    # index it leaves whole ends a failed rebuild's alert.
    empty = AsyncMock(return_value={})
    purge = MagicMock(return_value=0)
    resolve = MagicMock()
    with patch("app.pipeline.explore_pipeline.SessionLocal", return_value=db_session), \
         patch("app.pipeline.explore_pipeline.fetch_floor_remarks", empty), \
         patch("app.pipeline.explore_pipeline.fetch_house_floor_remarks", new_callable=AsyncMock, return_value=[]), \
         patch("app.pipeline.explore_pipeline.fetch_recent_presidential_actions",
               new_callable=AsyncMock, return_value=[]), \
         patch("app.pipeline.explore_pipeline.fetch_scotus_cases", new_callable=AsyncMock, return_value=[]), \
         patch("app.pipeline.explore_pipeline.fetch_fr_rulemaking", new_callable=AsyncMock, return_value=[]), \
         patch("app.pipeline.explore_pipeline.index_is_whole", side_effect=[False, True]), \
         patch("app.pipeline.explore_pipeline.rebuild_explore_index", return_value=None), \
         patch("app.pipeline.explore_pipeline.top_up_explore_index", return_value=0), \
         patch("app.pipeline.explore_pipeline._purge_orphaned_vectors", purge), \
         patch("app.ops_alerts.resolve_ops_alert", resolve), \
         patch("app.pipeline.explore_pipeline.rebuild_index", return_value=0), \
         patch("app.pipeline.explore_pipeline.update_document_authority",
               return_value={"documents": 0, "cited": 0}), \
         patch("app.pipeline.explore_pipeline.calibrate_and_store", return_value={}), \
         patch("app.pipeline.explore_pipeline.api_cache_set"):
        await run_explore_pipeline(days_back=1)
    assert purge.call_count == 2
    resolve.assert_called_once_with("explore-index-rebuild")


@pytest.mark.asyncio
async def test_a_run_facing_a_locked_index_neither_rebuilds_nor_tops_it_up(db_session):
    # It may well be whole (no reason to drop it), and it may not be (a
    # top-up beside a rebuild inserts chunks twice); nor does a look that
    # never answered resolve a failed rebuild's alert.
    import sqlite3

    empty = AsyncMock(return_value={})
    rebuild, embed, resolve = MagicMock(), MagicMock(return_value=0), MagicMock()
    with patch("app.pipeline.explore_pipeline.SessionLocal", return_value=db_session), \
         patch("app.pipeline.explore_pipeline.fetch_floor_remarks", empty), \
         patch("app.pipeline.explore_pipeline.fetch_house_floor_remarks", new_callable=AsyncMock, return_value=[]), \
         patch("app.pipeline.explore_pipeline.fetch_recent_presidential_actions",
               new_callable=AsyncMock, return_value=[]), \
         patch("app.pipeline.explore_pipeline.fetch_scotus_cases", new_callable=AsyncMock, return_value=[]), \
         patch("app.pipeline.explore_pipeline.fetch_fr_rulemaking", new_callable=AsyncMock, return_value=[]), \
         patch("app.pipeline.explore_pipeline.index_is_whole",
               side_effect=sqlite3.OperationalError("database is locked")), \
         patch("app.pipeline.explore_pipeline.rebuild_explore_index", rebuild), \
         patch("app.pipeline.explore_pipeline.top_up_explore_index", embed), \
         patch("app.pipeline.explore_pipeline._purge_orphaned_vectors", return_value=0), \
         patch("app.ops_alerts.resolve_ops_alert", resolve), \
         patch("app.pipeline.explore_pipeline.rebuild_index", return_value=0), \
         patch("app.pipeline.explore_pipeline.update_document_authority",
               return_value={"documents": 0, "cited": 0}), \
         patch("app.pipeline.explore_pipeline.calibrate_and_store", return_value={}), \
         patch("app.pipeline.explore_pipeline.api_cache_set"):
        await run_explore_pipeline(days_back=1)
    rebuild.assert_not_called()
    embed.assert_not_called()
    resolve.assert_not_called()


def test_ranking_calibration_is_kept_while_the_vector_index_is_not_whole(db_session):
    # Measured against a semantic channel answering nothing, the priors come
    # out as if the channels agreed perfectly.
    from app.pipeline import explore_ranking

    with patch("app.pipeline.vector_store.index_is_whole", return_value=False), \
         patch("app.pipeline.calibrate_ranking.compute_calibration", side_effect=AssertionError("measured")):
        assert explore_ranking.calibrate_and_store(db_session) is None


def test_a_busy_vector_index_keeps_the_calibration_without_calling_it_a_failure(db_session, caplog):
    import sqlite3

    from app.pipeline import explore_ranking

    with patch("app.pipeline.vector_store.index_is_whole",
               side_effect=sqlite3.OperationalError("database is locked")), \
         patch("app.pipeline.calibrate_ranking.compute_calibration", side_effect=AssertionError("measured")):
        assert explore_ranking.calibrate_and_store(db_session) is None
    assert "calibration failed" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("error,alerted", [
    ("database is locked", False),  # a lock: the next run tries, no page
    ("no such module: vec0", True),  # a real failure
])
async def test_a_rebuild_that_raises_alerts_only_on_a_real_failure(
    db_session, error, alerted,
):
    import sqlite3

    from app.pipeline import explore_pipeline

    alert, resolve = MagicMock(), MagicMock()
    with patch.object(explore_pipeline, "index_is_whole", return_value=False), \
         patch.object(explore_pipeline, "rebuild_explore_index", side_effect=sqlite3.OperationalError(error)), \
         patch.object(explore_pipeline, "top_up_explore_index") as top_up, \
         patch("app.ops_alerts.send_ops_alert", alert), \
         patch("app.ops_alerts.resolve_ops_alert", resolve):
        assert await explore_pipeline._embed_step(db_session) == 0
    assert alert.called is alerted
    resolve.assert_not_called()
    top_up.assert_not_called()


@pytest.mark.asyncio
async def test_a_rebuild_that_completes_resolves_the_alert(db_session):
    from app.pipeline import explore_pipeline

    resolve = MagicMock()
    with patch.object(explore_pipeline, "index_is_whole", return_value=False), \
         patch.object(explore_pipeline, "rebuild_explore_index", return_value=0), \
         patch("app.ops_alerts.resolve_ops_alert", resolve):
        # Even an empty corpus: the rebuild completed, whatever it held.
        assert await explore_pipeline._embed_step(db_session) == 0
    resolve.assert_called_once_with("explore-index-rebuild")


@pytest.mark.asyncio
@pytest.mark.parametrize("error,raised", [("database is locked", False), ("disk I/O error", True)])
async def test_a_top_up_whose_read_of_the_index_fails_embeds_nothing(db_session, error, raised):
    # Its reading of what is embedded too: unreadable must not read as
    # empty and re-encode the whole corpus.
    import sqlite3

    from app.pipeline import explore_pipeline

    with patch.object(explore_pipeline, "index_is_whole", return_value=True), \
         patch.object(explore_pipeline, "get_embedded_hashes", side_effect=sqlite3.OperationalError(error)), \
         patch.object(explore_pipeline, "embed_explore_documents", create=True) as embed, \
         patch("app.pipeline.vector_store.embed_explore_documents") as real_embed:
        if raised:
            with pytest.raises(sqlite3.OperationalError):
                await explore_pipeline._embed_step(db_session)
        else:
            assert await explore_pipeline._embed_step(db_session) == 0
    embed.assert_not_called()
    real_embed.assert_not_called()


@pytest.mark.asyncio
async def test_a_lock_after_the_rebuilds_swap_is_a_failure_not_a_skip(db_session):
    # The index is gone by then: no alert, and a log saying "busy", would
    # leave a partial index unreported until the next run.
    import sqlite3

    from app.pipeline import explore_pipeline, vector_store

    def failing(*_a, **_k):
        raise vector_store.RebuildFailed("after the swap") from sqlite3.OperationalError("database is locked")

    alert = MagicMock()
    with patch.object(explore_pipeline, "index_is_whole", return_value=False), \
         patch.object(explore_pipeline, "rebuild_explore_index", failing), \
         patch.object(explore_pipeline, "alert_rebuild_failed", alert):
        assert await explore_pipeline._embed_step(db_session) == 0
    alert.assert_called_once()


def test_a_top_up_takes_documents_missing_or_changed_since_they_were_embedded(db_session):
    # By each document's text hash, not by who changed it: a body backfilled
    # or re-ingested in place is stale whichever path wrote it; one with no
    # text, its hash recorded, is handled.
    from app.pipeline import explore_pipeline, vector_store

    docs = []
    for title, body in (("Current", "b"), ("Changed", "b"), ("Missing", "b"), ("", "")):
        d = ExploreDocument(doc_type="Executive Order", source="Federal Register", title=title,
                            summary="" if not title else "s", body=body, date="2026-07-01")
        db_session.add(d)
        docs.append(d)
    db_session.commit()
    current, changed, missing, textless = docs

    def hashed(d):
        return vector_store.explore_text_hash(vector_store.explore_embed_dict(d))

    def meta(d):
        return vector_store.explore_meta_hash(vector_store.explore_embed_dict(d))

    held = {
        current.id: (hashed(current), "a chamber since corrected"),
        changed.id: ("its old body's", meta(changed)),
        textless.id: (hashed(textless), meta(textless)),
    }
    with patch.object(explore_pipeline, "get_embedded_hashes", return_value=held):
        embed, relabel = explore_pipeline._top_up_plan(db_session)
    # Each with the hash it is written with and the one the index held.
    assert [(doc["title"], digest, was) for doc, digest, was in embed] == [
        ("Changed", hashed(changed), "its old body's"), ("Missing", hashed(missing), None)]
    # Metadata alone changed: written in place, not re-encoded.
    # The plan keeps only what the relabel writes, not the text it skips.
    assert [(doc["id"], digest, was) for doc, digest, was in relabel] == [
        (current.id, meta(current), "a chamber since corrected")]
    assert set(relabel[0][0]) == {"id", *vector_store._META_FIELDS}


def test_the_plan_is_rechecked_against_the_index_under_the_lock(db_session):
    # Only documents the index still holds as the plan found them: one a
    # rebuild waited out wrote since was read from the database later than
    # the plan — not embedded twice, nor put back to the plan's older text.
    from app.pipeline import explore_pipeline, vector_store

    doc = vector_store.explore_embed_dict(SimpleNamespace(
        id=5, title="t", summary="s", body="b", doc_type="Rule", source="FR", date="",
        politician_name="", politician_id="", chamber=""))
    current = vector_store.explore_text_hash(doc)
    plan = [(doc, current, "old")]
    for held, wanted in (
        ({5: ("old", "m")}, [{**doc, "_text_hash": current}]),
        ({5: (current, "m")}, []),
        ({5: ("newer than the plan", "m")}, []),
        ({}, []),
    ):
        with patch.object(explore_pipeline, "get_embedded_hashes", return_value=held):
            assert explore_pipeline._still_wanted(plan) == wanted
    never_embedded = [(doc, current, None)]
    with patch.object(explore_pipeline, "get_embedded_hashes", return_value={}):
        assert explore_pipeline._still_wanted(never_embedded) == [{**doc, "_text_hash": current}]
    relabel = [({"id": 5}, "new meta", "old meta")]
    for held, wanted in (({5: (current, "old meta")}, [{"id": 5}]),
                         ({5: (current, "rebuilt meta")}, []), ({}, [])):
        with patch.object(explore_pipeline, "get_embedded_hashes", return_value=held):
            assert explore_pipeline._still_to_relabel(relabel) == wanted


