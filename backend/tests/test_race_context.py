"""What-shapes-this-race figures: funding composition, lean placement, CSV."""

import csv
import io
import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import elections
from app.models import Candidate, Race
from app.race_context import (
    CSV_COLUMNS, FUNDING_BLOCKS, apportion, funding_sources, lean_context, races_csv,
)


@pytest.fixture(autouse=True)
def _in_the_2026_campaign(monkeypatch, freeze_utcnow):
    monkeypatch.setattr("app.election_phase.election_today", lambda: date(2026, 10, 1))
    freeze_utcnow(datetime(2026, 9, 30, 12, 0))


def _filed(**overrides):
    fields = dict(
        contributions=1000.0, individual_unitemized_contributions=150.0,
        individual_itemized_contributions=450.0, other_committee_contributions=300.0,
        party_committee_contributions=0.0, candidate_contribution=100.0,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


class TestApportion:
    def test_adds_up_exactly(self):
        for values in ([1, 1, 1], [0.5, 0.3, 0.2], [7, 0, 0, 3], [1e6, 1, 1]):
            assert sum(apportion(values, 20)) == 20
            assert sum(apportion(values, 100)) == 100

    def test_largest_remainder_and_ties_to_the_earlier(self):
        assert apportion([1, 1, 1], 100) == [34, 33, 33]
        assert apportion([2, 1], 20) == [13, 7]

    def test_nothing_to_split(self):
        assert apportion([0, 0], 20) == [0, 0]


class TestFundingSources:
    def test_parts_in_the_fecs_categories_with_shares_and_blocks(self):
        out = funding_sources(_filed())
        assert out["addsUp"] is True
        assert [p["key"] for p in out["parts"]] == [
            "smallDonors", "largerDonors", "committees", "party", "candidate"]
        assert [p["percent"] for p in out["parts"]] == [15, 45, 30, 0, 10]
        assert sum(p["blocks"] for p in out["parts"]) == FUNDING_BLOCKS
        assert [p["amount"] for p in out["parts"]] == [150.0, 450.0, 300.0, 0.0, 100.0]

    def test_not_read_yet_is_none_not_zero(self):
        assert funding_sources(_filed(individual_unitemized_contributions=None)) is None
        assert funding_sources(_filed(contributions=None)) is None
        assert funding_sources(_filed(contributions=0.0)) is None

    def test_categories_that_dont_make_the_total_get_no_shares(self):
        out = funding_sources(_filed(contributions=5000.0))
        assert out["addsUp"] is False
        assert all(p["percent"] is None and p["blocks"] is None for p in out["parts"])
        assert out["parts"][0]["amount"] == 150.0

    def test_a_negative_category_gets_no_shares(self):
        out = funding_sources(_filed(contributions=900.0, candidate_contribution=-100.0,
                                     other_committee_contributions=400.0))
        assert out["addsUp"] is False

    def test_rounding_within_a_dollar_still_adds_up(self):
        assert funding_sources(_filed(contributions=1000.6))["addsUp"] is True


class TestLeanContext:
    STATES = {"AA": -10, "BB": 0, "CC": 5, "DC": -40}
    DISTRICTS = {"AA-1": -3, "AA-2": 2, "BB-0": 2, "CC-1": 9}

    def test_house_district_among_districts(self):
        out = lean_context("H", 2, "district", self.STATES, self.DISTRICTS)
        assert out == {
            "unit": "districts", "of": 4, "moreDemocratic": 1, "moreRepublican": 1, "same": 1,
            "seatLean": 2,
            "distribution": [
                {"lean": -3, "label": "D+3", "count": 1},
                {"lean": 2, "label": "R+2", "count": 2},
                {"lean": 9, "label": "R+9", "count": 1},
            ],
        }

    def test_senate_among_the_states_only(self, monkeypatch):
        monkeypatch.setattr("app.race_context.federal_states", lambda: frozenset({"AA", "BB", "CC"}))
        out = lean_context("S", 0, "state", self.STATES, self.DISTRICTS)
        assert out == {
            "unit": "states", "of": 3, "moreDemocratic": 1, "moreRepublican": 1, "same": 0,
            "seatLean": 0,
            "distribution": [
                {"lean": -10, "label": "D+10", "count": 1},
                {"lean": 0, "label": "EVEN", "count": 1},
                {"lean": 5, "label": "R+5", "count": 1},
            ],
        }

    def test_a_statewide_stand_in_is_not_ranked_among_districts(self):
        assert lean_context("H", 5, "state", self.STATES, self.DISTRICTS) is None

    def test_no_lean(self):
        assert lean_context("S", None, None, self.STATES, self.DISTRICTS) is None


def _seed(db):
    db.add(Race(id="2026-HOUSE-CT-1", cycle_year=2026, office="H", state="CT", district=1))
    db.add(Candidate(
        id="H6CT99901", race_id="2026-HOUSE-CT-1", name="EXAMPLE, ONE", party="DEM",
        candidate_status="C", has_raised_funds=True, financials_through="2026-06-30",
        last_financials_sync=datetime(2026, 9, 1, 3, 0),
        candidate_loans=50.0, **vars(_filed()),
    ))
    db.add(Candidate(id="H6CT99902", race_id="2026-HOUSE-CT-1", name="EXAMPLE, TWO", party="REP",
                     candidate_status="C"))
    db.commit()


class TestRoutes:
    def test_race_detail_carries_the_new_figures(self, db_session):
        _seed(db_session)
        body = json.loads(elections.race_detail("2026-HOUSE-CT-1", db_session).body)
        filed, unsynced = body["candidates"]
        assert filed["fundingSources"]["parts"][0] == {
            "key": "smallDonors", "amount": 150.0, "percent": 15, "blocks": 3}
        assert filed["candidateLoans"] == 50.0
        assert unsynced["fundingSources"] is None
        assert body["leanContext"]["unit"] == "districts"
        assert body["leanContext"]["of"] >= 400

    def test_state_csv_has_every_listed_candidate_and_their_dates(self, db_session):
        _seed(db_session)
        response = elections.state_races_csv("ct", db_session)
        assert response.media_type.startswith("text/csv")
        assert "attachment" in response.headers["content-disposition"]
        rows = list(csv.DictReader(io.StringIO(response.body.decode())))
        assert [r["candidate_id"] for r in rows] == ["H6CT99901", "H6CT99902"]
        first = rows[0]
        assert first["individual_unitemized_contributions"] == "150.0"
        assert first["financials_through"] == "2026-06-30"
        assert first["last_financials_sync"] == "2026-09-01T03:00:00Z"
        assert first["fec_url"].endswith("/H6CT99901/?cycle=2026&election_full=true")
        # Not read yet stays empty, never 0.
        assert rows[1]["contributions"] == ""

    def test_unknown_state(self, db_session):
        with pytest.raises(HTTPException):
            elections.state_races_csv("ZZ", db_session)

    def test_columns_are_the_header(self):
        assert races_csv([]).strip() == ",".join(CSV_COLUMNS)
