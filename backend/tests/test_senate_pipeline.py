"""Tests for senate_pipeline helper functions."""

from app.config import settings
from app.models import Senator
from app.pipeline.sponsorship_backfill import backfill_withheld_sponsorship_scores
from app.pipeline.senate_pipeline import (
    PIPELINE_STEPS,
    _build_current_term_sponsored_for_cosponsor,
    _build_donor_entries,
)


def test_fetch_sponsored_cosponsors_is_a_registered_step():
    """ProgressTracker.begin/update/complete silently no-op for any step
    key not in the steps list passed to its constructor (see
    progress_tracker.py — `step = self._steps.get(key); if not step:
    return`). fetch_sponsored_cosponsors calls all three, correctly, but
    was missing from PIPELINE_STEPS — so every one of those calls was a
    silent no-op, and a ~6,000-bill sequential HTTP fetch loop (uncapped
    2026-07) ran fully invisible to the pipeline status API. Live-observed
    2026-07-21: looked indistinguishable from a hang for 25+ minutes
    between prepare_senators and sponsorship_analysis. The exact same
    failure mode already happened once for fetch_official_titles (see its
    own code comment: "in run 69 this loop ran for 80 minutes... with
    nothing in progress_detail to show for it") and was fixed there by
    registering the step — this is the same fix applied to the step that
    was still missing it."""
    step_keys = [key for key, _phase, _label in PIPELINE_STEPS]
    assert "fetch_sponsored_cosponsors" in step_keys
    # Must be registered before sponsorship_analysis begins, matching
    # where it actually runs in the pipeline.
    assert step_keys.index("fetch_sponsored_cosponsors") < step_keys.index("sponsorship_analysis")


class TestBuildDonorEntries:
    """_build_donor_entries flattens FEC receipts for classify_donors_hybrid.

    Every entry needs `fec_receipt` attached so Tier 1 (FEC entity_type) can
    resolve it — otherwise a bare company name like "Airbnb" falls through
    to the semantic embedding tier, which has no reliable signal for it
    (see test_donor_classifier.py's IND -> Org/Employees mapping).
    """

    def test_employer_receipts_carry_fec_receipt(self):
        senators = [{"id": "sen-1"}]
        fec_data = {
            "sen-1": {
                "receipts": [
                    {
                        "contributor_employer": "Airbnb",
                        "contribution_receipt_amount": 10000,
                        "entity_type": "IND",
                    }
                ],
            }
        }
        entries = _build_donor_entries(senators, fec_data)
        assert len(entries) == 1
        assert entries[0]["name"] == "Airbnb"
        assert entries[0]["fec_receipt"]["entity_type"] == "IND"

    def test_pac_receipts_carry_fec_receipt(self):
        senators = [{"id": "sen-1"}]
        fec_data = {
            "sen-1": {
                "pacReceipts": [
                    {
                        "contributor_name": "Test PAC",
                        "contribution_receipt_amount": 5000,
                        "entity_type": "PAC",
                    }
                ],
            }
        }
        entries = _build_donor_entries(senators, fec_data)
        assert len(entries) == 1
        assert entries[0]["fec_receipt"]["entity_type"] == "PAC"

    def test_skips_senators_with_no_fec_data(self):
        senators = [{"id": "sen-1"}, {"id": "sen-2"}]
        fec_data = {"sen-1": {"receipts": [{"contributor_employer": "Acme"}]}}
        entries = _build_donor_entries(senators, fec_data)
        assert len(entries) == 1

    def test_skips_receipts_with_no_employer(self):
        senators = [{"id": "sen-1"}]
        fec_data = {"sen-1": {"receipts": [{"contributor_employer": ""}]}}
        assert _build_donor_entries(senators, fec_data) == []

    def test_aggregated_entries_have_no_fec_receipt(self):
        """Aggregated (by_contributor) rows don't carry a raw receipt — this
        is unchanged pre-existing behavior, not part of the employer-receipt
        fix."""
        senators = [{"id": "sen-1"}]
        fec_data = {
            "sen-1": {
                "aggregated": [{"contributor_name": "Some Donor", "total": 2500}],
            }
        }
        entries = _build_donor_entries(senators, fec_data)
        assert len(entries) == 1
        assert "fec_receipt" not in entries[0]


class TestBuildCurrentTermSponsoredForCosponsor:
    """No per-senator cap — a prior 10-bill cap meant a prolific sponsor's
    ideology/leadership score was computed from under 15% of their actual
    sponsored bills (2026-07 audit, prompted by a real senator with 49
    sponsored bills in the current congress landing near the ideological
    center despite a real-world reputation, corroborated by GovTrack, as
    one of the most ideologically extreme senators)."""

    def _prep(self, bio_id: str, bills: list[dict], party: str = "D") -> dict:
        return {
            "senator": {"bioguideId": bio_id, "party": party},
            "sponsoredBills": bills,
        }

    def test_no_cap_on_bill_count(self):
        many_bills = [
            {"congress": settings.CURRENT_CONGRESS, "billId": f"S.{i}"}
            for i in range(49)
        ]
        prepared = [self._prep("S001", many_bills)]
        entries = _build_current_term_sponsored_for_cosponsor(prepared)
        assert len(entries) == 49

    def test_excludes_bills_from_other_congresses(self):
        bills = [
            {"congress": settings.CURRENT_CONGRESS - 1, "billId": "S.1"},
            {"congress": settings.CURRENT_CONGRESS, "billId": "S.2"},
            {"congress": settings.CURRENT_CONGRESS + 1, "billId": "S.3"},
        ]
        prepared = [self._prep("S001", bills)]
        entries = _build_current_term_sponsored_for_cosponsor(prepared)
        assert len(entries) == 1
        assert entries[0]["billId"] == "S.2"

    def test_skips_senators_with_no_bioguide_id(self):
        prepared = [{
            "senator": {"bioguideId": "", "party": "D"},
            "sponsoredBills": [{"congress": settings.CURRENT_CONGRESS, "billId": "S.1"}],
        }]
        assert _build_current_term_sponsored_for_cosponsor(prepared) == []

    def test_skips_bills_with_no_bill_id(self):
        bills = [{"congress": settings.CURRENT_CONGRESS}]
        prepared = [self._prep("S001", bills)]
        assert _build_current_term_sponsored_for_cosponsor(prepared) == []

    def test_entry_shape(self):
        bills = [{
            "congress": settings.CURRENT_CONGRESS,
            "billId": "S.42",
            "isLaw": True,
            "latestAction": "Signed by President",
        }]
        prepared = [self._prep("S001", bills, party="R")]
        entries = _build_current_term_sponsored_for_cosponsor(prepared)
        assert entries == [{
            "billId": "S.42",
            "congress": settings.CURRENT_CONGRESS,
            "sponsorBioguide": "S001",
            "sponsorParty": "R",
            "isLaw": True,
            "latestAction": "Signed by President",
        }]


class TestBackfillWithheldSponsorshipScores:
    """2026-07-25 regression: compute_ideology_scores (and its sibling
    leadership/bipartisanship functions) withhold as a whole-cohort gate
    (return {}), not per-senator — the run right after O6 shipped saw 59 of
    101 senators collapse onto an identical Independent Voting score
    because every downstream `.get(bio_id)` silently read None for
    everyone at once. This backfill must restore each senator's own last
    value, not some shared default."""

    def _make_senator(self, db_session, bio_id, **scores):
        s = Senator(
            id=bio_id, bioguide_id=bio_id, name=bio_id, state="GA", party="D",
            **scores,
        )
        db_session.add(s)
        db_session.commit()
        return s

    def test_fills_gap_from_last_stored_value_per_senator(self, db_session):
        self._make_senator(db_session, "S001", ideology_score=0.71, leadership_score=0.4)
        self._make_senator(db_session, "S002", ideology_score=0.22, leadership_score=0.6)

        # Whole-cohort withhold: ideology_scores comes back empty, as
        # compute_ideology_scores does when the SVD axis fails its
        # partisan-separation check.
        leadership_scores = {"S001": 0.4, "S002": 0.6}
        ideology_scores: dict = {}
        bipartisanship_scores = {"S001": 0.5, "S002": 0.5}
        attracted_bipartisanship_scores = {"S001": 0.5, "S002": 0.5}

        backfill_withheld_sponsorship_scores(
            db_session, Senator, {"S001", "S002"},
            leadership_scores, ideology_scores,
            bipartisanship_scores, attracted_bipartisanship_scores,
        )

        # Each senator gets their OWN prior value back, not a shared one.
        assert ideology_scores == {"S001": 0.71, "S002": 0.22}

    def test_no_prior_value_leaves_gap_unfilled(self, db_session):
        # A brand-new senator with no stored history and a withheld run:
        # nothing to backfill from, correctly stays missing.
        self._make_senator(db_session, "S001", ideology_score=None)

        ideology_scores: dict = {}
        backfill_withheld_sponsorship_scores(
            db_session, Senator, {"S001"},
            {"S001": 0.4}, ideology_scores, {"S001": 0.5}, {"S001": 0.5},
        )
        assert "S001" not in ideology_scores

    def test_no_missing_keys_is_a_no_op(self, db_session):
        # Every dict already has every bio_id — no DB query should even
        # matter here; values pass through untouched.
        scores = {"S001": 0.4}
        backfill_withheld_sponsorship_scores(
            db_session, Senator, {"S001"}, dict(scores), dict(scores), dict(scores), dict(scores),
        )


def test_upsert_senator_persists_confidence(db_session):
    # Senators' confidence grades (and the Constituent Alignment vote-part
    # status) were never written before v6.15; only the House saved them.
    import json

    from app.pipeline.senate_pipeline import upsert_senator

    confidence = {"constituentAlignment": "medium", "constituentAlignmentVotePart": "shrunk:0.60"}
    upsert_senator(db_session, {
        "id": "s-test", "name": "Senator Test", "state": "SW", "party": "D",
        "representationScore": {"constituentAlignment": 55, "confidence": confidence},
    })
    db_session.commit()
    stored = db_session.query(Senator).filter(Senator.id == "s-test").one()
    assert json.loads(stored.score_confidence) == confidence


def test_upsert_senator_persists_leadership_title_and_committees(db_session):
    # Never written for senators before 2026-09 (only the House path saved
    # them), so the API served every senator leadershipTitle null.
    import json

    from app.pipeline.senate_pipeline import upsert_senator

    committees = [{"committeeName": "Senate Committee on Finance", "chamber": "senate", "title": None}]
    base = {"id": "s-thune", "name": "John Thune", "state": "SD", "party": "R"}
    upsert_senator(db_session, {
        **base, "leadershipTitle": "Senate Majority Leader", "committees": committees,
    })
    db_session.commit()
    stored = db_session.query(Senator).filter(Senator.id == "s-thune").one()
    assert stored.leadership_title == "Senate Majority Leader"
    assert json.loads(stored.committees) == committees

    # Updating an existing row writes them too, including clearing a title
    # the member no longer holds.
    upsert_senator(db_session, {**base, "leadershipTitle": None, "committees": []})
    db_session.commit()
    db_session.refresh(stored)
    assert stored.leadership_title is None
    assert json.loads(stored.committees) == []

    # A record that doesn't carry the keys leaves the stored values alone.
    upsert_senator(db_session, {
        **base, "leadershipTitle": "Senate Majority Leader", "committees": committees,
    })
    upsert_senator(db_session, dict(base))
    db_session.commit()
    db_session.refresh(stored)
    assert stored.leadership_title == "Senate Majority Leader"
    assert json.loads(stored.committees) == committees


def test_house_representatives_are_backfilled_too(db_session):
    """The House pipeline used to score every member without their stored
    sponsorship values on a withheld run; the shared helper takes either
    chamber's model."""
    from app.models import Representative

    db_session.add(Representative(
        id="R-X1", bioguide_id="R001", name="Rep X", state="CT", district=1, party="D",
        leadership_score=0.3, ideology_score=-0.4, bipartisanship_score=0.6,
        attracted_bipartisanship_score=0.55,
    ))
    db_session.commit()
    leadership, ideology, bip, attracted = {}, {}, {}, {}
    backfill_withheld_sponsorship_scores(db_session, Representative, {"R001"}, leadership, ideology, bip, attracted)
    assert (leadership, ideology, bip, attracted) == (
        {"R001": 0.3}, {"R001": -0.4}, {"R001": 0.6}, {"R001": 0.55},
    )
