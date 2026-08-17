"""Tests for the /api/elections/* endpoints — calls router functions
directly with db_session (same convention as test_action_api.py), since
no TestClient+dependency-override harness exists in this test suite yet.
"""

import json
from datetime import datetime

import pytest
from fastapi import HTTPException

from app.api import elections
from app.models import Candidate, Race, RaceCoverageItem


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


class TestListRaces:
    def test_returns_race_with_pvi_and_top_candidates(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S1", "2026-SEN-GA", "OSSOFF, JON", cash_on_hand=500.0)
        _candidate(db_session, "S2", "2026-SEN-GA", "COLLINS, JANE", cash_on_hand=200.0)
        db_session.commit()

        data = _body(elections.list_races(db_session))
        assert len(data) == 1
        race = data[0]
        assert race["id"] == "2026-SEN-GA"
        assert race["candidateCount"] == 2
        assert race["topCandidates"][0]["id"] == "S1"  # higher cash_on_hand first
        assert isinstance(race["pvi"], int)  # GA has a real PVI entry

    def test_house_race_uses_district_pvi_not_state_pvi(self, db_session):
        _race(db_session, "2026-HOUSE-CA-12", "CA", office="H", district=12)
        db_session.commit()

        data = _body(elections.list_races(db_session))
        assert data[0]["pvi"] == elections.get_district_pvi_map()["CA-12"]
        # The provenance flag tells the frontend which map the number came
        # from — a district figure, not the statewide fallback.
        assert data[0]["pviLevel"] == "district"

    def test_senate_race_pvi_is_flagged_as_state_level(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        db_session.commit()

        data = _body(elections.list_races(db_session))
        assert data[0]["pviLevel"] == "state"

    def test_only_current_cycle_races_returned(self, db_session):
        """Contract of /races: the CURRENT cycle only — load-bearing the
        day a second cycle's roster syncs into the same table."""
        _race(db_session, "2026-SEN-GA", "GA")
        _race(db_session, "2028-SEN-AZ", "AZ", cycle_year=2028)
        db_session.commit()

        data = _body(elections.list_races(db_session))
        assert [r["id"] for r in data] == ["2026-SEN-GA"]

    def test_candidate_summary_exposes_status_and_sync_watermark(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(
            db_session, "S1", "2026-SEN-GA", "OSSOFF, JON",
            candidate_status="C", cash_on_hand=500.0,
            last_financials_sync=datetime(2026, 7, 20, 8, 30),
        )
        # Never-synced challenger: null watermark, not a fabricated time —
        # the frontend renders "awaiting FEC sync" instead of "$0 raised".
        _candidate(db_session, "S2", "2026-SEN-GA", "COLLINS, JANE")
        db_session.commit()

        data = _body(elections.list_races(db_session))
        synced, unsynced = data[0]["topCandidates"]
        assert synced["candidateStatus"] == "C"
        # Stored naive UTC must serialize with an explicit Z so JS Date
        # doesn't parse it as viewer-local time.
        assert synced["lastFinancialsSync"] == "2026-07-20T08:30:00Z"
        assert unsynced["lastFinancialsSync"] is None

    def test_confirmed_candidates_filter_out_defeated_primary_fec_filers(self, db_session):
        """Same filtering as the ballot page and race-detail route — a
        defeated-primary FEC filer shouldn't count toward candidateCount
        or edge out the real nominee for a topCandidates slot just
        because they raised more before losing."""
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "WINNER", "2026-SEN-GA", "PAXTON, KEN", confirmed_general=True, cash_on_hand=1.0)
        _candidate(db_session, "LOSER", "2026-SEN-GA", "CORNYN, JOHN", confirmed_general=False, cash_on_hand=999.0)
        db_session.commit()

        data = _body(elections.list_races(db_session))
        assert data[0]["candidateCount"] == 1
        assert [c["id"] for c in data[0]["topCandidates"]] == ["WINNER"]

    def test_primary_ballot_filters_when_no_nominee_is_confirmed_yet(self, db_session):
        """The months BEFORE a primary: nobody is a nominee yet, so the
        best available answer is who the state says is actually on its
        primary ballot — an FEC filer who never filed with the state is
        not a ballot option either."""
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "FILED", "2026-SEN-GA", "OSSOFF, JON",
                   on_primary_ballot=True, cash_on_hand=1.0)
        _candidate(db_session, "PAPER", "2026-SEN-GA", "NOBODY, A",
                   on_primary_ballot=False, cash_on_hand=999.0)
        db_session.commit()

        data = _body(elections.list_races(db_session))
        assert data[0]["candidateCount"] == 1
        assert [c["id"] for c in data[0]["topCandidates"]] == ["FILED"]

    def test_a_confirmed_nominee_outranks_the_primary_ballot(self, db_session):
        """Being on a primary ballot says nothing about surviving it, so
        once a state confirms nominees those win outright — including over
        someone who was on the primary ballot and lost."""
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "NOMINEE", "2026-SEN-GA", "PAXTON, KEN",
                   confirmed_general=True, on_primary_ballot=True, cash_on_hand=1.0)
        _candidate(db_session, "BEATEN", "2026-SEN-GA", "CORNYN, JOHN",
                   confirmed_general=False, on_primary_ballot=True, cash_on_hand=999.0)
        db_session.commit()

        data = _body(elections.list_races(db_session))
        assert [c["id"] for c in data[0]["topCandidates"]] == ["NOMINEE"]


class TestRaceDetail:
    def test_404_for_unknown_race(self, db_session):
        with pytest.raises(HTTPException) as exc_info:
            elections.race_detail("nonexistent", db_session)
        assert exc_info.value.status_code == 404

    def test_returns_candidates_and_coverage(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S1", "2026-SEN-GA", "OSSOFF, JON")
        db_session.add(RaceCoverageItem(
            race_id="2026-SEN-GA", source_type="news", source_name="AP News",
            title="Ossoff leads", url="https://apnews.com/a1", summary="Tight race.",
            published_at=datetime(2026, 7, 19, 14, 0),
        ))
        db_session.commit()

        data = _body(elections.race_detail("2026-SEN-GA", db_session))
        assert data["id"] == "2026-SEN-GA"
        assert data["pviLevel"] == "state"
        assert len(data["candidates"]) == 1
        assert len(data["coverage"]) == 1
        assert data["coverage"][0]["summary"] == "Tight race."  # verbatim
        # Explicit Z suffix — see _iso_utc (naive ISO parses as local time in JS).
        assert data["coverage"][0]["publishedAt"] == "2026-07-19T14:00:00Z"

    def test_confirmed_candidates_filter_out_defeated_primary_fec_filers(self, db_session):
        """Same real bug as test_elections_state_ballot's version of this
        test, but for the direct race-detail route: BallotRaceOptions
        links every ballot row to /elections/{race.id}, which calls this
        function — a candidate list unfiltered here would let the exact
        19-candidates bug resurface one click after the state-ballot page
        fixed it."""
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "WINNER", "2026-SEN-GA", "PAXTON, KEN", confirmed_general=True)
        _candidate(db_session, "LOSER", "2026-SEN-GA", "CORNYN, JOHN", confirmed_general=False)
        db_session.commit()

        data = _body(elections.race_detail("2026-SEN-GA", db_session))
        assert [c["id"] for c in data["candidates"]] == ["WINNER"]


class TestCandidateDetail:
    def test_404_for_unknown_candidate(self, db_session):
        with pytest.raises(HTTPException) as exc_info:
            elections.candidate_detail("nonexistent", db_session)
        assert exc_info.value.status_code == 404

    def test_returns_candidate_with_parent_race(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S1", "2026-SEN-GA", "OSSOFF, JON", cash_on_hand=500.0)
        db_session.commit()

        data = _body(elections.candidate_detail("S1", db_session))
        assert data["id"] == "S1"
        assert data["cashOnHand"] == 500.0
        assert data["race"]["id"] == "2026-SEN-GA"


class TestPviMap:
    def test_returns_both_state_and_district_maps(self):
        data = _body(elections.pvi_map())
        assert "AK" in data["states"]
        assert "AK-0" in data["districts"]

    def test_includes_provenance_metadata(self):
        """The bare numbers over-claim without provenance (2026-07 review
        F7) — the payload must carry per-map source metadata plus the
        lean-is-not-a-forecast note for the frontend to label."""
        data = _body(elections.pvi_map())
        meta = data["meta"]
        assert "states" in meta
        assert "districts" in meta
        assert "not" in meta["note"]  # the "measures lean, not who will win" caveat

    def test_includes_cycle_year(self):
        """Lets /elections's directory page get its header year from the
        same fetch it already makes for map coloring, instead of a
        second fetch of every race."""
        from app.pipeline.election_pipeline import current_election_cycle

        data = _body(elections.pvi_map())
        assert data["cycleYear"] == current_election_cycle()
