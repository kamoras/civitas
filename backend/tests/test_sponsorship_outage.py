"""An outage in the sponsorship read is never a record of no bills.

On 2026-10-14 one bill's actions failed three times; the House pipeline's
whole sponsorship step stopped there, 296 of 433 members never had their
bills read, and each was scored a neutral 50 on the bill part while the
run's Legislative Effectiveness reference was measured with them as members
holding no bills (mean credit 3.16 on a scale defined as 1.0). The profile
breakdown, recomputing from the stored bills against that reference, then
disagreed with 285 stored scores.

Covered here: a member whose bills weren't read is scored on the bills an
earlier run stored (with their date); one with none stored has the bill part
unmeasured; the stored score and the breakdown agree in every case; a bill
whose actions can't be read degrades only itself; the run's alert names
what went unread; drift on an unchanged method alerts. The reference
exclusion itself is in test_les_reference.py.
"""

import asyncio
import json
from unittest.mock import patch

from app.models import Representative, RepSponsoredBill
from app.pipeline import run_checks, senate_pipeline
from app.pipeline.analyze import score_calibration
from app.pipeline.analyze.score_calculator import (
    LES_BILLS_STATUS,
    calculate_confidence,
    calculate_scores,
    load_les_reference,
)
from app.pipeline.fetch.congress import CongressUnavailable
from app.pipeline.live_references import bills_read_this_run
from app.pipeline.sponsorship_backfill import carry_forward_sponsored_bills
from app.services.representative_service import get_representative_score_breakdown, upsert_representative


def _bill(n, stage="REFERRED", law=False):
    return {
        "billId": f"HR.{n}", "title": f"Act {n}", "introducedDate": "2025-03-01",
        "latestAction": "Referred to committee", "latestActionDate": "2025-03-01",
        "policyArea": "HEALTH", "policyAreas": [], "partyLeaning": None,
        "congress": 119, "billType": "hr", "isLaw": law, "stage": stage, "commemorative": False,
    }


def _rep(rid, bio, **kw):
    return {
        "id": rid, "bioguideId": bio, "name": f"Member {rid}", "state": "TX", "district": 2,
        "party": "R", "yearsInOffice": 5, "leadershipScore": 0.6, "attractedBipartisanshipScore": 0.3,
        "funding": {}, "votingRecord": {"keyVotes": [], "recentVotes": []}, **kw,
    }


def _seed_stored(db, rid, bio, bills, status):
    db.add(Representative(
        id=rid, bioguide_id=bio, name=f"Member {rid}", state="TX", district=2, party="R",
        years_in_office=5, score_confidence=json.dumps({LES_BILLS_STATUS: status} if status else {}),
    ))
    for b in bills:
        db.add(RepSponsoredBill(
            representative_id=rid, bill_id=b["billId"], title=b["title"], congress=119,
            bill_type=b["billType"], stage=b["stage"], is_law=b["isLaw"], latest_action=b["latestAction"],
        ))
    db.commit()


class TestCarryForward:
    def test_unread_members_keep_their_stored_bills_and_their_date(self, db_session):
        _seed_stored(db_session, "r-c", "C1", [_bill(1), _bill(2, "PASSED_CHAMBER")], "fetched:2026-10-13")
        _seed_stored(db_session, "r-u", "U1", [_bill(3)], None)
        fetched = _rep("r-f", "F1", sponsoredBills=[_bill(9)])
        failed = _rep("r-c", "C1", sponsoredBills=[], sponsoredBillsUnavailable=True)
        unreached = _rep("r-u", "U1")  # the read stopped before this member
        new = _rep("r-n", "N1", sponsoredBillsUnavailable=True)

        carried = carry_forward_sponsored_bills(db_session, Representative, [fetched, failed, unreached, new])

        assert sorted(carried) == ["C1", "U1"]
        assert [b["billId"] for b in failed["sponsoredBills"]] == ["HR.1", "HR.2"]
        assert failed["sponsoredBillsCarriedFrom"] == "2026-10-13" and not failed["sponsoredBillsUnavailable"]
        assert unreached["sponsoredBillsCarriedFrom"] == ""  # no run recorded a date
        assert new["sponsoredBillsUnavailable"] and new["sponsoredBills"] == []
        assert "sponsoredBillsCarriedFrom" not in fetched
        # Only bills this run read are measured into the reference.
        assert [bills_read_this_run(m) for m in (fetched, failed, unreached, new)] == [True, False, False, False]

    def test_a_carried_date_is_kept_across_nights(self, db_session):
        _seed_stored(db_session, "r-c", "C1", [_bill(1)], "carried:2026-10-10")
        member = _rep("r-c", "C1", sponsoredBillsUnavailable=True)
        carry_forward_sponsored_bills(db_session, Representative, [member])
        assert member["sponsoredBillsCarriedFrom"] == "2026-10-10"
        assert calculate_confidence(member)[LES_BILLS_STATUS] == "carried:2026-10-10"


class TestBreakdownAgreesWithStoredScore:
    def test_fetched_carried_and_unmeasured_members(self, db_session, pinned_les_reference):
        """The pipeline's sequence (carry forward, score, store) and the
        breakdown recomputed from what was stored give the same score in
        every dimension, for each way a member's bills can arrive."""
        _seed_stored(db_session, "r-c", "C1", [_bill(1, "ENACTED", True), _bill(2)], "fetched:2026-10-13")
        members = [
            _rep("r-f", "F1", sponsoredBills=[_bill(5, "PASSED_CHAMBER"), _bill(6)]),
            _rep("r-c", "C1", sponsoredBills=[], sponsoredBillsUnavailable=True),
            _rep("r-n", "N1"),  # never stored, never read
        ]
        carry_forward_sponsored_bills(db_session, Representative, members)
        reference = load_les_reference()
        for rep in members:
            scores = calculate_scores({**rep, "lesReference": reference})
            scores["confidence"] = calculate_confidence(rep)
            upsert_representative(db_session, {**rep, "representationScore": scores})

        for rep in members:
            stored = db_session.get(Representative, rep["id"])
            breakdown = get_representative_score_breakdown(db_session, rep["id"])
            assert breakdown["legislativeEffectiveness"]["score"] == stored.score_legislative_effectiveness
            assert breakdown["fundingIndependence"]["score"] == stored.score_funding_independence
            assert breakdown["constituentAlignment"]["score"] == stored.score_constituent_alignment

        unmeasured = get_representative_score_breakdown(db_session, "r-n")["legislativeEffectiveness"]
        assert unmeasured["components"][0]["score"] is None
        carried = get_representative_score_breakdown(db_session, "r-c")["legislativeEffectiveness"]
        assert "stored on 2026-10-13" in carried["components"][0]["detail"]
        # The carried bills were written back, not dropped.
        assert db_session.query(RepSponsoredBill).filter_by(representative_id="r-c").count() == 2


class TestOneBillDegradesOnlyItself:
    def test_senate_stage_read_names_the_unread_bill_and_classifies_the_rest(self, db_session):
        bills = [
            {"billId": "S.1", "billType": "s", "congress": 119, "latestAction": "Read twice"},
            {"billId": "S.2", "billType": "s", "congress": 119, "latestAction": "Read twice"},
        ]

        async def actions(client, db, sp):
            if sp["billId"] == "S.1":
                raise CongressUnavailable("actions of S.1 (119)")
            return [{"actionCode": "10000", "type": "IntroReferral", "text": "Introduced"}]

        with patch.object(senate_pipeline, "_sponsored_bill_actions", actions):
            unread = asyncio.run(senate_pipeline._classify_sponsored_stages(
                db_session, [{"sponsoredBills": bills}],
            ))
        assert unread == ["S.1 (119)"]
        assert "stage" not in bills[0]  # falls back to its latest action (_les_bill_stage)
        assert bills[1]["stage"]


class TestUnreadSponsorshipAlert:
    def test_names_each_gap_and_closes_when_clean(self):
        with patch.object(run_checks, "send_ops_alert") as alert, \
                patch.object(run_checks, "resolve_ops_alert") as resolve:
            run_checks.alert_unread_sponsorship("house", ["HR.1228 (119)"], ["C1"], ["N1"])
            body = alert.call_args.args[1]
            assert "HR.1228 (119)" in body and "C1" in body and "N1" in body
            assert alert.call_args.kwargs["condition"] == "sponsorship-unread-house"
            run_checks.alert_unread_sponsorship("house", [], [], [])
            resolve.assert_called_once_with("sponsorship-unread-house")
            run_checks.alert_unread_sponsorship("senate", [], [], [], close_when_clean=False)
            assert resolve.call_count == 1


def _dist(stdev_scale, version="v6.34", congress=119, n=40):
    scores = {f"m{i}": 50 + (i - n / 2) * stdev_scale for i in range(n)}
    values = list(scores.values())
    stats = score_calibration._dim_stats(values)
    return {
        "date": "x", "_versions": {version}, "_congress": congress,
        "legislative_effectiveness": {**stats, "_entity_scores": scores},
    }


class TestSameMethodDrift:
    def test_a_third_off_the_spread_on_one_method_is_an_incident(self):
        events = score_calibration.detect_drift(_dist(1.0), _dist(0.67))
        les = [e for e in events if e["dimension"] == "legislative_effectiveness"][0]
        assert les["severity"] == "alert" and les["same_method"]

    def test_a_method_change_or_new_congress_is_not(self):
        for curr in (_dist(0.67, version="v6.35"), _dist(0.67, congress=120)):
            events = score_calibration.detect_drift(_dist(1.0), curr)
            assert not any(e["same_method"] for e in events)

    def test_ordinary_nights_stay_quiet(self):
        assert score_calibration.detect_drift(_dist(1.0), _dist(0.97)) == []

    def test_an_incident_raises_an_ops_alert(self):
        report = {
            "from_date": "2026-10-13", "to_date": "2026-10-14",
            "drift_events": score_calibration.detect_drift(_dist(1.0), _dist(0.67)),
        }
        with patch("app.pipeline.analyze.score_calibration.generate_calibration_report", return_value=report), \
                patch.object(run_checks, "send_ops_alert") as alert:
            run_checks.run_calibration_check("representative")
        assert alert.call_args.kwargs["condition"] == "score-drift-representative"
        assert "legislative_effectiveness" in alert.call_args.args[1]
