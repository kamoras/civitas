"""Tests for the /api/elections/* endpoints — calls router functions
directly with db_session (same convention as test_action_api.py), since
no TestClient+dependency-override harness exists in this test suite yet.
"""

import json

import pytest
from fastapi import HTTPException

from app.api import elections
from app.models import Candidate, Race, RaceCoverageItem


def _body(response):
    return json.loads(response.body)


def _race(db, race_id, state, office="S", district=None):
    r = Race(id=race_id, cycle_year=2026, office=office, state=state, district=district)
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
        ))
        db_session.commit()

        data = _body(elections.race_detail("2026-SEN-GA", db_session))
        assert data["id"] == "2026-SEN-GA"
        assert len(data["candidates"]) == 1
        assert len(data["coverage"]) == 1
        assert data["coverage"][0]["summary"] == "Tight race."  # verbatim


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
