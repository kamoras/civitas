"""Odd-year state elections (state_ballot_scope.odd_year_elections) and the
redistricting watch (redistricting.watch), read from their checked-in files."""

import json
import re
from datetime import date, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.redistricting import MAP_KINDS, watch
from app.state_ballot_scope import _states, odd_year_elections

_MAPS = json.loads((Path(__file__).resolve().parent.parent / "app" / "data"
                    / "redrawn_congressional_maps.json").read_text())["cycles"]


class TestOddYear:
    def test_even_years_have_none(self):
        assert odd_year_elections(2028) == []

    def test_each_state_listed_follows_its_own_cycle(self):
        for year in (2025, 2027, 2029):
            rows = odd_year_elections(year)
            assert rows, year
            for row in rows:
                entry = _states()[row["state"]]
                for kind in row["kinds"]:
                    cycle = entry[kind]
                    assert (year - cycle["from"]) % cycle["every"] == 0
                assert len(row["words"]) == len(row["kinds"])

    def test_alternating_states_differ_by_year(self):
        # A four-year statewide cycle and a two-year legislative one: the
        # two odd years must not list the same contests.
        assert {(r["state"], tuple(r["kinds"])) for r in odd_year_elections(2025)} != {
            (r["state"], tuple(r["kinds"])) for r in odd_year_elections(2027)}

    def test_endpoint(self, monkeypatch):
        monkeypatch.setattr("app.api.special_elections.election_today", lambda: date(2026, 12, 1))
        body = TestClient(app).get("/api/elections/odd-year").json()
        assert body["year"] == 2027 and body["states"]
        assert all(s["office"]["url"].startswith("https://") for s in body["states"])


class TestWatch:
    def test_every_entry_says_how_and_when_it_was_settled(self):
        for cycle, states in _MAPS.items():
            for st, entry in states.items():
                assert entry["kind"] in MAP_KINDS, (cycle, st)
                assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["settled"])
                # The date is the one the cited reason or source names.
                assert entry["settled"] in entry["reason"] + entry["source"]["title"], (cycle, st)

    def test_an_unconfirmed_next_cycle_keeps_the_maps_in_force(self):
        newest = max(int(c) for c in _MAPS)
        w = watch(newest + 2)
        assert w["confirmed"] is False and w["inForceCycle"] == newest
        assert len(w["maps"]) == len(_MAPS[str(newest)])
        assert w["lastSettled"] == max(e["settled"] for e in _MAPS[str(newest)].values())
        assert [m["settled"] for m in w["maps"]] == sorted((m["settled"] for m in w["maps"]), reverse=True)

    def test_a_listed_cycle_is_confirmed(self):
        newest = max(int(c) for c in _MAPS)
        assert watch(newest)["confirmed"] is True

    def test_index_carries_it(self, freeze_utcnow):
        freeze_utcnow(datetime(2027, 2, 1, 12))
        body = TestClient(app).get("/api/elections/redistricting").json()
        assert body["watch"]["nextCycle"] == 2028 and body["watch"]["maps"]
