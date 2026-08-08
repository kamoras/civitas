"""Tests for GET /elections/states/{state} — the ballot-centric per-state
view. Same direct-router-call convention as test_elections_api.py.
"""

import json

import pytest
from fastapi import HTTPException

from app.api import elections
from app.models import Candidate, Race, Representative, Senator


def _body(response):
    return json.loads(response.body)


def _race(db, race_id, state, office="S", district=None, cycle_year=2026):
    r = Race(id=race_id, cycle_year=cycle_year, office=office, state=state, district=district)
    db.add(r)
    return r


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
    must say "no Senate race", not error or fabricate one."""
    _race(db_session, "2026-HOUSE-GA-6", "GA", office="H", district=6)
    db_session.commit()

    data = _body(elections.state_ballot("GA", db_session))
    assert data["senateRaces"] == []
    assert len(data["houseRaces"]) == 1


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
