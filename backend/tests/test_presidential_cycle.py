"""The presidential contest: electoral votes from the apportionment, the
national race the roster sync keeps the FEC's statutory candidates in, the
endpoint, and the EAC survey's election administration figures."""

import importlib.util
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app import electoral_votes as ev
from app.database import get_db
from app.election_administration import RATES, administration
from app.main import app
from app.models import Race
from app.pipeline import election_pipeline

_spec = importlib.util.spec_from_file_location(
    "fetch_eavs", Path(__file__).resolve().parent.parent / "scripts" / "fetch_eavs.py")
fetch_eavs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch_eavs)
aggregate, newest_release = fetch_eavs.aggregate, fetch_eavs.newest_release


def _client(db):
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


class TestElectoralVotes:
    def test_seats_plus_two_and_three_for_dc(self):
        votes = ev.electoral_votes()
        assert votes["DC"] == 3 and len(votes) == 51
        assert all(n >= 3 for n in votes.values())
        s = ev.summary()
        # 435 seats + 100 senators + DC's 3.
        assert s["total"] == 538 and s["toWin"] == 270
        assert s["states"][0]["votes"] == max(votes.values())

    def test_only_every_fourth_year(self):
        assert [y for y in range(2025, 2034) if ev.is_presidential(y)] == [2028, 2032]


class TestRoster:
    def _raw(self, **over):
        raw = dict(candidate_id="P80000001", state="US", office="P", name="DOE, JANE", party="DEM",
                   incumbent_challenge="O", has_raised_funds=True, candidate_election_year=2028,
                   candidate_status="C")
        raw.update(over)
        return raw

    def test_statutory_filers_join_one_national_race(self, db_session):
        assert election_pipeline._sync_roster(db_session, 2028, [
            self._raw(), self._raw(candidate_id="P80000002", state="TX"),
            # A paper filing, under the FEC's statutory threshold.
            self._raw(candidate_id="P80000003", candidate_status="N"),
        ]) == 2
        race = db_session.get(Race, "2028-PRES")
        assert (race.office, race.state, race.district) == ("P", "US", None)
        assert sorted(c.id for c in race.candidates) == ["P80000001", "P80000002"]

    def test_the_race_list_stays_senate_and_house(self, db_session, freeze_utcnow):
        freeze_utcnow(datetime(2028, 2, 1, 12))
        election_pipeline._sync_roster(db_session, 2028, [self._raw()])
        try:
            races = _client(db_session).get("/api/elections/races").json()
        finally:
            app.dependency_overrides.clear()
        assert all(r["office"] in ("S", "H") for r in races)


def test_presidential_endpoint(db_session, freeze_utcnow):
    freeze_utcnow(datetime(2028, 2, 1, 12))
    election_pipeline._sync_roster(db_session, 2028, [TestRoster()._raw()])
    try:
        body = _client(db_session).get("/api/elections/presidential").json()
    finally:
        app.dependency_overrides.clear()
    assert body["presidential"] is True and body["cycle"] == 2028
    assert body["registered"] == 1 and body["candidates"][0]["id"] == "P80000001"
    assert body["electoralVotes"]["total"] == 538


def test_a_midterm_cycle_says_when_the_next_presidential_is(db_session, freeze_utcnow):
    freeze_utcnow(datetime(2030, 2, 1, 12))
    try:
        body = _client(db_session).get("/api/elections/presidential").json()
    finally:
        app.dependency_overrides.clear()
    assert body["presidential"] is False and body["nextPresidential"] == 2032
    assert "candidates" not in body


class TestSurvey:
    def test_negative_codes_are_not_counts(self):
        states = aggregate([
            {"State_Abbr": "XX", "A1a": "100", "F1a": "-88"},
            {"State_Abbr": "XX", "A1a": "50", "F1a": "40"},
        ])
        assert states["XX"]["jurisdictions"] == 2
        assert states["XX"]["items"]["A1a"] == {"value": 150, "reporting": 2}
        assert states["XX"]["items"]["F1a"] == {"value": 40, "reporting": 1}

    def test_newest_release_on_the_page(self):
        html = ('<a href="/f/2022_EAVS_for_Public_Release_nolabel_V1_CSV.zip">'
                '<a href="/f/2024_EAVS_for_Public_Release_nolabel_V1_csv.zip">'
                '<a href="/f/2024_EAVS_for_Public_Release_nolabel_V2_csv.zip">')
        assert newest_release(html, "https://www.eac.gov/x") == (
            2024, 2, "https://www.eac.gov/f/2024_EAVS_for_Public_Release_nolabel_V2_csv.zip")

    def test_rates_only_over_complete_reporting(self):
        data = administration()
        assert data["of"] == 51 and {s["state"] for s in data["states"]} >= {"DC", "GA", "WY"}
        for s in data["states"]:
            for key, num, den in RATES:
                full = (s["items"][num]["reporting"] == s["jurisdictions"]
                        and s["items"][den]["reporting"] == s["jurisdictions"])
                assert (s["rates"][key] is not None) == (full and s["items"][den]["value"] > 0)
        assert 0 < data["national"]["turnout"]["rate"] < 1
