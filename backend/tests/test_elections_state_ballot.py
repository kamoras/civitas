"""Tests for GET /elections/states/{state} — the ballot-centric per-state
view. Same direct-router-call convention as test_elections_api.py.
"""

import json
from datetime import datetime

import pytest
from fastapi import HTTPException

from app.api import elections
from app.models import (
    BallotMeasure,
    Candidate,
    MeasureCoverage,
    Race,
    RaceResult,
    RaceCoverageItem,
    Representative,
    Senator,
)


@pytest.fixture(autouse=True)
def _campaign_clock(monkeypatch):
    """These tests build a 2026-cycle roster. Pinned inside the 2026
    campaign: on the real clock, once the results window closes (Nov 18,
    2026) the site's cycle is 2028 and they read an empty roster. A test
    that needs another date patches election_today itself."""
    from datetime import date

    monkeypatch.setattr("app.election_phase.election_today", lambda: date(2026, 10, 1))


# Every fixture here is a 2026 race; pin the clock inside that cycle so the
# tests don't all fail on election night (conftest.freeze_utcnow).
@pytest.fixture(autouse=True)
def _in_the_2026_cycle(freeze_utcnow):
    freeze_utcnow(datetime(2026, 9, 30, 12, 0))


@pytest.fixture(autouse=True)
def _the_119th_sits(monkeypatch):
    """Redrawn states are measured against the sitting Congress's lines
    (live_results.sync.redrawn_states). Before Jan 3, 2027 that is the
    119th, on the old map; pin it so these 2026 fixtures don't flip then."""
    from app.config import settings

    monkeypatch.setattr(settings, "CURRENT_CONGRESS", 119)


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


def _filer(cand_id, name, party, contributions, cash_on_hand):
    return cand_id, name, dict(party=party, contributions=contributions, cash_on_hand=cash_on_hand)


@pytest.mark.parametrize("race_id, state, office, district, filers, expected", [
    # The real bug this fix addresses, verified live across 22 real 2026
    # races: Ohio's real House District 4 lists "WILSON, TAMARA" twice under
    # two different FEC candidate ids (one DEM, one IND) with byte-identical
    # contributions and cash on hand -- a refile that got a new id, not two
    # people. Real values, from the live production pull.
    pytest.param("2026-HOUSE-OH-4", "OH", "H", 4, [
        _filer("H6OH04173", "WILSON, TAMARA", "DEM", 22049.51, 520819.93),
        _filer("H2OH04164", "WILSON, TAMARA", "IND", 22049.51, 520819.93),
    ], 1, id="two_fec_ids_for_the_same_person_collapse"),
    # Real Maine Senate data: one letter apart -- a name-typo correction on
    # refiling -- under two ids, both reporting -$3,500 cash on hand: a real
    # case where the shared fingerprint is NEGATIVE, and where the surname
    # match must survive a near-miss first name.
    pytest.param("2026-SEN-ME", "ME", "S", None, [
        _filer("S6ME00316", "CALABRESE, CARMEM VINCENT MR.", "REP", 17759.71, -3500.0),
        _filer("S6ME00324", "CALABRESE, CARMEN VINCENT MR.", "REP", 17759.71, -3500.0),
    ], 1, id="typo_corrected_name_still_collapses"),
    # Real Missouri data: the suffix attached to the surname in one, trailing
    # the first name in the other -- FEC doesn't put JR/SR in a consistent
    # place, so the surname normalization has to strip it from either side.
    pytest.param("2026-HOUSE-MO-3", "MO", "H", 3, [
        _filer("H8MO09146", "ONDER JR, ROBERT FRANK", "REP", 878006.03, 471458.89),
        _filer("H4MO03221", "ONDER, ROBERT FOR JR.", "REP", 878006.03, 471458.89),
    ], 1, id="generational_suffix_on_either_side_still_matches"),
    # Real Ohio Senate data, live-verified 2026-09-04: the plainest real
    # case, no name-variant handling needed, just two ids for one filer.
    pytest.param("2026-SEN-OH-SPECIAL", "OH", "S", None, [
        _filer("S6OH00353", "VOLPE, CHRISTOPHER", "DEM", 4317.18, 168.3),
        _filer("S6OH00346", "VOLPE, CHRISTOPHER", "DEM", 4317.18, 168.3),
    ], 1, id="exact_name_duplicate_collapses"),
    # The real negative case that rules out a financials-only rule: real
    # California District 4 data, two people with nothing in common both
    # reporting exactly $7,000 raised and $0 cash on hand. Coincidental
    # round numbers; the completely different surnames must block the merge.
    pytest.param("2026-HOUSE-CA-4", "CA", "H", 4, [
        _filer("H6CA04206", "BROWN, SHARON", "REP", 7000.0, 0.0),
        _filer("H6CA08223", "GHUSAR, MANDY", "DEM", 7000.0, 0.0),
    ], 2, id="identical_financials_alone_do_not_merge_two_people"),
    # A shared (None, None) or (0, 0) fingerprint is common among minor
    # filers and proves nothing about being the same person -- never dedup
    # evidence, even when the two also happen to share a surname.
    pytest.param("2026-SEN-GA", "GA", "S", None, [
        _filer("A", "SMITH, JOHN", "DEM", None, None),
        _filer("B", "SMITH, JANE", "DEM", 0.0, 0.0),
    ], 2, id="never_synced_or_zero_dollar_never_merged_on_that_alone"),
])
def test_duplicate_fec_filings_collapse_only_on_real_evidence(
    db_session, race_id, state, office, district, filers, expected,
):
    _race(db_session, race_id, state, office=office, district=district)
    for cand_id, name, fields in filers:
        _candidate(db_session, cand_id, race_id, name, **fields)
    db_session.commit()

    data = _body(elections.state_ballot(state, db_session))
    (race,) = data["senateRaces"] + data["houseRaces"]
    assert len(race["candidates"]) == expected


def test_dedup_keeps_the_confirmed_general_candidate_over_id_order(db_session):
    """The tie-break bug this guards against: the old rule only trusted
    "confirmed" when EXACTLY ONE dupe had confirmed_general OR
    on_primary_ballot set, else fell back to an arbitrary id-sort over
    the whole group. Here both flags are set, but on DIFFERENT rows (a
    realistic case -- confirmed_general and on_primary_ballot come from
    separate state-source lookups) and the id-sort would pick "A"
    (confirmed_general=False) over "Z" (confirmed_general=True). The
    fix ranks confirmed_general above on_primary_ballot above neither,
    so the actually-confirmed row always survives regardless of id."""
    _race(db_session, "2026-SEN-GA", "GA")
    _candidate(
        db_session, "A", "2026-SEN-GA", "SMITH, JOHN",
        contributions=5000.0, cash_on_hand=1000.0, on_primary_ballot=True, confirmed_general=False,
    )
    _candidate(
        db_session, "Z", "2026-SEN-GA", "SMITH, JOHN",
        contributions=5000.0, cash_on_hand=1000.0, on_primary_ballot=False, confirmed_general=True,
    )
    db_session.commit()

    data = _body(elections.state_ballot("GA", db_session))
    candidates = data["senateRaces"][0]["candidates"]
    assert len(candidates) == 1
    assert candidates[0]["id"] == "Z"


def test_pvi_fallback_matches_race_detail_behavior(db_session):
    """House PVI prefers the district map, flagged 'district'; falls
    back to statewide, flagged 'state' — same contract race_detail
    already has, verified consistent rather than reimplemented
    differently here."""
    _race(db_session, "2026-HOUSE-CA-12", "CA", office="H", district=12)
    db_session.commit()

    data = _body(elections.state_ballot("CA", db_session))
    house = data["houseRaces"][0]
    assert house["pvi"] == elections._election_district_pvi(2026)[0]["CA-12"]
    assert house["pviLevel"] == "district"


def test_election_date_and_cycle_year_agree(db_session):
    """electionDate and cycleYear must come from the same source of
    truth (election_phase.active_election) — a mismatch would mean the
    header's date and the year label on the page disagree."""
    _race(db_session, "2026-SEN-GA", "GA")
    data = _body(elections.state_ballot("GA", db_session))
    assert int(data["electionDate"][:4]) == data["cycleYear"]


def test_state_pvi_is_included_at_top_level(db_session):
    _race(db_session, "2026-SEN-GA", "GA")
    db_session.commit()

    data = _body(elections.state_ballot("GA", db_session))
    assert isinstance(data["statePvi"], int)


def test_a_redrawn_state_says_its_district_lines_are_new(db_session):
    _race(db_session, "2026-SEN-TX", "TX")
    _race(db_session, "2026-SEN-GA", "GA")
    db_session.commit()

    assert _body(elections.state_ballot("TX", db_session))["newDistrictLines"] is True
    assert _body(elections.state_ballot("GA", db_session))["newDistrictLines"] is False


def test_new_lines_stay_new_until_the_members_elected_on_them_sit(db_session, monkeypatch):
    """After the 2026 results window closes the site is on 2028, but until
    Jan 3, 2027 the 119th Congress — elected on the old lines — still sits:
    a redrawn state's districts are still not the ones its members hold."""
    from datetime import date

    from app.config import settings

    monkeypatch.setattr("app.election_phase.election_today", lambda: date(2026, 11, 20))
    tx = _body(elections.state_ballot("TX", db_session))
    assert tx["cycleYear"] == 2028
    assert tx["newDistrictLines"] is True
    assert _body(elections.state_ballot("GA", db_session))["newDistrictLines"] is False

    # From Jan 3 the 120th sits on the 2026 lines, and 2028 lists no new map.
    monkeypatch.setattr(settings, "CURRENT_CONGRESS", 120)
    monkeypatch.setattr("app.election_phase.election_today", lambda: date(2027, 1, 10))
    assert _body(elections.state_ballot("TX", db_session))["newDistrictLines"] is False


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

    def test_house_incumbent_whose_own_surname_carries_a_generational_suffix_still_links(self, db_session):
        """Real Missouri data: FEC's "ONDER JR, ROBERT FRANK" attaches
        the suffix to the surname segment itself. Before _incumbent_link
        reused candidate_dedup's normalized_surname, the raw
        `name.split(",")[0]` extraction included "jr" as part of the
        last name, which could never match a Representative row's plain
        "Robert Onder" -- this incumbent would silently get no link."""
        _race(db_session, "2026-HOUSE-MO-3", "MO", office="H", district=3)
        _candidate(db_session, "H8MO09146", "2026-HOUSE-MO-3", "ONDER JR, ROBERT FRANK", incumbent_challenge="I")
        _representative(db_session, "R-ONDER", "Robert Onder", "MO", 3)
        db_session.commit()

        data = _body(elections.state_ballot("MO", db_session))
        assert data["houseRaces"][0]["candidates"][0]["incumbentRecord"]["id"] == "R-ONDER"

    def test_an_incumbent_renumbered_by_a_redrawn_map_still_links(self, db_session):
        """Utah's 2026 map renumbers seats; an incumbent running in a
        district another member holds today is matched across the state's
        delegation — uniquely, or not at all."""
        _race(db_session, "2026-HOUSE-UT-3", "UT", office="H", district=3)
        _candidate(db_session, "H1", "2026-HOUSE-UT-3", "MOVER, PAT", incumbent_challenge="I")
        _representative(db_session, "R-MOVER", "Pat Mover", "UT", 1)
        _representative(db_session, "R-OTHER", "Sam Other", "UT", 3)
        db_session.commit()

        data = _body(elections.state_ballot("UT", db_session))
        record = data["houseRaces"][0]["candidates"][0]["incumbentRecord"]
        assert record["id"] == "R-MOVER"
        assert record["district"] == 1  # the seat held today, not this race's number
        assert record["seat"] == "UT-1"

    def test_a_departed_member_sharing_the_district_number_is_never_linked(self, db_session):
        """A member within the retirement grace period shares the district
        number with their successor; only current members are matched."""
        _race(db_session, "2026-HOUSE-GA-6", "GA", office="H", district=6)
        _candidate(db_session, "H1", "2026-HOUSE-GA-6", "MCBATH, LUCY", incumbent_challenge="I")
        _representative(db_session, "R-NEW", "Lucy McBath", "GA", 6)
        _representative(db_session, "R-OLD", "Old McBath", "GA", 6, is_current=False)
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        assert data["houseRaces"][0]["candidates"][0]["incumbentRecord"]["id"] == "R-NEW"

    def test_an_unchanged_map_never_links_across_districts(self, db_session):
        _race(db_session, "2026-HOUSE-GA-3", "GA", office="H", district=3)
        _candidate(db_session, "H1", "2026-HOUSE-GA-3", "MOVER, PAT", incumbent_challenge="I")
        _representative(db_session, "R-MOVER", "Pat Mover", "GA", 1)
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        assert data["houseRaces"][0]["candidates"][0]["incumbentRecord"] is None

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
        _candidate(db_session, "S1", "2026-SEN-GA", "BRENNAN, JON", incumbent_challenge="I")
        _senator(db_session, "SEN-BRENNAN", "Jon Brennan", "GA", score_funding_independence=80.0)
        # A senator from a DIFFERENT state must never match.
        _senator(db_session, "SEN-OTHER", "Someone Brennan", "TX")
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        assert data["senateRaces"][0]["candidates"][0]["incumbentRecord"]["id"] == "SEN-BRENNAN"

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
        _candidate(db_session, "S1", "2026-SEN-GA", "BRENNAN, JON", incumbent_challenge="I")
        _senator(db_session, "SEN-BRENNAN", "Jon Brennan", "GA", is_current=False)
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
        like "Van Dorn" — this is exactly why the fix matches
        TRAILING tokens rather than just the single last word."""
        _race(db_session, "2026-SEN-MD", "MD")
        _candidate(db_session, "S1", "2026-SEN-MD", "VAN DORN, CHRIS", incumbent_challenge="I")
        _senator(db_session, "SEN-VH", "Chris Van Dorn", "MD")
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
        _candidate(db_session, "S1", "2026-SEN-GA", "BRENNAN, JON", incumbent_challenge="I")
        _senator(db_session, "SEN-BRENNAN", "Jon Brennan", "GA")
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
        assert data["senateRaces"][0]["candidates"][0]["incumbentRecord"]["id"] == "SEN-BRENNAN"
        assert all(
            r["candidates"][0]["incumbentRecord"] is not None for r in data["houseRaces"]
        )

    def test_incumbent_score_matches_the_shared_compute_overall_score_formula(self, db_session):
        """Not a separately-derived number — the exact same formula the
        leaderboard and profile page use, so a score can't read
        differently depending which page shows it."""
        from app.pipeline.analyze.score_calculator import compute_overall_score

        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S1", "2026-SEN-GA", "BRENNAN, JON", incumbent_challenge="I")
        senator = _senator(
            db_session, "SEN-BRENNAN", "Jon Brennan", "GA",
            score_funding_independence=65.0, score_constituent_alignment=40.0,
        )
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        assert data["senateRaces"][0]["candidates"][0]["incumbentRecord"]["score"] == (
            compute_overall_score(senator)
        )


class TestIncumbentRecordOnceTheElectedCongressSits:
    """The 2026 results window can include January 3, 2027. From noon that
    day the 120th sits, and until a member run refreshes the roster it may
    hold either Congress: the members going in, or the winners. A link
    then is only ever the candidate's own scorecard, and says no seat."""

    @staticmethod
    def _on_jan_3(db, monkeypatch, congress):
        """The page on Jan 3, still on the 2026 results (the count moved on
        Dec 28), with `congress` sitting."""
        from datetime import date

        from app.config import settings

        db.commit()
        db.add(RaceResult(race_id=db.query(Race).first().id, election_date="2026-11-03", source_name="x",
                          tallies="[]", votes_counted=0, first_reported_at=datetime(2026, 11, 4),
                          last_change_at=datetime(2026, 12, 28), fetched_at=datetime(2026, 12, 28)))
        db.commit()
        monkeypatch.setattr("app.election_phase.election_today", lambda: date(2027, 1, 3))
        monkeypatch.setattr(settings, "CURRENT_CONGRESS", congress)
        data = _body(elections.state_ballot(db.query(Race).first().state, db))
        assert data["cycleYear"] == 2026 and data["phase"]["phase"] == "results"
        return data

    @pytest.mark.parametrize("congress,linked", [(119, True), (120, False)])
    def test_a_renumbered_member_is_matched_across_the_delegation_only_while_the_119th_sits(
        self, db_session, monkeypatch, congress, linked,
    ):
        """On a refreshed roster a unique surname across the delegation can
        be a newly elected namesake elsewhere in the state."""
        _race(db_session, "2026-HOUSE-UT-3", "UT", office="H", district=3)
        _candidate(db_session, "H1", "2026-HOUSE-UT-3", "MOVER, PAT", incumbent_challenge="I")
        _representative(db_session, "R-MOVER", "Pat Mover", "UT", 1)
        _representative(db_session, "R-OTHER", "Sam Other", "UT", 3)
        record = self._on_jan_3(db_session, monkeypatch, congress)["houseRaces"][0]["candidates"][0]["incumbentRecord"]
        if linked:
            assert record == {"id": "R-MOVER", "score": record["score"], "district": 1, "seat": "UT-1"}
        else:
            assert record is None

    @pytest.mark.parametrize("congress", [119, 120])
    def test_the_races_own_seat_still_links_but_names_no_seat_once_the_120th_sits(
        self, db_session, monkeypatch, congress,
    ):
        _race(db_session, "2026-HOUSE-GA-6", "GA", office="H", district=6)
        _candidate(db_session, "H1", "2026-HOUSE-GA-6", "MCBATH, LUCY", incumbent_challenge="I")
        _representative(db_session, "R-MCBATH", "Lucy McBath", "GA", 6)
        record = self._on_jan_3(db_session, monkeypatch, congress)["houseRaces"][0]["candidates"][0]["incumbentRecord"]
        assert record["id"] == "R-MCBATH"
        if congress == 119:
            assert record["seat"] == "GA-6"
        else:
            assert "seat" not in record and "district" not in record

    def test_a_namesake_winner_on_a_refreshed_roster_is_never_linked_to_the_member_going_in(
        self, db_session, monkeypatch,
    ):
        """The member going in lost to a challenger with the same surname,
        and the roster now holds the winner: the seat's holder matches the
        member's surname, but is the other candidate."""
        _race(db_session, "2026-HOUSE-GA-6", "GA", office="H", district=6)
        _candidate(db_session, "H1", "2026-HOUSE-GA-6", "SMITH, PAT", incumbent_challenge="I")
        _candidate(db_session, "H2", "2026-HOUSE-GA-6", "SMITH, DANA", incumbent_challenge="C", party="REP")
        _representative(db_session, "R-DANA", "Dana Smith", "GA", 6, party="R")
        data = self._on_jan_3(db_session, monkeypatch, 120)
        assert all(c["incumbentRecord"] is None for c in data["houseRaces"][0]["candidates"])

    @pytest.mark.parametrize("namesake,linked", [(False, True), (True, False)])
    def test_a_senator_links_once_the_120th_sits_unless_a_rival_shares_the_surname(
        self, db_session, monkeypatch, namesake, linked,
    ):
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S1", "2026-SEN-GA", "BRENNAN, JON", incumbent_challenge="I")
        if namesake:
            _candidate(db_session, "S2", "2026-SEN-GA", "BRENNAN, DANA", incumbent_challenge="C", party="REP")
        _senator(db_session, "SEN-BRENNAN", "Jon Brennan", "GA")
        data = self._on_jan_3(db_session, monkeypatch, 120)
        record = next(c for c in data["senateRaces"][0]["candidates"] if c["id"] == "S1")["incumbentRecord"]
        assert (record is not None and record["id"] == "SEN-BRENNAN") if linked else record is None

    def test_a_multiword_namesake_winner_is_never_linked_to_the_member_going_in(
        self, db_session, monkeypatch,
    ):
        """The roster match takes "cruz" for "Dana De La Cruz" (trailing
        tokens), so the namesake check must too: otherwise Pat Cruz's card
        links to the winner's scorecard on a refreshed roster."""
        _race(db_session, "2026-HOUSE-GA-6", "GA", office="H", district=6)
        _candidate(db_session, "H1", "2026-HOUSE-GA-6", "CRUZ, PAT", incumbent_challenge="I")
        _candidate(db_session, "H2", "2026-HOUSE-GA-6", "DE LA CRUZ, DANA", incumbent_challenge="C", party="REP")
        _representative(db_session, "R-DANA", "Dana De La Cruz", "GA", 6, party="R")
        data = self._on_jan_3(db_session, monkeypatch, 120)
        assert all(c["incumbentRecord"] is None for c in data["houseRaces"][0]["candidates"])

    def test_a_multiword_senate_namesake_is_never_linked_to_the_member_going_in(
        self, db_session, monkeypatch,
    ):
        """The same rule the other way round: the incumbent's surname is
        the longer one, and a rival's is its trailing token."""
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S1", "2026-SEN-GA", "VAN DORN, CHRIS", incumbent_challenge="I")
        _candidate(db_session, "S2", "2026-SEN-GA", "DORN, DANA", incumbent_challenge="C", party="REP")
        _senator(db_session, "SEN-VH", "Chris Van Dorn", "GA")
        data = self._on_jan_3(db_session, monkeypatch, 120)
        assert all(c["incumbentRecord"] is None for c in data["senateRaces"][0]["candidates"])

    def test_an_fec_duplicate_of_the_member_going_in_is_not_a_namesake(self, db_session, monkeypatch):
        """FEC's duplicate record of the same person (same money, same
        surname) is merged into one card, and must not suppress its link."""
        _race(db_session, "2026-HOUSE-GA-6", "GA", office="H", district=6)
        _candidate(db_session, "H1", "2026-HOUSE-GA-6", "MCBATH, LUCY", incumbent_challenge="I",
                   contributions=100.0, cash_on_hand=50.0, confirmed_general=True)
        _candidate(db_session, "H1B", "2026-HOUSE-GA-6", "MCBATH, LUCY K", incumbent_challenge="I",
                   contributions=100.0, cash_on_hand=50.0)
        _representative(db_session, "R-MCBATH", "Lucy McBath", "GA", 6)
        candidates = self._on_jan_3(db_session, monkeypatch, 120)["houseRaces"][0]["candidates"]
        assert [c["id"] for c in candidates] == ["H1"]
        assert candidates[0]["incumbentRecord"]["id"] == "R-MCBATH"

    def test_a_namesake_who_is_not_a_confirmed_nominee_still_counts(self, db_session, monkeypatch):
        """The roster can hold any winner the page doesn't show as a
        confirmed nominee, so the check covers the whole deduplicated field."""
        _race(db_session, "2026-HOUSE-GA-6", "GA", office="H", district=6)
        _candidate(db_session, "H1", "2026-HOUSE-GA-6", "SMITH, PAT", incumbent_challenge="I", confirmed_general=True)
        _candidate(db_session, "H2", "2026-HOUSE-GA-6", "SMITH, DANA", incumbent_challenge="C", party="REP")
        _representative(db_session, "R-DANA", "Dana Smith", "GA", 6, party="R")
        data = self._on_jan_3(db_session, monkeypatch, 120)
        assert all(c["incumbentRecord"] is None for c in data["houseRaces"][0]["candidates"])

    @pytest.mark.parametrize("congress", [119, 120])
    @pytest.mark.parametrize("namesake_elsewhere", [False, True])
    def test_a_redrawn_states_seat_number_links_only_a_unique_surname_in_the_delegation(
        self, db_session, monkeypatch, congress, namesake_elsewhere,
    ):
        """On a redrawn map the race's number names a different district
        than the member holding that number was elected in. Pat Smith
        (holding UT-1) runs in the new UT-3, whose holder going in is Dana
        Smith: the direct hit on UT-3 is Dana, not Pat, and must not link —
        while the members going in sit and after the 120th sits on a roster
        not yet refreshed. Without a namesake in the delegation the hit is
        the candidate's own seat and still links."""
        _race(db_session, "2026-HOUSE-UT-3", "UT", office="H", district=3)
        _candidate(db_session, "H1", "2026-HOUSE-UT-3", "SMITH, PAT", incumbent_challenge="I")
        if namesake_elsewhere:
            _representative(db_session, "R-PAT", "Pat Smith", "UT", 1)
            _representative(db_session, "R-DANA", "Dana Smith", "UT", 3)
        else:
            _representative(db_session, "R-PAT", "Pat Smith", "UT", 3)
        record = self._on_jan_3(db_session, monkeypatch, congress)["houseRaces"][0]["candidates"][0]["incumbentRecord"]
        if namesake_elsewhere:
            assert record is None
        else:
            assert record["id"] == "R-PAT"

    def test_a_redrawn_states_namesake_blocks_the_seat_number_during_the_campaign(self, db_session):
        """The same as above on an ordinary campaign day (the 119th sits)."""
        _race(db_session, "2026-HOUSE-UT-3", "UT", office="H", district=3)
        _candidate(db_session, "H1", "2026-HOUSE-UT-3", "SMITH, PAT", incumbent_challenge="I")
        _representative(db_session, "R-PAT", "Pat Smith", "UT", 1)
        _representative(db_session, "R-DANA", "Dana Smith", "UT", 3)
        db_session.commit()
        data = _body(elections.state_ballot("UT", db_session))
        assert data["houseRaces"][0]["candidates"][0]["incumbentRecord"] is None

    @pytest.mark.parametrize("congress,linked", [(119, True), (120, False)])
    def test_a_namesake_in_the_states_other_senate_race_blocks_the_link_once_the_120th_sits(
        self, db_session, monkeypatch, congress, linked,
    ):
        """A regular and a special Senate race in one state and cycle: the
        senators are matched across the whole state, so the special's
        winner sharing the regular incumbent's surname can be the row a
        refreshed roster holds. The namesake check spans both races."""
        _race(db_session, "2026-SEN-GA", "GA")
        db_session.add(Race(id="2026-SEN-GA-SPECIAL", cycle_year=2026, office="S", state="GA", is_special=True))
        _candidate(db_session, "S1", "2026-SEN-GA", "BRENNAN, JON", incumbent_challenge="I")
        _candidate(db_session, "S2", "2026-SEN-GA-SPECIAL", "BRENNAN, DANA", incumbent_challenge="O", party="REP")
        if linked:
            _senator(db_session, "SEN-JON", "Jon Brennan", "GA")
        else:
            _senator(db_session, "SEN-DANA", "Dana Brennan", "GA", party="R")
        data = self._on_jan_3(db_session, monkeypatch, congress)
        records = [c["incumbentRecord"] for r in data["senateRaces"] for c in r["candidates"]]
        if linked:
            assert [rec["id"] for rec in records if rec] == ["SEN-JON"]
        else:
            assert records == [None, None]


class TestStaleIncumbentFlag:
    """Real MI 2026 Senate shape, live-verified 2026-09: Sen. Gary Peters
    stayed FEC-coded incumbent_challenge="I" months after announcing he
    would not seek re-election, while every other filer in the race
    correctly synced to "O" (open seat) — FEC has no "declined to run"
    status code, so this O-vs-I mix is the only usable signal."""

    def test_incumbent_flag_is_dropped_when_race_mixes_open_and_incumbent(self, db_session):
        _race(db_session, "2026-SEN-MI", "MI")
        _candidate(
            db_session, "PETERS", "2026-SEN-MI", "PETERS, GARY",
            incumbent_challenge="I", cash_on_hand=6_546_332, contributions=6_978_978,
        )
        _candidate(
            db_session, "ROGERS", "2026-SEN-MI", "ROGERS, MICHAEL J", party="REP",
            incumbent_challenge="O", cash_on_hand=4_473_237, contributions=7_681_046,
        )
        _candidate(
            db_session, "ELSAYED", "2026-SEN-MI", "EL-SAYED, ABDUL",
            incumbent_challenge="O", cash_on_hand=2_552_763, contributions=14_479_903,
        )
        _senator(db_session, "SEN-PETERS", "Gary Peters", "MI")
        db_session.commit()

        data = _body(elections.state_ballot("MI", db_session))
        by_id = {c["id"]: c for c in data["senateRaces"][0]["candidates"]}
        assert by_id["PETERS"]["incumbentChallenge"] is None
        assert by_id["PETERS"]["incumbentRecord"] is None
        assert by_id["ROGERS"]["incumbentChallenge"] == "O"
        assert by_id["ELSAYED"]["incumbentChallenge"] == "O"

    def test_a_genuine_defended_seat_keeps_its_incumbent_flag(self, db_session):
        """The common, correct shape (one "I", the rest "C", no "O" at
        all) must never be touched by this guard."""
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "BRENNAN", "2026-SEN-GA", "BRENNAN, JON", incumbent_challenge="I")
        _candidate(db_session, "CHALLENGER", "2026-SEN-GA", "CHALLENGER, PAT", party="REP", incumbent_challenge="C")
        _senator(db_session, "SEN-BRENNAN", "Jon Brennan", "GA")
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        by_id = {c["id"]: c for c in data["senateRaces"][0]["candidates"]}
        assert by_id["BRENNAN"]["incumbentChallenge"] == "I"
        assert by_id["BRENNAN"]["incumbentRecord"]["id"] == "SEN-BRENNAN"
        assert by_id["CHALLENGER"]["incumbentChallenge"] == "C"


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
        race_ids — the reader must not see the same headline twice.

        The two rows share the same published_at/fetched_at (same article,
        same ingest pass), so which race's badge wins must not depend on
        undefined SQL tie-break order — the `.id` tiebreaker makes it
        repeatable across calls rather than however the DB happens to
        return tied rows."""
        _race(db_session, "2026-SEN-GA", "GA", office="S")
        _race(db_session, "2026-HOUSE-GA-6", "GA", office="H", district=6)
        _coverage(db_session, "2026-SEN-GA", "https://apnews.com/both-races")
        _coverage(db_session, "2026-HOUSE-GA-6", "https://apnews.com/both-races")
        db_session.commit()

        responses = [_body(elections.state_ballot("GA", db_session)) for _ in range(5)]
        assert all(len(data["coverage"]) == 1 for data in responses)
        assert len({data["coverage"][0]["race"]["id"] for data in responses}) == 1

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
    def _election_day(db):
        return elections.active_election(db).election_day.isoformat()

    def test_measures_are_included_verbatim(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        db_session.add(BallotMeasure(
            id="ga-measure-1", state="GA", election_date=self._election_day(db_session),
            number="Amendment 1", title="Property tax exemption",
            official_title="An act relating to property tax exemptions.",
            source_name="Georgia Secretary of State",
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
            state="GA", election_date=self._election_day(db_session),
            status=MeasureCoverage.CONFIRMED_NONE, source_name="Georgia Secretary of State",
        ))
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        assert data["measures"] == []
        assert data["measureCoverage"]["status"] == MeasureCoverage.CONFIRMED_NONE
        assert data["measureCoverage"]["sourceName"] == "Georgia Secretary of State"

    def test_official_lookup_and_omits_are_always_present(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        db_session.commit()

        data = _body(elections.state_ballot("GA", db_session))
        assert data["officialLookup"]["url"]
        assert isinstance(data["omits"], list) and len(data["omits"]) > 0

    def test_dc_is_a_valid_ballot_jurisdiction_despite_no_federal_race(self, db_session):
        """DC has no voting House/Senate race and is absent from
        election_calendar.federal_states(), but it does vote on statewide
        initiatives — the ballot page must not 404 it."""
        data = _body(elections.state_ballot("DC", db_session))
        assert data["senateRaces"] == []
        assert data["houseRaces"] == []
        assert any("Delegate" in item for item in data["omits"])

    def test_a_territory_with_no_ballot_jurisdiction_still_404s(self, db_session):
        with pytest.raises(HTTPException) as exc_info:
            elections.state_ballot("GU", db_session)
        assert exc_info.value.status_code == 404


class TestJudicialOmitShrinks:
    """`omits` has to describe the page as it actually is.

    A disclaimer list that keeps disclaiming what the page now shows
    stops being a description and becomes boilerplate a reader learns to
    skip — the same rule that already governs the executive and
    legislative lines.
    """

    @staticmethod
    def _sync_judicial(db, state="NC", court="district", district="3", seat="2",
                       party="R", name="Lloyd Williams"):
        """Through the real sync, which writes the row AND the coverage
        marker — inserting a bare row would leave the page reporting
        not_yet_covered, which is correct but not what this tests."""
        from app.pipeline.fetch.state_candidates import _sync_judicial_nominees
        _sync_judicial_nominees(
            db, 2026, state,
            {"strategy": "tabular", "source_name": "NC State Board of Elections",
             "judicial_offices": True},
            [{"office": court, "district": district, "seat": seat,
              "party": party, "last_name": name}],
        )

    def test_uncovered_state_names_contests_and_retention_together(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        db_session.commit()
        data = _body(elections.state_ballot("GA", db_session))
        assert "Judicial contests and retention questions" in data["omits"]
        assert data["judicialRaces"] == []
        # Unchecked, not checked-and-empty (see TestJudicialConfirmedNone).
        assert data["judicialCoverage"]["status"] == "not_yet_covered"

    def test_covered_state_still_declares_retention_questions(self, db_session):
        """The line SHRINKS rather than disappearing: retention questions
        are a separate yes/no ballot item, not a contest between
        candidates, and nothing reads them yet. Claiming judicial is
        covered while they are not would be the honest half-statement
        this list exists to avoid."""
        _race(db_session, "2026-SEN-NC", "NC")
        db_session.commit()
        self._sync_judicial(db_session)

        data = _body(elections.state_ballot("NC", db_session))
        assert data["judicialRaces"], "the section should render"
        assert "Judicial retention questions" in data["omits"]
        assert "Judicial contests and retention questions" not in data["omits"]

    def test_the_other_omissions_are_untouched(self, db_session):
        _race(db_session, "2026-SEN-NC", "NC")
        db_session.commit()
        self._sync_judicial(db_session)
        data = _body(elections.state_ballot("NC", db_session))
        for still_omitted in ("County and municipal offices",
                              "Local ballot measures",
                              "Primary and runoff ballots"):
            assert still_omitted in data["omits"]


class TestJudicialConfirmedNone:
    """A state whose judicial seats were ALL decided in its primary.

    Idaho is the live case: its three matched contests were unopposed,
    so all three were elected in May under Idaho Code 34-1217 and none
    is on the November ballot. That is a CHECKED answer, and the page
    has to say so rather than render the same empty space a state
    nobody has looked at gets.
    """

    @staticmethod
    def _sync_empty(db, state="ID"):
        from app.pipeline.fetch.state_candidates import _sync_judicial_nominees
        _sync_judicial_nominees(
            db, 2026, state,
            {"strategy": "tabular", "source_name": "Idaho Secretary of State",
             "judicial_offices": True, "judicial_resolution": "elects"},
            [],
        )

    def test_checked_and_empty_is_not_the_same_as_unchecked(self, db_session):
        _race(db_session, "2026-SEN-ID", "ID")
        db_session.commit()
        self._sync_empty(db_session)

        data = _body(elections.state_ballot("ID", db_session))
        assert data["judicialRaces"] == []
        assert data["judicialCoverage"]["status"] == "confirmed_none"
        assert data["judicialCoverage"]["sourceName"] == "Idaho Secretary of State"
        # And the omission shrinks, because judicial contests ARE now
        # accounted for — the answer is simply that none are on the ballot.
        assert "Judicial retention questions" in data["omits"]
        assert "Judicial contests and retention questions" not in data["omits"]
        # The unchecked counterpart (GA, full omission kept) is
        # TestJudicialOmitShrinks.test_uncovered_state_names_contests_and_retention_together.


def test_a_redrawn_states_house_race_takes_the_new_lines_lean(db_session):
    """district_pvi.json's sitting (119th) table describes today's seats;
    on Utah's 2026 map UT-1 is a different district (R+10 on the old lines,
    D+12 on the new). The race carries the new lines' number from the
    120th Congress's pinned table, flagged as a district figure — never
    the old seat's, and no longer the statewide stand-in."""
    from app.pipeline.analyze import score_calculator

    _race(db_session, "2026-HOUSE-UT-1", "UT", office="H", district=1)
    db_session.commit()
    house = _body(elections.state_ballot("UT", db_session))["houseRaces"][0]
    assert (house["pvi"], house["pviLevel"]) == (-12, "district")
    assert score_calculator.get_district_pvi_map()["UT-1"] == 10


def test_a_redrawn_state_with_no_table_for_its_lines_takes_the_flagged_statewide_lean():
    """When the table served is older than the redraw, district_pvi_for_congress
    drops the redrawn state, and _pvi_for_race falls back to the statewide
    number, flagged — never an old-map district's value."""
    from app.api.elections import _pvi_for_race
    from app.pipeline.fetch.district_pvi import _district_pvi_for_congress

    data = {"congresses": {"119": {"districts": {"UT-1": 10, "GA-1": 9}}}}
    sources = {"120": {"redrawn_states": ["UT"]}}
    table, meta = _district_pvi_for_congress(120, data, sources)
    assert meta["omittedRedrawnStates"] == ["UT"]
    race = Race(id="2026-HOUSE-UT-1", cycle_year=2026, office="H", state="UT", district=1)
    assert _pvi_for_race(race, {"UT": 11}, table) == (11, "state")
    kept = Race(id="2026-HOUSE-GA-1", cycle_year=2026, office="H", state="GA", district=1)
    assert _pvi_for_race(kept, {"GA": 3}, table) == (9, "district")


def test_redrawn_maps_and_pinned_pvi_sources_name_the_same_states():
    """Two data files say which states redrew for 2026: the map/crosswalk
    list (redrawn_congressional_maps.json, read by redrawn_states) and the
    pinned-PVI sources (district_pvi_sources.json). If they disagree, the
    page would call a district new while serving an old-map lean, or the
    reverse."""
    from app.live_results.sync import redrawn_states
    from app.pipeline.fetch.district_pvi import congress_for_election, load_sources

    pinned = load_sources()["congresses"][str(congress_for_election(2026))]
    assert redrawn_states(2026) == set(pinned["redrawn_states"])
