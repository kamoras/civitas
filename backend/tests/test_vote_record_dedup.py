"""Each roll call counts once in a member's voting record.

Regression for a Senate double-count: normalize_votes was handed the recent
roll calls (for its aggregate counts) AND normalize_recent_votes produced
them again, so every recent roll call reached keyVotes + recentVotes twice
until the key/recent split — which deduplicated by billId against the
non-key leftovers only. A recent roll call picked as a key vote therefore
stayed in both lists. select_key_votes favours party breaks (+3), so the
duplicated votes were mostly breaks, inflating Constituent Alignment
(measured: 20 roll calls / 2 breaks scored as 25 / 4, CA 54 -> 66 for a
swing-state member). A key bill whose floor vote was also a recent roll call
was counted through both paths in both chambers.

These tests replay the pipeline's own call sequence (senate_pipeline.py's
per-senator vote block) through the real helpers.
"""

from app.pipeline.analyze.cross_reference import select_key_votes
from app.pipeline.analyze.score_calculator import _constituent_alignment_core
from app.pipeline.senate_pipeline import (
    _key_bill_votes_only,
    _recent_not_covered_by_key_bills,
    split_key_and_recent_votes,
)
from app.pipeline.transform.normalize_votes import (
    dedupe_votes,
    extract_senator_vote,
    house_roll_call_id,
    normalize_recent_votes,
    normalize_votes,
    vote_identity,
)


def _rc(congress, session, roll, vote):
    return {
        "congress": congress, "session": session, "rollNumber": roll,
        "members": [{"lastName": "Smith", "state": "OH", "voteCast": vote}],
    }


def _senator_record(classified_bills, roll_call_data_map, classified_recent, recent_rc_map):
    """Mirror of senate_pipeline.run_senate_pipeline's per-senator block."""
    recent_only = _recent_not_covered_by_key_bills(classified_recent, roll_call_data_map)
    senator_votes = {}
    for bill in classified_bills:
        rcd = roll_call_data_map.get(bill["billId"])
        if rcd:
            v = extract_senator_vote(rcd, "", "Smith", "OH")
            if v:
                senator_votes[bill["billId"]] = v
    for rc in recent_only:
        rc_id = rc.get("rcKey") or rc.get("billId", "")
        rcd = recent_rc_map.get(rc_id)
        if rcd:
            v = extract_senator_vote(rcd, "", "Smith", "OH")
            if v:
                senator_votes[rc_id] = v
    record = normalize_votes("X", classified_bills + recent_only, senator_votes, senator_party="R")
    record["recentVotes"] = normalize_recent_votes(
        recent_only, recent_rc_map, "Smith", "OH", "R", effective_party="R",
    )
    record["keyVotes"] = _key_bill_votes_only(record["keyVotes"], roll_call_data_map)

    all_votes = dedupe_votes(record["keyVotes"] + record["recentVotes"])
    key_ids = set(select_key_votes(all_votes, []))
    record["keyVotes"], record["recentVotes"] = split_key_and_recent_votes(
        record["keyVotes"], record["recentVotes"], key_ids,
    )
    return record


def _recent_fixture(n=20, breaks=2, bill_id=lambda i: f"S.{i}"):
    classified, rc_map = [], {}
    for i in range(n):
        key = f"119-1-{i}"
        classified.append({
            "billId": bill_id(i), "rcKey": key, "policyArea": "HEALTH",
            "partyLeaning": "R", "partyAlignmentWeight": 1.0,
        })
        rc_map[key] = _rc(119, 1, i, "Nay" if i < breaks else "Yea")
    return classified, rc_map


def _all(record):
    return record["keyVotes"] + record["recentVotes"]


class TestRecentRollCallsCountOnce:
    def test_every_roll_call_appears_exactly_once(self):
        classified, rc_map = _recent_fixture()
        record = _senator_record([], {}, classified, rc_map)
        votes = _all(record)
        assert len(votes) == 20
        assert len({vote_identity(v) for v in votes}) == 20
        assert sum(1 for v in votes if v["votedWithParty"] is False) == 2

    def test_selected_party_breaks_are_not_duplicated(self):
        classified, rc_map = _recent_fixture()
        record = _senator_record([], {}, classified, rc_map)
        key_idents = {vote_identity(v) for v in record["keyVotes"]}
        recent_idents = {vote_identity(v) for v in record["recentVotes"]}
        assert key_idents and not key_idents & recent_idents
        # both breaks are selected as key votes (the +3 bonus) — once each
        assert sum(1 for v in record["keyVotes"] if v["votedWithParty"] is False) == 2

    def test_constituent_alignment_matches_the_true_record(self):
        classified, rc_map = _recent_fixture()
        record = _senator_record([], {}, classified, rc_map)
        truth = {
            "keyVotes": dedupe_votes(_all(record)), "recentVotes": [], "effectiveParty": "R",
        }
        scored = _constituent_alignment_core(record, [], {}, "PA", "R")["score"]
        assert scored == _constituent_alignment_core(truth, [], {}, "PA", "R")["score"]

    def test_votes_sharing_a_document_stay_distinct(self):
        # Cloture and passage on the same document: one billId, two roll
        # calls. Deduping must not collapse them.
        classified, rc_map = _recent_fixture(n=4, breaks=1, bill_id=lambda i: "S.1")
        record = _senator_record([], {}, classified, rc_map)
        assert len(_all(record)) == 4

    def test_aggregate_loyalty_counts_each_roll_call_once(self):
        classified, rc_map = _recent_fixture()
        record = _senator_record([], {}, classified, rc_map)
        assert record["votedAgainstPartyCount"] == 2
        assert record["votedWithPartyCount"] == 18


class TestKeyBillAlsoARecentRollCall:
    def test_overlapping_roll_call_is_counted_once_as_the_key_bill(self):
        classified_recent, recent_rc_map = _recent_fixture(n=5, breaks=1)
        # Key bill HR.9's floor vote is recent roll call 119-1-0 (a break).
        key_bill = {
            "billId": "HR.9", "billName": "A real bill", "policyArea": "HEALTH",
            "partyLeaning": "R", "partyAlignmentWeight": 1.0,
        }
        roll_call_data_map = {"HR.9": recent_rc_map["119-1-0"]}

        record = _senator_record([key_bill], roll_call_data_map, classified_recent, recent_rc_map)
        votes = _all(record)

        assert len(votes) == 5
        assert sum(1 for v in votes if v["votedWithParty"] is False) == 1
        overlap = [v for v in votes if vote_identity(v) == "119-1-0"]
        assert len(overlap) == 1 and overlap[0]["billId"] == "HR.9"

    def test_recent_filter_drops_only_covered_roll_calls(self):
        classified_recent, recent_rc_map = _recent_fixture(n=3)
        kept = _recent_not_covered_by_key_bills(
            classified_recent, {"HR.9": recent_rc_map["119-1-1"]},
        )
        assert [rc["rcKey"] for rc in kept] == ["119-1-0", "119-1-2"]


class TestSplitFallback:
    def test_no_selection_falls_back_to_first_key_bill_votes(self):
        key_bills = [{"billId": f"HR.{i}", "rcKey": None} for i in range(7)]
        recent = [{"billId": "S.1", "rcKey": "119-1-1"}]
        key, rest = split_key_and_recent_votes(key_bills, recent, set())
        assert [v["billId"] for v in key] == [f"HR.{i}" for i in range(5)]
        assert [v["billId"] for v in rest] == ["HR.5", "HR.6", "S.1"]
        assert all(v["voteCategory"] == "key" for v in key)
        assert all(v["voteCategory"] == "recent" for v in rest)


class TestHouseRollCallId:
    def test_matches_the_recent_vote_billid_format(self):
        assert house_roll_call_id({"year": 2026, "rollNumber": 45}) == "HouseRC-2026-45"


class TestHouseKeyBillAlsoARecentRollCall:
    def test_recent_duplicate_of_a_key_bill_roll_call_is_dropped(self):
        from app.pipeline.house_pipeline import recent_not_covered_by_key_bills

        house_roll_calls = {"HR.9": {"year": 2026, "rollNumber": 45}}
        classified_recent = [
            {"billId": "HouseRC-2026-45"},
            {"billId": "HouseRC-2026-46"},
        ]
        kept = recent_not_covered_by_key_bills(classified_recent, house_roll_calls)
        assert [b["billId"] for b in kept] == ["HouseRC-2026-46"]
