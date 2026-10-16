"""The redistricting pages: scripts/build_redistricting_overlap.py, the file
it writes (app/data/redistricting_overlap.json), app/redistricting.py's
figures and the API that serves them."""

import importlib.util
import json
import pathlib
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import redistricting as rd
from app.api.redistricting import router
from app.main import app as main_app

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_SCRIPTS = _BACKEND / "scripts"
_DATA = _BACKEND / "app" / "data"


def _load(name: str):
    sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _load("build_redistricting_overlap")
cw = _load("build_county_district_crosswalk")

_BUNDLED = json.loads((_DATA / "redistricting_overlap.json").read_text())
_MAPS = json.loads((_DATA / "redrawn_congressional_maps.json").read_text())["cycles"]


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


# ── the script ──────────────────────────────────────────────────────────


def test_overlap_counts_every_resident_once():
    population = {"a": 10, "b": 5, "c": 7, "d": 3}
    old = [("a", "01"), ("b", "01"), ("c", "02"), ("d", "02"), ("empty", "01")]
    new = [("a", "02"), ("b", "01"), ("c", "02"), ("d", "02"), ("empty", "02")]
    cells, unmatched = script.overlap(population, old, new, "ZZ")
    assert cells == [["ZZ-1", "ZZ-2", 10], ["ZZ-2", "ZZ-2", 10], ["ZZ-1", "ZZ-1", 5]]
    assert unmatched == 0


def test_overlap_reports_residents_without_a_district():
    # A delegate-seat code, and a block one file lacks: never dropped quietly.
    cells, unmatched = script.overlap({"a": 4, "b": 6}, [("a", "98"), ("b", "01")], [("a", "01")], "ZZ")
    assert cells == [] and unmatched == 10


def test_the_bundled_file_covers_exactly_the_states_on_new_lines():
    on_cd120 = sorted(st for st, e in _MAPS[_BUNDLED["cycle"]].items() if e["map"] == "CD120")
    assert sorted(_BUNDLED["states"]) == on_cd120 == script.redrawn()
    assert "MO" not in _BUNDLED["states"]  # stayed map: votes on its old lines
    assert _BUNDLED["minResidents"] == 50
    assert (_BUNDLED["fromCongress"], _BUNDLED["toCongress"]) == (119, 120)


def test_the_bundled_districts_are_the_county_files_districts():
    """Same Census join as the county crosswalks, so the same districts on
    each map — a district here missing there would be a different map."""
    new = json.loads((_DATA / "county_district_crosswalk.json").read_text())["districts"]
    old = json.loads((_DATA / "county_district_crosswalk_sitting.json").read_text())["districts"]
    for st, cells in _BUNDLED["states"].items():
        assert {c[1] for c in cells} == {k for k in new if k.startswith(f"{st}-")}, st
        assert {c[0] for c in cells} == {k for k in old if k.startswith(f"{st}-")}, st


# ── the figures ─────────────────────────────────────────────────────────


def test_share_pct_never_rounds_to_all_or_nothing():
    assert rd.share_pct(996, 1000) == 99
    assert rd.share_pct(4, 1000) == 1
    assert rd.share_pct(1000, 1000) == 100
    assert rd.share_pct(0, 1000) == 0
    assert rd.share_pct(780, 1000) == 78


def test_lean_category_is_the_pvi_sign():
    assert [rd.lean_category(v) for v in (5, 0, -3, None)] == ["R", "EVEN", "D", None]


@pytest.fixture
def toy(monkeypatch):
    """Old ZZ-1 and ZZ-3 trade residents; old ZZ-2 is kept whole. A
    20-resident overlap is under the floor."""
    doc = {
        "cycle": "2026", "fromCongress": 119, "toCongress": 120, "minResidents": 50,
        "_source": "toy",
        "states": {"ZZ": [
            ["ZZ-1", "ZZ-1", 700], ["ZZ-3", "ZZ-1", 280], ["ZZ-1", "ZZ-3", 20],
            ["ZZ-2", "ZZ-2", 1000], ["ZZ-3", "ZZ-3", 660],
        ]},
    }
    tables = {119: {"ZZ-1": 3, "ZZ-2": -10, "ZZ-3": -1}, 120: {"ZZ-1": 9, "ZZ-2": -10, "ZZ-3": 2}}
    monkeypatch.setattr(rd, "_cache", doc)
    monkeypatch.setattr(rd, "district_pvi_for_congress", lambda c: (tables[c], {"congress": c}))
    computed = {119: {"ZZ-1": 3.4, "ZZ-2": -9.6, "ZZ-3": -0.7}, 120: {"ZZ-1": 8.8, "ZZ-2": -9.6, "ZZ-3": 2.3}}
    monkeypatch.setattr(rd.district_lean, "computed_lean", lambda c: computed[c])
    monkeypatch.setattr(rd, "_maps", lambda: {"2026": {"ZZ": {"map": "CD120", "reason": "toy act",
        "source": {"title": "Act", "issuedBy": "Legislature", "url": "https://example.gov/act"}}}})
    return doc


def test_state_changes_sources_and_destinations(toy):
    out = rd.state_changes("ZZ")
    one, two, three = out["newDistricts"]
    assert (one["predecessor"], one["predecessorSharePct"]) == ("ZZ-1", 71)
    assert [s["district"] for s in one["sources"]] == ["ZZ-1", "ZZ-3"]
    assert (one["pviBefore"], one["pviAfter"], one["pviShift"]) == (3, 9, 6)
    assert one["pviBeforeWeighted"] == round((3 * 700 - 1 * 280) / 980, 1)
    assert two["unchanged"] and not one["unchanged"]
    # The 20-resident overlap is counted, not listed.
    assert [s["district"] for s in three["sources"]] == ["ZZ-3"]
    assert three["sliverResidents"] == 20 and three["population"] == 680
    old_one = out["oldDistricts"][0]
    assert [d["district"] for d in old_one["destinations"]] == ["ZZ-1"]
    assert old_one["sliverResidents"] == 20
    assert out["map"]["url"] == "https://example.gov/act"


def test_computed_lean_rides_beside_cooks_and_never_into_the_counts(toy):
    out = rd.state_changes("ZZ")
    one = out["newDistricts"][0]
    assert (one["computedLeanBefore"], one["computedLeanAfter"]) == (3.4, 8.8)
    assert out["oldDistricts"][2]["computedLean"] == -0.7
    assert out["sources"]["computedLean"]["href"] == "/about/data#computed-lean"
    # The seat counts are Cook's: the toy's ZZ-3 is D on Cook's old table
    # and on the computed one alike, but only Cook's decides.
    assert out["summary"]["seatsByLeanBefore"] == {"R": 1, "EVEN": 0, "D": 2}


def test_state_summary(toy):
    s = rd.state_changes("ZZ")["summary"]
    assert s["seats"] == 3 and s["population"] == 2660
    assert s["unchangedSeats"] == 1
    # Old ZZ-3's 280 in the new ZZ-1, and old ZZ-1's 20 in the new ZZ-3.
    assert s["residentsMoved"] == 300
    assert s["seatsByLeanBefore"] == {"R": 1, "EVEN": 0, "D": 2}
    assert s["seatsByLeanAfter"] == {"R": 2, "EVEN": 0, "D": 1}
    assert s["netSeatChangeByLean"] == {"R": 1, "EVEN": 0, "D": -1}
    assert s["districtsLeaningDifferentlyFromPredecessor"] == 1  # new ZZ-3: from D-1 to R+2
    assert s["meanResidentShift"] == round((700 * 6 + 280 * 10 + 660 * 3 + 20 * 1) / 2660, 1)


def test_a_district_split_with_no_successor(monkeypatch, toy):
    toy["states"]["ZZ"] = [["ZZ-1", "ZZ-1", 600], ["ZZ-2", "ZZ-1", 400], ["ZZ-2", "ZZ-2", 100],
                           ["ZZ-3", "ZZ-2", 500], ["ZZ-3", "ZZ-3", 500]]
    out = rd.state_changes("ZZ")
    assert [r["hasSuccessor"] for r in out["oldDistricts"]] == [True, False, True]
    assert out["summary"]["oldWithoutSuccessor"] == 1


def test_ballot_notes_only_for_the_cycle_the_lines_are_for(toy):
    assert rd.new_district_notes("ZZ", 2026)[1]["predecessorSharePct"] == 71
    assert rd.new_district_notes("ZZ", 2028) == {}
    assert rd.new_district_notes("MO", 2026) == {}


# ── the API ─────────────────────────────────────────────────────────────


def test_api_serves_every_redrawn_state_and_404s_the_rest():
    c = _client()
    index = c.get("/api/elections/redistricting").json()
    assert [s["state"] for s in index["states"]] == sorted(_BUNDLED["states"])
    assert [h["state"] for h in index["heldMaps"]] == ["MO"]
    tx = c.get("/api/elections/redistricting/tx").json()
    assert tx["stateName"] == "Texas"
    assert len(tx["newDistricts"]) == 38
    assert all(r["counties"] for r in tx["newDistricts"] + tx["oldDistricts"])
    assert c.get("/api/elections/redistricting/MO").status_code == 404


def test_csv_has_every_cell():
    r = _client().get("/api/elections/redistricting/NC/overlap.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = r.text.strip().splitlines()
    assert rows[0].startswith("old_district_119,new_district_120,residents_2020")
    assert len(rows) - 1 == len(_BUNDLED["states"]["NC"])
    assert sum(int(line.split(",")[2]) for line in rows[1:]) == sum(c[2] for c in _BUNDLED["states"]["NC"])


def test_every_listed_map_cites_an_official_source():
    for st, entry in _MAPS["2026"].items():
        src = entry.get("source") or {}
        assert src.get("url", "").startswith("https://"), st
        assert src.get("title") and src.get("issuedBy"), st


def test_old_seat_note_answers_every_profile():
    c = _client()
    nc = c.get("/api/elections/redistricting/NC/old/1").json()
    assert nc["seat"]["district"] == "NC-1" and nc["fromCongressSitting"] is True
    assert c.get("/api/elections/redistricting/MO/old/5").json()["seat"] is None
    assert c.get("/api/elections/redistricting/WY/old/0").json()["seat"] is None


def test_headline_seat_change_is_after_minus_before_in_every_shipped_state():
    """The pages lead with the net change; it must be exactly what the
    before/after strips beside it show, category by category."""
    for st in _BUNDLED["states"]:
        s = rd.state_changes(st)["summary"]
        for k in ("R", "EVEN", "D"):
            assert s["netSeatChangeByLean"][k] == s["seatsByLeanAfter"][k] - s["seatsByLeanBefore"][k], (st, k)
        assert sum(s["seatsByLeanAfter"].values()) == s["seats"], st


def test_routes_resolve_on_the_real_app():
    """No earlier /api/elections/{...} route shadows the redistricting ones."""
    c = TestClient(main_app)
    index = c.get("/api/elections/redistricting")
    assert index.status_code == 200 and "heldMaps" in index.json()
    assert c.get("/api/elections/redistricting/UT").json()["state"] == "UT"
    assert c.get("/api/elections/redistricting/UT/old/1").json()["seat"]["district"] == "UT-1"
    assert c.get("/api/elections/redistricting/UT/overlap.csv").headers["content-type"].startswith("text/csv")


def test_the_index_says_a_redraw_is_past_once_its_election_is_held(freeze_utcnow, monkeypatch):
    """Before the cycle's election its new lines are the coming ones; after
    it (and into the next cycle) the same page describes a past redraw."""
    from datetime import date, datetime

    c = _client()
    for today, upcoming in ((date(2026, 10, 1), True), (date(2026, 11, 4), False), (date(2028, 3, 1), False)):
        freeze_utcnow(datetime.combine(today, datetime.min.time()).replace(hour=15))
        monkeypatch.setattr("app.api.redistricting.election_today", lambda d=today: d)
        assert c.get("/api/elections/redistricting").json()["upcoming"] is upcoming, today
