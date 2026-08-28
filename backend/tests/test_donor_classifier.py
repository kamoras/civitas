"""Tests for the hybrid donor classifier.

Tests the tiered classification strategy:
1. FEC committee type codes (structured metadata)
2. Semantic embedding-based classification
3. Learning store lookup
4. kNN fallback
"""

import pytest
from unittest.mock import patch

from app.models import LearnedClassification
from app.pipeline.analyze.donor_classifier_ai import (
    FEC_ENTITY_TYPE_MAP,
    classify_donor_type_from_fec,
    classify_donor_type_semantic,
    classify_employer_skips_batch,
    classify_skip_names_batch,
    classify_transfer_memos_batch,
    is_skip_entity,
    classify_donors_hybrid,
)


class TestFECTypeClassification:
    """Tier 1: FEC entity type and receipt type codes."""

    @pytest.mark.parametrize(
        "entity_type, expected",
        [
            ("PAC", "PAC"),
            ("ORG", "Org/Employees"),
            ("IND", "Org/Employees"),
            ("CCM", "CandidateAffiliated"),
            ("CAN", "Self-Funded"),
            ("PTY", "Party/Ideological"),
        ],
    )
    def test_known_entity_types(self, entity_type, expected):
        receipt = {"entity_type": entity_type}
        assert classify_donor_type_from_fec(receipt) == expected

    def test_com_defers_to_semantic_classifier(self):
        """COM (generic committee) is ambiguous — returns None to defer to
        embedding-based classification which can distinguish corporate
        employee PACs from purely political PACs."""
        receipt = {"entity_type": "COM"}
        assert classify_donor_type_from_fec(receipt) is None

    def test_affiliated_receipt_types(self):
        for rt in ("18G", "18H", "18K", "18J", "22G", "22H"):
            receipt = {"receipt_type": rt}
            assert classify_donor_type_from_fec(receipt) == "CandidateAffiliated"

    def test_unknown_entity_type_returns_none(self):
        receipt = {"entity_type": "ZZZ"}
        assert classify_donor_type_from_fec(receipt) is None

    def test_missing_fields_returns_none(self):
        assert classify_donor_type_from_fec({}) is None

    def test_fec_entity_type_map_covers_expected_codes(self):
        assert len(FEC_ENTITY_TYPE_MAP) == 6

class TestSkipDetection:
    """Tier 2: Payment processor skip detection."""

    @pytest.mark.parametrize(
        "name",
        ["WINRED TECHNICAL SERVICES", "ACTBLUE", "ANEDOT INC"],
    )
    def test_skip_entities(self, name):
        assert is_skip_entity(name) is True

    def test_non_skip_entities(self):
        assert is_skip_entity("PFIZER INC") is False
        assert is_skip_entity("GOLDMAN SACHS") is False


class TestSemanticClassification:
    """Tier 2: Embedding-based semantic donor type classification."""

    def test_candidate_self_funded_personal_contribution(self):
        """When donor name matches the candidate's name, it's a self-funded contribution."""
        result = classify_donor_type_semantic(
            "CRUZ, RAPHAEL EDWARD TED",
            candidate_name="CRUZ, RAFAEL EDWARD (TED)",
        )
        assert result == "Self-Funded"

    def test_returns_none_for_empty_name(self):
        assert classify_donor_type_semantic("") is None
        assert classify_donor_type_semantic("AB") is None

    def test_a_victory_fund_committee_is_not_self_funded(self):
        """A 2026-08 audit found "Rutherford Victory Fund" (and similar
        candidate-surname committee names) classified Self-Funded — the
        SequenceMatcher ratio between a short committee name and the
        candidate's own name can clear the self-funded threshold purely
        because the surname dominates the string, even though a joint
        fundraising committee's money is split with party/PAC committees
        and is never the candidate's own. This must land as
        CandidateAffiliated instead (the same bucket a "Friends of X" or
        "X for Senate" committee lands in), never Self-Funded."""
        result = classify_donor_type_semantic(
            "RUTHERFORD VICTORY FUND",
            candidate_name="RUTHERFORD, JOHN",
        )
        assert result != "Self-Funded"

    def test_a_leadership_pac_named_for_the_candidate_is_not_self_funded(self):
        result = classify_donor_type_semantic(
            "SMITH LEADERSHIP PAC",
            candidate_name="SMITH, JANE",
        )
        assert result != "Self-Funded"


@pytest.mark.slow
class TestHybridClassification:
    """Integration: full tiered classification via classify_donors_hybrid."""

    @pytest.mark.asyncio
    async def test_empty_input(self):
        result = await classify_donors_hybrid([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_fec_tier(self, db_session):
        donors = [
            {
                "name": "Test PAC",
                "amount": 5000,
                "fec_receipt": {"entity_type": "PAC"},
            }
        ]
        result = await classify_donors_hybrid(donors, db_session=db_session)
        assert "TEST PAC" in result
        assert result["TEST PAC"]["type"] == "PAC"

    @pytest.mark.asyncio
    async def test_skip_tier(self, db_session):
        donors = [{"name": "ACTBLUE", "amount": 1000}]
        result = await classify_donors_hybrid(donors, db_session=db_session)
        assert "ACTBLUE" in result
        assert result["ACTBLUE"]["type"] == "SKIP"
        assert result["ACTBLUE"]["skip"] is True

    @pytest.mark.asyncio
    async def test_learning_store_tier(self, db_session):
        db_session.add(LearnedClassification(
            entity_name="MYSTERY DONOR",
            entity_type="donor_type",
            value="Org/Employees",
            confidence=0.9,
            source="llm",
        ))
        db_session.add(LearnedClassification(
            entity_name="MYSTERY DONOR",
            entity_type="industry",
            value="TECH",
            confidence=0.9,
            source="llm",
        ))
        db_session.flush()

        donors = [{"name": "Mystery Donor", "amount": 2000}]
        result = await classify_donors_hybrid(donors, db_session=db_session)
        assert "MYSTERY DONOR" in result
        assert result["MYSTERY DONOR"]["type"] == "Org/Employees"
        assert result["MYSTERY DONOR"]["industry"] == "TECH"

    @pytest.mark.asyncio
    async def test_deduplication(self, db_session):
        donors = [
            {"name": "Test Corp", "amount": 1000, "fec_receipt": {"entity_type": "PAC"}},
            {"name": "TEST CORP", "amount": 2000, "fec_receipt": {"entity_type": "PAC"}},
            {"name": "test corp", "amount": 500, "fec_receipt": {"entity_type": "PAC"}},
        ]
        result = await classify_donors_hybrid(donors, db_session=db_session)
        assert len(result) == 1
        assert "TEST CORP" in result

    @pytest.mark.asyncio
    async def test_unknown_donors_classified_via_nn(self, db_session):
        """Donors with unknown type AND industry should be queued for kNN."""
        donors = [{"name": "Completely Unknown Entity XYZ", "amount": 500}]
        # Patch both upstream embedding tiers so the donor stays unclassifiable
        # and truly falls through to the NN step (embedding similarity scores
        # from newer sentence-transformers versions may classify it otherwise).
        with patch(
            "app.pipeline.analyze.donor_classifier_ai.classify_industries_batch_scored",
            return_value={},
        ), patch(
            "app.pipeline.analyze.donor_classifier_ai.classify_donor_type_semantic",
            return_value=None,
        ), patch(
            "app.pipeline.analyze.donor_classifier_ai._classify_remaining_via_nn",
            return_value={"COMPLETELY UNKNOWN ENTITY XYZ": {"type": "Org/Employees", "industry": "OTHER"}},
        ) as mock_nn:
            await classify_donors_hybrid(donors, db_session=db_session)
            mock_nn.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_unknown_and_empty_names(self, db_session):
        donors = [
            {"name": "UNKNOWN", "amount": 100},
            {"name": "", "amount": 200},
            {"name": "  ", "amount": 300},
        ]
        result = await classify_donors_hybrid(donors, db_session=db_session)
        assert result == {}

    @pytest.mark.asyncio
    async def test_does_not_block_the_event_loop(self):
        """classify_donors_hybrid must run its CPU-bound work off the event
        loop (asyncio.to_thread), not directly on it — a full Senate run's
        ~18k donors blocking the loop for minutes is what took down
        production on 2026-07-21: nothing else on the process, including
        the /api/health endpoint Docker's healthcheck polls, could respond,
        so Docker killed the "unhealthy" container mid-pipeline. This
        doesn't call the real classifier (too slow/model-dependent for a
        unit test) — it stands in a blocking time.sleep for the sync body
        and races it against a concurrently-running coroutine of known
        duration.

        Elapsed time, not tick count, is the discriminator here:
        asyncio.gather always waits for BOTH coroutines to finish either
        way, so a version of this test that only checked "did the ticker
        complete" would pass whether or not the sleep actually overlapped
        with it. If the sync body runs on the event loop's own thread
        (the bug), gather's total wall time is sleep_time + ticker_time
        (sequential — the ticker can't make progress until the blocking
        call releases the thread). If it runs in a separate thread (the
        fix), total wall time is ~max(sleep_time, ticker_time) (they
        overlap). Live-verified both ways while writing this fix: ~0.70s
        with the sync body inlined (the pre-fix shape), ~0.40s through
        asyncio.to_thread (the shipped fix, sleep_time=0.3s < ticker_time=0.4s
        so ticker dominates)."""
        import asyncio
        import time
        from app.pipeline.analyze import donor_classifier_ai

        def fake_sync_classify(donors, db_session, on_progress, candidate_name):
            time.sleep(0.3)
            return {}

        async def ticker():
            for _ in range(20):
                await asyncio.sleep(0.02)

        with patch.object(
            donor_classifier_ai, "_classify_donors_hybrid_sync", fake_sync_classify,
        ):
            start = time.monotonic()
            await asyncio.gather(
                classify_donors_hybrid([{"name": "X"}]),
                ticker(),
            )
            elapsed = time.monotonic() - start

        # Sequential (blocked loop) would be ~0.3 + 0.4 = 0.7s; concurrent
        # (fixed) is ~max(0.3, 0.4) = 0.4s. 0.55s cleanly separates them.
        assert elapsed < 0.55, (
            f"gather took {elapsed:.2f}s (expected ~0.4s if concurrent) — "
            "the sync classification body is blocking the event loop's own "
            "thread instead of running in a worker thread"
        )

    @pytest.mark.asyncio
    async def test_commits_periodically_by_elapsed_time_not_one_long_transaction(self, db_session):
        """SQLite allows exactly one writer at a time. Moving classification
        off the event loop (an earlier test in this file) made it genuinely
        run CONCURRENTLY with the rest of the app for the first time —
        which surfaced a second, separate bug: _store_donor_learning was
        called once or twice per donor with a single db_session.commit()
        only at the very end, so a full run held one open write transaction
        for its entire ~3 minutes. Every other write anywhere in the app
        during that window either waited out the busy_timeout or failed
        outright — live-observed 2026-07-21 as a real 500 on POST
        /api/track-visit while a donor classification run was in progress.

        This is deliberately a TIME bound (_COMMIT_INTERVAL_SECONDS), not a
        donor-count bound — a first attempt at this fix used a count (commit
        every 500 donors) reasoning that 500 plain DB writes take a few
        seconds. That didn't hold in production: most donors also need a
        real sentence-transformer encode() call (the semantic tier), which
        is far slower and more variable than a DB write, so contention hit
        again with ~100 donors still short of the 500-donor mark. This test
        reproduces that shape — patches classify_donor_type_from_fec to add
        a small per-call delay standing in for that variable cost — and
        asserts on WALL-CLOCK commit frequency, not donor count, so it
        would have caught the count-based version's failure.

        Patches classify_industries_batch_scored so industry resolves to a
        controlled non-OTHER value without a real model call, guaranteeing
        every donor reaches _store_donor_learning."""
        import time
        from app.pipeline.analyze import donor_classifier_ai

        donors = [
            {
                "name": f"Donor {i} Inc",
                "amount": 100,
                "fec_receipt": {"entity_type": "ORG"},
            }
            for i in range(10)
        ]

        commit_calls = 0
        real_commit = db_session.commit

        def counting_commit():
            nonlocal commit_calls
            commit_calls += 1
            real_commit()

        real_fec_classify = donor_classifier_ai.classify_donor_type_from_fec

        def slow_fec_classify(receipt):
            time.sleep(0.03)  # stand-in for a real per-donor encode() call
            return real_fec_classify(receipt)

        with patch.object(donor_classifier_ai, "_COMMIT_INTERVAL_SECONDS", 0.05), \
             patch.object(
                 donor_classifier_ai, "classify_industries_batch_scored",
                 return_value={d["name"]: ("TECH", 0.9) for d in donors},
             ), \
             patch.object(
                 donor_classifier_ai, "classify_donor_type_from_fec", slow_fec_classify,
             ), \
             patch.object(db_session, "commit", counting_commit):
            result = await classify_donors_hybrid(donors, db_session=db_session)

        assert len(result) == 10
        # 10 donors * 0.03s = ~0.3s total, against a 0.05s commit interval:
        # several intermediate commits plus the unconditional final one. The
        # exact count matters less than confirming more than one commit
        # happened at all — a count-based batch (e.g. every 500 donors)
        # would produce exactly 1 here, which is the failure this test
        # guards against.
        assert commit_calls > 1, (
            f"only {commit_calls} commit(s) for 10 slow donors — writes are "
            "accumulating in one long transaction again"
        )


class TestLearningStoreCorrectionThreshold:
    """_CORRECTION_THRESHOLD (O2) — fully mocked (classify_industries_
    batch_scored stubbed directly), so these don't need the real model
    and run in the fast suite unlike TestHybridClassification above."""

    def _seed_stale(self, db_session):
        db_session.add(LearnedClassification(
            entity_name="STALE CO", entity_type="donor_type",
            value="Org/Employees", confidence=0.9, source="llm",
        ))
        db_session.add(LearnedClassification(
            entity_name="STALE CO", entity_type="industry",
            value="TECH", confidence=0.9, source="llm",
        ))
        db_session.flush()

    @pytest.mark.asyncio
    async def test_requires_above_median_confidence(self, db_session):
        """A weak embedding disagreement (below the measured real floor
        for classify_industries_batch_scored's raw score, ~0.60) must not
        override a stored industry — it used to override on any
        disagreement at all."""
        self._seed_stale(db_session)
        donors = [{"name": "Stale Co", "amount": 1000}]
        with patch(
            "app.pipeline.analyze.donor_classifier_ai.classify_industries_batch_scored",
            return_value={"Stale Co": ("FINANCE", 0.50)},
        ):
            result = await classify_donors_hybrid(donors, db_session=db_session)
        assert result["STALE CO"]["industry"] == "TECH"

    @pytest.mark.asyncio
    async def test_fires_above_threshold(self, db_session):
        self._seed_stale(db_session)
        donors = [{"name": "Stale Co", "amount": 1000}]
        with patch(
            "app.pipeline.analyze.donor_classifier_ai.classify_industries_batch_scored",
            return_value={"Stale Co": ("FINANCE", 0.65)},
        ):
            result = await classify_donors_hybrid(donors, db_session=db_session)
        assert result["STALE CO"]["industry"] == "FINANCE"


class TestClassifyRemainingViaNn:
    """A kNN-resolved industry used to get a second, redundant write
    immediately after the correct one — store_llm_classifications(),
    despite its name, was fed the kNN result and mislabeled it
    source="llm" at confidence 0.7, downgrading the correct source="nn"/
    0.75 row _store_donor_learning had just written. No LLM is ever
    called in this pipeline (donor_classifier_ai.py imports no call_llm),
    so that second write was never anything but wrong provenance —
    removed entirely rather than fixed to say "nn" twice."""

    def test_a_knn_resolved_industry_keeps_its_nn_provenance(self, db_session):
        from app.pipeline.analyze.donor_classifier_ai import _classify_remaining_via_nn

        with patch(
            "app.pipeline.analyze.donor_classifier_ai.classify_batch_nn",
            side_effect=[
                {"Acme Corp": "MANUFACTURING"},  # industry pass
                {"Acme Corp": "Org/Employees"},  # donor_type pass
            ],
        ):
            _classify_remaining_via_nn([{"name": "Acme Corp", "amount": 5000}], db_session)

        stored = (
            db_session.query(LearnedClassification)
            .filter(
                LearnedClassification.entity_name == "ACME CORP",
                LearnedClassification.entity_type == "industry",
            )
            .one()
        )
        assert stored.source == "nn"
        assert stored.confidence == 0.75


class TestOtherPlaceholderDoesNotBlockKnn:
    """2026-08 audit: a donor with a known donor_type (e.g. FEC tier) but
    an unresolved industry writes donor_type immediately and queues the
    donor for kNN to resolve industry in the same run. That write used to
    also persist a placeholder industry="OTHER" at the donor_type tier's
    confidence (e.g. "fec" -> 1.0), which set _seen_this_run's industry
    entry high enough to make _store_donor_learning's in-run confidence
    guard silently skip kNN's real answer (source="nn", confidence 0.75)
    a few lines later — stranding the industry at "OTHER" forever. The
    same merge step also let kNN's own, independently re-guessed
    donor_type silently replace the already-correct FEC-derived one in
    the function's *returned* dict (the DB stayed correct only because
    the placeholder write happened to protect donor_type too)."""

    @pytest.mark.asyncio
    async def test_knn_resolved_industry_persists_past_the_other_placeholder(self, db_session):
        donors = [{
            "name": "Acme Corp",
            "amount": 5000,
            "fec_receipt": {"entity_type": "PAC"},
        }]
        with patch(
            "app.pipeline.analyze.donor_classifier_ai.classify_industries_batch_scored",
            return_value={},  # industry unresolved by the embedding tier -> "OTHER" -> needs_nn
        ), patch(
            "app.pipeline.analyze.donor_classifier_ai.classify_batch_nn",
            side_effect=[
                {"Acme Corp": "MANUFACTURING"},  # industry pass
                {"Acme Corp": "Org/Employees"},  # donor_type pass
            ],
        ):
            result = await classify_donors_hybrid(donors, db_session=db_session)

        assert result["ACME CORP"]["industry"] == "MANUFACTURING"

        stored = (
            db_session.query(LearnedClassification)
            .filter(
                LearnedClassification.entity_name == "ACME CORP",
                LearnedClassification.entity_type == "industry",
            )
            .one()
        )
        assert stored.value == "MANUFACTURING"
        assert stored.source == "nn"

    @pytest.mark.asyncio
    async def test_knn_industry_pass_does_not_downgrade_the_known_donor_type(self, db_session):
        # entity_type "ORG" (not "PAC") deliberately avoids
        # cross_validate_donor_types, which independently reclassifies
        # PAC-labeled *company* names to Org/Employees via its own
        # embedding check — a real, separate mechanism that would
        # otherwise produce the same-looking result and mask this test's
        # actual target (the raw `{**existing, **classification}` merge).
        donors = [{
            "name": "Acme Corp",
            "amount": 5000,
            "fec_receipt": {"entity_type": "ORG"},
        }]
        with patch(
            "app.pipeline.analyze.donor_classifier_ai.classify_industries_batch_scored",
            return_value={},
        ), patch(
            "app.pipeline.analyze.donor_classifier_ai.classify_batch_nn",
            side_effect=[
                {"Acme Corp": "MANUFACTURING"},
                # kNN's own re-guess deliberately disagrees with the real,
                # already-established FEC donor_type ("Org/Employees").
                {"Acme Corp": "PAC"},
            ],
        ):
            result = await classify_donors_hybrid(donors, db_session=db_session)

        assert result["ACME CORP"]["type"] == "Org/Employees"

        stored = (
            db_session.query(LearnedClassification)
            .filter(
                LearnedClassification.entity_name == "ACME CORP",
                LearnedClassification.entity_type == "donor_type",
            )
            .one()
        )
        assert stored.value == "Org/Employees"
        assert stored.source == "fec"


@pytest.mark.slow
class TestSkipNamesBatch:
    """No prior coverage existed for these batch skip-detection functions
    (2026-08 cleanup: their batch-encode-then-normalize internals were
    consolidated into vector_store.encode_normalized) — these prove basic
    behavior still holds. Testing the two real entry points at their own
    measured 0.78 threshold, not the shared classify_skip_names_batch
    helper's own default (0.50 — verified too loose to be safe on its
    own; see that function's docstring) directly."""

    def test_employer_skip_detects_non_organizations(self):
        result = classify_employer_skips_batch(["RETIRED", "SELF-EMPLOYED", "Acme Manufacturing Corp"])
        assert "RETIRED" in result
        assert "SELF-EMPLOYED" in result
        assert "ACME MANUFACTURING CORP" not in result

    def test_transfer_memo_detects_fund_transfers(self):
        result = classify_transfer_memos_batch([
            "Earmarked contribution transfer", "Lunch reimbursement for staff",
        ])
        assert "EARMARKED CONTRIBUTION TRANSFER" in result
        assert "LUNCH REIMBURSEMENT FOR STAFF" not in result

    def test_empty_input_returns_empty_set(self):
        assert classify_skip_names_batch([]) == set()
        assert classify_employer_skips_batch([]) == set()
        assert classify_transfer_memos_batch([]) == set()
