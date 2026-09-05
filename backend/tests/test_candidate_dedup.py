"""Tests for app/candidate_dedup.py.

test_elections_state_ballot.py already covers dedupe_candidates end to
end (via the state_ballot endpoint) with real 2026 fixture data — those
tests stay there since they're really testing _confirmed_or_all's
integration of it. This file covers what only makes sense to test at the
module level: dedupe_merge_map's actual id->id DIRECTION (not just "one
row disappeared"), and resolve_candidate_id, the entry point
election_bluesky.py's _roster_fact depends on to avoid posting about a
dropped id.
"""

from app.candidate_dedup import dedupe_merge_map, normalized_surname, resolve_candidate_id
from app.models import Candidate


def _cand(cand_id, name, **overrides):
    defaults = dict(race_id="2026-HOUSE-OH-4", party="DEM")
    defaults.update(overrides)
    return Candidate(id=cand_id, name=name, **defaults)


class TestDedupeMergeMap:
    def test_maps_the_dropped_id_to_the_surviving_id(self):
        # Real Ohio House District 4 data: "WILSON, TAMARA" filed under
        # two FEC ids with byte-identical financials (test_elections_
        # state_ballot.py's test_two_fec_ids_for_the_same_person_collapse_
        # to_one uses the same real fixture).
        older = _cand("H2OH04164", "WILSON, TAMARA", party="IND", contributions=22049.51, cash_on_hand=520819.93)
        newer = _cand("H6OH04173", "WILSON, TAMARA", party="DEM", contributions=22049.51, cash_on_hand=520819.93)

        merge_map = dedupe_merge_map([older, newer])

        # Neither id is confirmed/on_primary_ballot here, so the tie-break
        # falls to the lowest id -- assert the SURVIVOR's identity
        # directly (not just "one entry"), since a mapping pointing the
        # wrong direction (surviving id -> dropped id) would silently
        # break any resolver built on it.
        assert merge_map == {"H6OH04173": "H2OH04164"}

    def test_prefers_the_confirmed_general_row_as_the_survivor(self):
        confirmed = _cand("ZZZ", "SMITH, JOHN", contributions=100.0, cash_on_hand=50.0, confirmed_general=True)
        unconfirmed = _cand("AAA", "SMITH, JOHN", contributions=100.0, cash_on_hand=50.0)

        merge_map = dedupe_merge_map([confirmed, unconfirmed])

        # AAA sorts first alphabetically -- proving this isn't just an
        # id-sort tie-break, confirmed_general must be what wins.
        assert merge_map == {"AAA": "ZZZ"}

    def test_no_duplicates_returns_an_empty_map(self):
        a = _cand("A", "SMITH, JOHN", contributions=100.0, cash_on_hand=50.0)
        b = _cand("B", "DOE, JANE", contributions=200.0, cash_on_hand=75.0)
        assert dedupe_merge_map([a, b]) == {}


class TestResolveCandidateId:
    def test_resolves_a_dropped_id_to_the_surviving_one(self):
        older = _cand("H2OH04164", "WILSON, TAMARA", party="IND", contributions=22049.51, cash_on_hand=520819.93)
        newer = _cand("H6OH04173", "WILSON, TAMARA", party="DEM", contributions=22049.51, cash_on_hand=520819.93)

        # H6OH04173 is the one dedupe_candidates would drop -- a stored
        # matched_candidate_id pointing at it must resolve to the id the
        # race's own candidate list still shows.
        assert resolve_candidate_id("H6OH04173", [older, newer]) == "H2OH04164"

    def test_a_surviving_or_unrelated_id_resolves_to_itself(self):
        older = _cand("H2OH04164", "WILSON, TAMARA", party="IND", contributions=22049.51, cash_on_hand=520819.93)
        newer = _cand("H6OH04173", "WILSON, TAMARA", party="DEM", contributions=22049.51, cash_on_hand=520819.93)

        assert resolve_candidate_id("H2OH04164", [older, newer]) == "H2OH04164"
        assert resolve_candidate_id("SOME-OTHER-ID", [older, newer]) == "SOME-OTHER-ID"


class TestNormalizedSurname:
    def test_strips_a_generational_suffix_from_either_side_of_the_name(self):
        # Real Missouri data (test_elections_state_ballot.py's
        # test_a_generational_suffix_on_either_side_of_the_name_still_
        # matches uses the same real pair).
        assert normalized_surname("ONDER JR, ROBERT FRANK") == "onder"
        assert normalized_surname("ONDER, ROBERT FOR JR.") == "onder"

    def test_different_surnames_are_not_normalized_together(self):
        assert normalized_surname("BROWN, SHARON") != normalized_surname("GHUSAR, MANDY")
