"""Tests for GET /elections/states/{state} — the ballot-centric per-state
view. Same direct-router-call convention as test_elections_api.py.
"""

import json
from datetime import datetime

import pytest
from fastapi import HTTPException

from app.api import elections
from app.models import BallotMeasure, Candidate, MeasureCoverage, Race, RaceCoverageItem, Representative, Senator


def _body(response):
    return json.loads(response.body)


def _race(db, race_id, state, office="S", district=None, cycle_year=2026):
    r = Race(id=race_id, cycle_year=cycle_year, office=office, state=state, district=district)
    db.add(r)
    return r


def _coverage(db, race_id, url, **overrides):
    defaults = dict(
        source_type="news", source_name="AP News", title="A story", summary="Summary.",
    )
    defaults.update(overrides)
    item = RaceCoverageItem(race_id=race_id, url=url, **defaults)
    db.add(item)
    return item


def _candidate(db, cand_id, race_id, name, **overrides):
    defaults = dict(party="DEM")
    defaults.update(overrides)
    c = Candidate(id=cand_id, race_id=race_id, name=name, **defaults)
    db.add(c)
    return c


def _senator(db, sid, name, state, **overrides):
    defaults = dict(party="D")
    defaults.update(overrides)
    s = Senator(id=sid, name=name, state=state, **defaults)
    db.add(s)
    return s


def _representative(db, rid, name, state, district, **overrides):
    defaults = dict(party="D")
    defaults.update(overrides)
    r = Representative(id=rid, name=name, state=state, district=district, **defaults)
    db.add(r)
    return r


def test_404_for_a_code_that_is_not_a_state_with_federal_races(db_session):
    with pytest.raises(HTTPException) as exc_info:
        elections.state_ballot("ZZ", db_session)
    assert exc_info.value.status_code == 404


def test_state_code_is_case_insensitive(db_session):
    _race(db_session, "2026-SEN-GA", "GA")
    db_session.commit()

    data = _body(elections.state_ballot("ga", db_session))
    assert data["state"] == "GA"


def test_splits_races_into_senate_and_house(db_session):
    _race(db_session, "2026-SEN-GA", "GA", office="S")
    _race(db_session, "2026-HOUSE-GA-6", "GA", office="H", district=6)
    _race(db_session, "2026-HOUSE-GA-1", "GA", office="H", district=1)
    db_session.commit()

    data = _body(elections.state_ballot("GA", db_session))
    assert len(data["senateRaces"]) == 1
    # District ascending, not insertion order.
    assert [r["district"] for r in data["houseRaces"]] == [1, 6]


def test_house_only_state_has_no_senate_race_this_cycle(db_session):
    """A state's OTHER Senate class isn't up this cycle — the response
    must say "no Senate race", not error or fabricate one. AZ specifically
    (not GA): AZ is Class I and III, neither of which is up in 2026
    (Class II), so this is a genuine not-up-this-cycle case, not a data
    gap — real-world ground truth verified against seats_up_for_year."""
    _race(db_session, "2026-HOUSE-AZ-1", "AZ", office="H", district=1)
    db_session.commit()

    data = _body(elections.state_ballot("AZ", db_session))
    assert data["senateRaces"] == []
    assert len(data["houseRaces"]) == 1
    # Computed from the real Senate class rotation, not fabricated — AZ's
    # soonest regular seat after 2026 is 2028 (Class III; its other seat,
    # Class I, isn't up again until 2030).
    assert data["nextSenateElection"] == 2028


def test_senate_race_present_leaves_next_senate_election_null(db_session):
    """The field only answers a question the page is actually asking —
    once a Senate race exists this cycle, there's nothing to explain."""
    _race(db_session, "2026-SEN-GA", "GA", office="S")
    db_session.commit()

    data = _body(elections.state_ballot("GA", db_session))
    assert data["nextSenateElection"] is None


def test_senate_race_missing_but_state_is_up_this_cycle_stays_null(db_session):
    """GA IS Class II — up in 2026 — but its Race row hasn't synced (a
    real pipeline-lag failure mode this codebase has hit before). Must
    NOT claim a fabricated "next election" year here: that would tell a
    voter their real, on-the-ballot Senate race isn't up until later.
    Empty senateRaces alone isn't enough to explain — the calendar has to
    actually agree the seat isn't up this cycle."""
    _race(db_session, "2026-HOUSE-GA-6", "GA", office="H", district=6)
    db_session.commit()

    data = _body(elections.state_ballot("GA", db_session))
    assert data["senateRaces"] == []
    assert data["nextSenateElection"] is None


def test_candidate_list_is_not_truncated_to_top_two(db_session):
    """The bug this endpoint exists to not repeat: _race_summary (the
    map/directory view) intentionally truncates to the top 2 by cash on
    hand. A ballot has to show every real option, not just the best-
    funded two."""
    _race(db_session, "2026-SEN-GA", "GA")
    for i in range(5):
        _candidate(db_session, f"S{i}", "2026-SEN-GA", f"CANDIDATE {i}", cash_on_hand=float(i))
    db_session.commit()

    data = _body(elections.state_ballot("GA", db_session))
    assert len(data["senateRaces"][0]["candidates"]) == 5


def test_confirmed_candidates_filter_out_defeated_primary_fec_filers(db_session):
    """The real bug this fix addresses, verified live on production: TX's
    2026 Senate race listed 8 Republicans + 6 Democrats as active ballot
    options months after the actual primary/runoff had already resolved
    to exactly one nominee per party. Once ANY candidate in a race is
    confirmed_general, only confirmed candidates show — an unconfirmed
    FEC filer (who may well have lost their primary) is not a real
    ballot option."""
    _race(db_session, "2026-SEN-GA", "GA")
    _candidate(db_session, "WINNER", "2026-SEN-GA", "PAXTON, KEN", confirmed_general=True)
    _candidate(db_session, "LOSER", "2026-SEN-GA", "CORNYN, JOHN", confirmed_general=False)
    db_session.commit()

    data = _body(elections.state_ballot("GA", db_session))
    candidates = data["senateRaces"][0]["candidates"]
    assert [c["id"] for c in candidates] == ["WINNER"]


def test_no_confirmed_data_falls_back_to_every_fec_filer(db_session):
    """A race with no confirmed_general candidates at all (state not
    covered yet, or genuinely pre-primary) is unchanged from before this
    feature existed — never narrows to zero just because nothing's been
    confirmed."""
    _race(db_session, "2026-SEN-GA", "GA")
    _candidate(db_session, "A", "2026-SEN-GA", "SMITH, JANE", confirmed_general=False)
    _candidate(db_session, "B", "2026-SEN-GA", "DOE, JOHN", confirmed_general=False)
    db_session.commit()

    data = _body(elections.state_ballot("GA", db_session))
    assert len(data["senateRaces"][0]["candidates"]) == 2


def test_two_fec_ids_for_the_same_person_collapse_to_one(db_session):
    """The real bug this fix addresses, verified live across 22 real 2026
    races: Ohio's real House District 4 lists "WILSON, TAMARA" twice under
    two different FEC candidate ids (one DEM, one IND) with byte-identical
    contributions and cash on hand -- a refile that got a new id, not two
    people. Real values, from the live production pull."""
    _race(db_session, "2026-HOUSE-OH-4", "OH", office="H", district=4)
    _candidate(
        db_session, "H6OH04173", "2026-HOUSE-OH-4", "WILSON, TAMARA",
        party="DEM", contributions=22049.51, cash_on_hand=520819.93,
    )
    _candidate(
        db_session, "H2OH04164", "2026-HOUSE-OH-4", "WILSON, TAMARA",
        party="IND", contributions=22049.51, cash_on_hand=520819.93,
    )
    db_session.commit()

    data = _body(elections.state_ballot("OH", db_session))
    candidates = data["houseRaces"][0]["candidates"]
    assert len(candidates) == 1


def test_a_refiled_candidate_with_a_typo_corrected_name_still_collapses(db_session):
    """Real Maine Senate data: "CALABRESE, CARMEM VINCENT MR." and
    "CALABRESE, CARMEN VINCENT MR." (one letter apart -- a name-typo
    correction on refiling) under two ids, both reporting -$3,500 cash on
    hand -- a real case where the shared fingerprint is NEGATIVE, and
    where the surname match must survive a near-miss first name."""
    _race(db_session, "2026-SEN-ME", "ME", office="S")
    _candidate(
        db_session, "S6ME00316", "2026-SEN-ME", "CALABRESE, CARMEM VINCENT MR.",
        party="REP", contributions=17759.71, cash_on_hand=-3500.0,
    )
    _candidate(
        db_session, "S6ME00324", "2026-SEN-ME", "CALABRESE, CARMEN VINCENT MR.",
        party="REP", contributions=17759.71, cash_on_hand=-3500.0,
    )
    db_session.commit()

    data = _body(elections.state_ballot("ME", db_session))
    assert len(data["senateRaces"][0]["candidates"]) == 1


def test_a_generational_suffix_on_either_side_of_the_name_still_matches(db_session):
    """Real Missouri data: "ONDER JR, ROBERT FRANK" (suffix attached to
    the surname) and "ONDER, ROBERT FOR JR." (suffix trailing the first
    name instead) -- FEC doesn't put JR/SR in a consistent place, so the
    surname normalization has to strip it from either side."""
    _race(db_session, "2026-HOUSE-MO-3", "MO", office="H", district=3)
    _candidate(
        db_session, "H8MO09146", "2026-HOUSE-MO-3", "ONDER JR, ROBERT FRANK",
        party="REP", contributions=878006.03, cash_on_hand=471458.89,
    )
    _candidate(
        db_session, "H4MO03221", "2026-HOUSE-MO-3", "ONDER, ROBERT FOR JR.",
        party="REP", contributions=878006.03, cash_on_hand=471458.89,
    )
    db_session.commit()

    data = _body(elections.state_ballot("MO", db_session))
    assert len(data["houseRaces"][0]["candidates"]) == 1


def test_exact_name_duplicate_in_the_same_race_collapses(db_session):
    """Real Ohio Senate data, live-verified 2026-09-04: "VOLPE,
    CHRISTOPHER" appears twice with identical (contributions,
    cash_on_hand) -- the plainest real case, no name-variant handling
    needed, just two ids for the one real filer."""
    _race(db_session, "2026-SEN-OH-SPECIAL", "OH", office="S", cycle_year=2026)
    _candidate(
        db_session, "S6OH00353", "2026-SEN-OH-SPECIAL", "VOLPE, CHRISTOPHER",
        party="DEM", contributions=4317.18, cash_on_hand=168.3,
    )
    _candidate(
        db_session, "S6OH00346", "2026-SEN-OH-SPECIAL", "VOLPE, CHRISTOPHER",
        party="DEM", contributions=4317.18, cash_on_hand=168.3,
    )
    db_session.commit()

    data = _body(elections.state_ballot("OH", db_session))
    volpes = [c for c in data["senateRaces"][0]["candidates"] if c["name"] == "VOLPE, CHRISTOPHER"]
    assert len(volpes) == 1


def test_identical_financials_alone_do_not_merge_two_different_people(db_session):
    """The real negative case that rules out a financials-only rule: real
    California District 4 data shows "BROWN, SHARON" and "GHUSAR, MANDY"
    -- two people with nothing in common -- both reporting exactly $7,000
    raised and $0 cash on hand. Coincidental round numbers, not the same
    candidate; the completely different surnames must block the merge."""
    _race(db_session, "2026-HOUSE-CA-4", "CA", office="H", district=4)
    _candidate(
        db_session, "H6CA04206", "2026-HOUSE-CA-4", "BROWN, SHARON",
        party="REP", contributions=7000.0, cash_on_hand=0.0,
    )
    _candidate(
        db_session, "H6CA08223", "2026-HOUSE-CA-4", "GHUSAR, MANDY",
        party="DEM", contributions=7000.0, cash_on_hand=0.0,
    )
    db_session.commit()

    data = _body(elections.state_ballot("CA", db_session))
    assert len(data["houseRaces"][0]["candidates"]) == 2


def test_never_synced_or_zero_dollar_candidates_are_never_merged_on_that_alone(db_session):
    """A shared (None, None) or (0, 0) fingerprint is common among minor
    filers and proves nothing about being the same person -- must never
    be treated as dedup evidence even when two such candidates also
    happen to share a surname."""
    _race(db_session, "2026-SEN-GA", "GA")
    _candidate(db_session, "A", "2026-SEN-GA", "SMITH, JOHN", contributions=None, cash_on_hand=None)
    _candidate(db_session, "B", "2026-SEN-GA", "SMITH, JANE", contributions=0.0, cash_on_hand=0.0)
    db_session.commit()

    data = _body(elections.state_ballot("GA", db_session))
    assert len(data["senateRaces"][0]["candidates"]) == 2


def test_pvi_fallback_matches_race_detail_behavior(db_session):
    """House PVI prefers the district map, flagged 'district'; falls
    back to statewide, flagged 'state' — same contract race_detail
    already has, verified consistent rather than reimplemented
    differently here."""
    _race(db_session, "2026-HOUSE-CA-12", "CA", office="H", district=12)
    db_session.commit()

    data = _body(elections.state_ballot("CA", db_session))
    house = data["houseRaces"][0]
    assert house["pvi"] == elections.get_district_pvi_map()["CA-12"]
    assert house["pviLevel"] == "district"


def test_election_date_and_cycle_year_agree():
    """electionDate and cycleYear must come from the same source of
    truth (next_election_day) — a mismatch would mean the header's date
    and the year label on the page disagree."""
    from app.pipeline.election_pipeline import current_election_cycle

    assert current_election_cycle() == int(
        elections.next_election_day(elections.utcnow().date()).year
    )


def test_state_pvi_is_included_at_top_level(db_session):
    _race(db_session, "2026-SEN-GA", "GA")
    db_session.commit()

    data = _body(elections.state_ballot("GA", db_session))
    assert isinstance(data["statePvi"], int)


def test_house_race_includes_its_district_counties(db_session):
    """Lets a voter who knows their county but not their district number
    recognize their district in the picker (real Census-sourced data —
    see county_district_crosswalk.json)."""
    _race(db_session, "2026-HOUSE-GA-6", "GA", office="H", district=6)
    db_session.commit()

    data = _body(elections.state_ballot("GA", db_session))
    counties = data["houseRaces"][0]["counties"]
    assert counties
    assert all("County" in c or "(part)" in c for c in counties)


def test_senate_race_has_no_counties_field_populated(db_session):
    """Counties are a House-district concept — a statewide Senate race
    must not claim a county list."""
    _race(db_session, "2026-SEN-GA", "GA")
    db_session.commit()

    data = _body(elections.state_ballot("GA", db_session))
    assert data["senateRaces"][0]["counties"] is None


def test_house_race_with_no_crosswalk_entry_gets_null_counties(db_session):
    """A district number outside the real 1..N range for that state
    (bad data, not a real district) must not silently return an empty or
    wrong county list — null, same never-guess discipline as PVI
    fallback."""
    _race(db_session, "2026-HOUSE-GA-99", "GA", office="H", district=99)
    db_session.commit()

    data = _body(elections.state_ballot("GA", db_session))
    assert data["houseRaces"][0]["counties"] is None


class TestIncumbentRecordLink:
    """The wrong match here would attribute one member's voting record
    to a different person on the ballot — every case here is either a
    real, unambiguous match or None, never a guess."""

    def test_house_incumbent_links_by_exact_district(self, db_session):
        _race(db_session, "2026-HOUSE-GA-6", "GA", office="H", district=6)
        _candidate(db_session, "H1", "2026-HOUSE-GA-6", "MCBATH, LUCY", incumbent_challenge="I")
        _representative(db_session, "R-MCBATH", "Lucy McBath", "GA", 6, score_funding_independence=70.0)
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        cand = data["houseRaces"][0]["candidates"][0]
        assert cand["incumbentRecord"]["id"] == "R-MCBATH"
        assert isinstance(cand["incumbentRecord"]["score"], float)

    def test_house_non_incumbent_gets_no_link(self, db_session):
        _race(db_session, "2026-HOUSE-GA-6", "GA", office="H", district=6)
        _candidate(db_session, "H1", "2026-HOUSE-GA-6", "CHALLENGER, PAT", incumbent_challenge="C")
        _representative(db_session, "R-MCBATH", "Lucy McBath", "GA", 6)
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        assert data["houseRaces"][0]["candidates"][0]["incumbentRecord"] is None

    def test_house_incumbent_with_no_matching_representative_row_gets_no_link(self, db_session):
        """A district with no synced Representative row (e.g. a brand
        new district) must not crash or guess — just no link."""
        _race(db_session, "2026-HOUSE-GA-99", "GA", office="H", district=99)
        _candidate(db_session, "H1", "2026-HOUSE-GA-99", "NOBODY, PAT", incumbent_challenge="I")
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        assert data["houseRaces"][0]["candidates"][0]["incumbentRecord"] is None

    def test_senate_incumbent_links_by_unique_last_name_within_state(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S1", "2026-SEN-GA", "OSSOFF, JON", incumbent_challenge="I")
        _senator(db_session, "SEN-OSSOFF", "Jon Ossoff", "GA", score_funding_independence=80.0)
        # A senator from a DIFFERENT state must never match.
        _senator(db_session, "SEN-OTHER", "Someone Ossoff", "TX")
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        assert data["senateRaces"][0]["candidates"][0]["incumbentRecord"]["id"] == "SEN-OSSOFF"

    def test_senate_incumbent_gets_no_link_when_last_name_is_ambiguous_within_state(self, db_session):
        """Two of a state's senators sharing a last name is the one
        scenario this can't safely disambiguate — must fall back to no
        link, not guess which one."""
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S1", "2026-SEN-GA", "SMITH, JANE", incumbent_challenge="I")
        _senator(db_session, "SEN-1", "Jane Smith", "GA")
        _senator(db_session, "SEN-2", "Robert Smith", "GA")
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        assert data["senateRaces"][0]["candidates"][0]["incumbentRecord"] is None

    def test_senate_incumbent_ignores_a_non_current_senator(self, db_session):
        """A departed/vacant-seat Senator row must not be linked as if
        still serving — same is_current discipline the model itself
        documents."""
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S1", "2026-SEN-GA", "OSSOFF, JON", incumbent_challenge="I")
        _senator(db_session, "SEN-OSSOFF", "Jon Ossoff", "GA", is_current=False)
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        assert data["senateRaces"][0]["candidates"][0]["incumbentRecord"] is None

    def test_senate_last_name_match_is_token_exact_not_substring(self, db_session):
        """A candidate's last name being a SUBSTRING of an unrelated
        senator's name must not count as a match — "lee" inside
        "leeman" is coincidence, not identity. Real regression this
        guards: an earlier version used `last_name in name.lower()`."""
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S1", "2026-SEN-GA", "LEE, JANE", incumbent_challenge="I")
        _senator(db_session, "SEN-1", "Robert Leeman", "GA")
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        assert data["senateRaces"][0]["candidates"][0]["incumbentRecord"] is None

    def test_senate_multi_word_last_name_still_matches(self, db_session):
        """The token-exact match must still handle a multi-word surname
        like "Van Hollen" — this is exactly why the fix matches
        TRAILING tokens rather than just the single last word."""
        _race(db_session, "2026-SEN-MD", "MD")
        _candidate(db_session, "S1", "2026-SEN-MD", "VAN HOLLEN, CHRIS", incumbent_challenge="I")
        _senator(db_session, "SEN-VH", "Chris Van Hollen", "MD")
        db_session.commit()

        data = _body(elections.state_ballot("MD", db_session))
        assert data["senateRaces"][0]["candidates"][0]["incumbentRecord"]["id"] == "SEN-VH"

    def test_house_incumbent_matching_also_uses_token_exact_match(self, db_session):
        """Same substring-coincidence guard applies to the House path."""
        _race(db_session, "2026-HOUSE-GA-6", "GA", office="H", district=6)
        _candidate(db_session, "H1", "2026-HOUSE-GA-6", "LEE, JANE", incumbent_challenge="I")
        _representative(db_session, "R-1", "Robert Leeman", "GA", 6)
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        assert data["houseRaces"][0]["candidates"][0]["incumbentRecord"] is None

    def test_incumbent_matching_does_not_query_per_candidate(self, db_session, monkeypatch):
        """Representative/Senator must be fetched once per request, not
        once per incumbent — a state can have ~50 House races, and
        querying inside the per-candidate loop would be exactly the N+1
        shape .candidates' selectinload already exists to avoid for a
        different relationship. Regression test for that fix."""
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S1", "2026-SEN-GA", "OSSOFF, JON", incumbent_challenge="I")
        _senator(db_session, "SEN-OSSOFF", "Jon Ossoff", "GA")
        for d in range(1, 4):
            rid = f"2026-HOUSE-GA-{d}"
            _race(db_session, rid, "GA", office="H", district=d)
            _candidate(db_session, f"H{d}", rid, f"REP{d}, PAT", incumbent_challenge="I")
            _representative(db_session, f"R-{d}", f"Pat Rep{d}", "GA", d)
        db_session.commit()

        query_counts: dict[str, int] = {"Representative": 0, "Senator": 0}
        original_query = db_session.query

        def counting_query(*args, **kwargs):
            for arg in args:
                name = getattr(arg, "__name__", None)
                if name in query_counts:
                    query_counts[name] += 1
            return original_query(*args, **kwargs)

        monkeypatch.setattr(db_session, "query", counting_query)
        data = _body(elections.state_ballot("GA", db_session))

        assert query_counts["Representative"] == 1
        assert query_counts["Senator"] == 1
        # Sanity: the batched lookups still produced correct matches.
        assert data["senateRaces"][0]["candidates"][0]["incumbentRecord"]["id"] == "SEN-OSSOFF"
        assert all(
            r["candidates"][0]["incumbentRecord"] is not None for r in data["houseRaces"]
        )

    def test_incumbent_score_matches_the_shared_compute_overall_score_formula(self, db_session):
        """Not a separately-derived number — the exact same formula the
        leaderboard and profile page use, so a score can't read
        differently depending which page shows it."""
        from app.pipeline.analyze.score_calculator import compute_overall_score

        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S1", "2026-SEN-GA", "OSSOFF, JON", incumbent_challenge="I")
        senator = _senator(
            db_session, "SEN-OSSOFF", "Jon Ossoff", "GA",
            score_funding_independence=65.0, score_independent_voting=40.0,
        )
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        assert data["senateRaces"][0]["candidates"][0]["incumbentRecord"]["score"] == (
            compute_overall_score(senator)
        )


class TestStateCoverage:
    """Front-and-center top-of-page coverage teaser (2026-08 review: news
    coverage and funding shouldn't require a click-through)."""

    def test_aggregates_coverage_across_senate_and_house_races(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA", office="S")
        _race(db_session, "2026-HOUSE-GA-6", "GA", office="H", district=6)
        _coverage(db_session, "2026-SEN-GA", "https://apnews.com/senate-story", title="Senate race")
        _coverage(db_session, "2026-HOUSE-GA-6", "https://apnews.com/house-story", title="House race")
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        urls = {item["url"] for item in data["coverage"]}
        assert urls == {"https://apnews.com/senate-story", "https://apnews.com/house-story"}

    def test_each_item_carries_which_race_its_about(self, db_session):
        _race(db_session, "2026-HOUSE-GA-6", "GA", office="H", district=6)
        _coverage(db_session, "2026-HOUSE-GA-6", "https://apnews.com/a")
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        assert data["coverage"][0]["race"] == {
            "id": "2026-HOUSE-GA-6", "office": "H", "district": 6,
        }

    def test_deduplicates_the_same_story_matched_to_two_races(self, db_session):
        """A single article can name candidates from two different races
        in the same state (e.g. covers both the Senate and a House
        race), producing two DB rows with the same url under different
        race_ids — the reader must not see the same headline twice."""
        _race(db_session, "2026-SEN-GA", "GA", office="S")
        _race(db_session, "2026-HOUSE-GA-6", "GA", office="H", district=6)
        _coverage(db_session, "2026-SEN-GA", "https://apnews.com/both-races")
        _coverage(db_session, "2026-HOUSE-GA-6", "https://apnews.com/both-races")
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        assert len(data["coverage"]) == 1

    def test_deduplicated_story_picks_a_race_deterministically(self, db_session):
        """The two rows for a deduplicated story share the same
        published_at/fetched_at (same article, same ingest pass), so
        which race's badge wins must not depend on undefined SQL tie-
        break order — the `.id` tiebreaker makes it repeatable across
        calls rather than however the DB happens to return tied rows."""
        _race(db_session, "2026-SEN-GA", "GA", office="S")
        _race(db_session, "2026-HOUSE-GA-6", "GA", office="H", district=6)
        _coverage(db_session, "2026-SEN-GA", "https://apnews.com/both-races")
        _coverage(db_session, "2026-HOUSE-GA-6", "https://apnews.com/both-races")
        db_session.commit()

        winners = {
            _body(elections.state_ballot("GA", db_session))["coverage"][0]["race"]["id"]
            for _ in range(5)
        }
        assert len(winners) == 1

    def test_excludes_coverage_from_a_different_state(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA", office="S")
        _race(db_session, "2026-SEN-CA", "CA", office="S")
        _coverage(db_session, "2026-SEN-GA", "https://apnews.com/ga-story")
        _coverage(db_session, "2026-SEN-CA", "https://apnews.com/ca-story")
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        assert [item["url"] for item in data["coverage"]] == ["https://apnews.com/ga-story"]

    def test_empty_list_not_missing_key_when_no_coverage(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        assert data["coverage"] == []

    def test_ordered_newest_first(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        _coverage(
            db_session, "2026-SEN-GA", "https://apnews.com/older",
            published_at=datetime(2026, 7, 1),
        )
        _coverage(
            db_session, "2026-SEN-GA", "https://apnews.com/newer",
            published_at=datetime(2026, 7, 20),
        )
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        assert [item["url"] for item in data["coverage"]] == [
            "https://apnews.com/newer", "https://apnews.com/older",
        ]


class TestBallotMeasures:
    """The statewide-ballot-measures fields folded into this endpoint
    from the ballot-measures feature — measures, measureCoverage,
    officialLookup, omits."""

    @staticmethod
    def _election_day():
        return elections.next_election_day(elections.utcnow().date()).isoformat()

    def test_measures_are_included_verbatim(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        db_session.add(BallotMeasure(
            id="ga-measure-1", state="GA", election_date=self._election_day(),
            number="Amendment 1", title="Property tax exemption",
            official_title="An act relating to property tax exemptions.",
            source_name="Vote Smart",
        ))
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        assert len(data["measures"]) == 1
        assert data["measures"][0]["number"] == "Amendment 1"
        assert data["measures"][0]["officialTitle"] == (
            "An act relating to property tax exemptions."
        )

    def test_no_coverage_row_defaults_to_not_yet_covered(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        assert data["measures"] == []
        assert data["measureCoverage"]["status"] == MeasureCoverage.NOT_YET_COVERED

    def test_confirmed_none_is_reported_not_conflated_with_not_yet_covered(self, db_session):
        """A state with genuinely zero measures must not render like a
        state Civitas simply hasn't ingested yet."""
        _race(db_session, "2026-SEN-GA", "GA")
        db_session.add(MeasureCoverage(
            state="GA", election_date=self._election_day(),
            status=MeasureCoverage.CONFIRMED_NONE, source_name="Vote Smart",
        ))
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        assert data["measures"] == []
        assert data["measureCoverage"]["status"] == MeasureCoverage.CONFIRMED_NONE
        assert data["measureCoverage"]["sourceName"] == "Vote Smart"

    def test_official_lookup_and_omits_are_always_present(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        assert data["officialLookup"]["url"]
        assert isinstance(data["omits"], list) and len(data["omits"]) > 0

    def test_dc_is_a_valid_ballot_jurisdiction_despite_no_federal_race(self, db_session):
        """DC has no voting House/Senate race and is absent from
        STATES_WITH_FEDERAL_RACES, but it does vote on statewide
        initiatives — the ballot page must not 404 it."""
        data = _body(elections.state_ballot("DC", db_session))
        assert data["senateRaces"] == []
        assert data["houseRaces"] == []
        assert any("Delegate" in item for item in data["omits"])

    def test_a_territory_with_no_ballot_jurisdiction_still_404s(self, db_session):
        with pytest.raises(HTTPException) as exc_info:
            elections.state_ballot("GU", db_session)
        assert exc_info.value.status_code == 404
