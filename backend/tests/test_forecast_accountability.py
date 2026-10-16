"""The map's ranges against the official count, and the swing map
(app/forecast/accountability.py, GET /api/elections/archive/{cycle}/accountability).

Synthetic certified results across fixture seats, read through the
archive exactly as the live sync freezes it; the clock is pinned to
election night, the results window and after January 3."""

import json
from datetime import date, datetime

import pytest

from app.api import election_archive as api
from app.election_archive import freeze
from app.forecast import accountability
from app.forecast.build import map_path
from app.models import Race, RaceResult

DAY = date(2026, 11, 3)
ELECTION_NIGHT = datetime(2026, 11, 4, 3, 0)
IN_WINDOW = datetime(2026, 11, 10, 16, 0)
AFTER_JAN_3 = datetime(2027, 2, 1, 16, 0)


@pytest.fixture(autouse=True)
def _the_119th_sits(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "CURRENT_CONGRESS", 119)


def _body(response):
    return json.loads(response.body)


def _ranged(id_, state, lean, r80, r95, mean, basis="polls", district=None):
    short = {"polls": "from polls", "no_poll": "no poll", "before_window": "polls not counted yet"}[basis]
    return {"id": id_, "state": state, "stateName": state, "special": False, "district": district,
            "lean": lean, "leanLabel": "x", "usualBin": "close", "dem": "D", "rep": "R", "incumbentParty": None,
            "status": "range", "prior": mean, "mean": mean, "range80": r80, "range95": r95,
            "range80Label": "x", "rangeBasis": basis, "rangeBasisShort": short,
            "difference": {"strength": 0, "direction": None, "seatPolls": basis == "polls"},
            "polls": {"count": 0, "used": 0, "latest": None, "list": [], "all": []}}


SEATS = {
    "senate": [
        # Official, D 52-48: margin D+4, inside both ranges; lean D+3.2 -> usual margin D+6.4.
        _ranged("2026-SEN-GA", "GA", -3.2, [-8.0, 2.0], [-11.0, 5.0], -3.0),
        # Official, R 60-40: margin R+20, outside both.
        _ranged("2026-SEN-NC", "NC", 1.0, [-5.0, 5.0], [-10.0, 10.0], 0.0, basis="no_poll"),
        # Still counting: never graded.
        _ranged("2026-SEN-OH", "OH", 5.0, [0.0, 10.0], [-4.0, 14.0], 5.0),
    ],
    "house": [
        # Official, R 55-45: margin R+10, outside 80 (-2..8) but inside 95 (-6..12).
        _ranged("2026-HOUSE-GA-6", "GA", 2.0, [-2.0, 8.0], [-6.0, 12.0], 3.0, district=6),
        # Official, but an independent ran second: not a D-vs-R race.
        _ranged("2026-HOUSE-GA-7", "GA", 0.0, [-5.0, 5.0], [-9.0, 9.0], 0.0, district=7),
        # No count read.
        _ranged("2026-HOUSE-GA-8", "GA", 4.0, [0.0, 10.0], [-4.0, 14.0], 5.0, district=8),
        # No range (no Republican nominee): its usual vote is kept, nothing is graded.
        {"id": "2026-HOUSE-GA-9", "state": "GA", "stateName": "GA", "special": False, "district": 9,
         "lean": -10.0, "leanLabel": "D+10", "usualBin": "D_usually", "dem": "D", "rep": None,
         "incumbentParty": None, "status": "not_contested", "reason": "x"},
    ],
}

COUNTS = {
    "2026-SEN-GA": (True, [("DEM", 520), ("REP", 480)]),
    "2026-SEN-NC": (True, [("REP", 600), ("DEM", 400)]),
    "2026-SEN-OH": (False, [("DEM", 900), ("REP", 100)]),
    "2026-HOUSE-GA-6": (True, [("REP", 550), ("DEM", 450), ("LIB", 20)]),
    "2026-HOUSE-GA-7": (True, [("DEM", 500), ("IND", 400), ("REP", 100)]),
}


def _map(seats=SEATS, as_of="2026-11-02T23:55:00Z"):
    calibration = {"cycles": [1998, 2024], "races": 300, "coverage80": 0.81, "coverage95": 0.94,
                   "coverage80Fundamentals": 0.79,
                   "forward": {"cycle": 2024, "races": 34, "coverage80": 0.82, "coverage95": 0.97}}
    return {"schemaVersion": 2, "asOf": as_of, "electionDay": "2026-11-03", "cycle": 2026,
            "bins": {"leansMax": 5.0, "usuallyMax": 15.0}, "sources": {"lean": {"house": {"congress": 120}}},
            "chambers": {c: {"calibration": calibration, "seats": s} for c, s in seats.items()}}


def _result(official, tallies, counted=datetime(2026, 11, 4, 3)):
    return {"official": official, "sourceName": "Secretary of State", "sourceUrl": "https://sos.example",
            "fetchedAt": counted.isoformat() + "Z",
            "candidates": [{"name": p, "party": p, "votes": v} for p, v in tallies]}


def _record(counts=COUNTS):
    return {"cycle": 2026, "electionDay": "2026-11-03",
            "races": [{"id": rid, "result": _result(*c)} for rid, c in counts.items()]}


def _seat(graded, chamber, seat_id):
    return next(s for s in graded["chambers"][chamber]["seats"] if s["id"] == seat_id)


class TestGrade:
    def test_each_seat_against_its_frozen_ranges(self):
        g = accountability.grade(_map(), _record())
        ga = _seat(g, "senate", "2026-SEN-GA")
        assert (ga["count"], ga["margin"], ga["marginLabel"]) == ("official", -4.0, "D+4")
        assert ga["inside80"] is True and ga["inside95"] is True and ga["fromCentre"] == -1.0
        nc = _seat(g, "senate", "2026-SEN-NC")
        assert nc["margin"] == 20.0 and nc["inside80"] is False and nc["inside95"] is False
        h6 = _seat(g, "house", "2026-HOUSE-GA-6")
        # The two-party margin: the Libertarian's votes are left out, as the fit's results were.
        assert h6["margin"] == 10.0 and h6["inside80"] is False and h6["inside95"] is True

    def test_a_count_not_yet_official_is_never_graded(self):
        g = accountability.grade(_map(), _record())
        oh = _seat(g, "senate", "2026-SEN-OH")
        assert oh["count"] == "counting"
        assert oh["margin"] is None and oh["inside80"] is None and oh["swing"] is None
        assert g["chambers"]["senate"]["counts"] == {"official": 2, "counting": 1, "no_count": 0,
                                                     "not_two_party": 0}
        assert g["chambers"]["senate"]["certified"] == 2 and g["chambers"]["senate"]["ranged"] == 3

    def test_a_race_whose_top_two_are_not_d_and_r_is_not_graded(self):
        g = accountability.grade(_map(), _record())
        h7 = _seat(g, "house", "2026-HOUSE-GA-7")
        assert h7["count"] == "not_two_party" and h7["inside80"] is None and h7["swing"] is None
        assert _seat(g, "house", "2026-HOUSE-GA-8")["count"] == "no_count"
        house = g["chambers"]["house"]
        assert house["counts"] == {"official": 1, "counting": 0, "no_count": 1, "not_two_party": 1}
        # Certified counts both official kinds; only the D-vs-R one is graded.
        assert house["certified"] == 2 and house["all"]["graded"] == 1

    def test_swing_is_the_margin_minus_twice_the_lean(self):
        g = accountability.grade(_map(), _record())
        ga = _seat(g, "senate", "2026-SEN-GA")
        assert ga["usualMargin"] == -6.4 and ga["usualMarginLabel"] == "D+6"
        assert ga["swing"] == 2.4 and ga["swingLabel"] == "R+2" and ga["swingBin"] == "R_leans"
        assert _seat(g, "senate", "2026-SEN-NC")["swing"] == 18.0
        assert g["chambers"]["senate"]["swing"] == {"seats": 2, "mean": 10.2, "meanLabel": "R+10",
                                                    "median": 10.2, "medianLabel": "R+10"}
        # An unranged seat still has its usual vote, but no count, no swing.
        assert _seat(g, "house", "2026-HOUSE-GA-9")["usualMargin"] == -20.0

    def test_aggregates_by_chamber_and_basis_beside_the_promise(self):
        g = accountability.grade(_map(), _record())
        senate = g["chambers"]["senate"]
        assert senate["all"]["graded"] == 2 and senate["all"]["held80"] == 1 and senate["all"]["held95"] == 1
        assert senate["all"]["share80"] == 0.5
        assert senate["all"]["interval80"] == accountability.clopper_pearson(1, 2)
        by = {b["basis"]: b for b in senate["byBasis"]}
        assert by["polls"]["graded"] == 1 and by["polls"]["ranged"] == 2 and by["polls"]["held80"] == 1
        assert by["no_poll"]["held80"] == 0 and by["no_poll"]["label"] == "no poll"
        assert senate["promised"]["nominal80"] == 0.8 and senate["promised"]["heldOut"]["coverage80"] == 0.81
        assert senate["promised"]["forward"]["cycle"] == 2024
        # Misses are listed, not hidden.
        assert [(m["id"], m["outsideBy"]) for m in senate["misses"]] == [("2026-SEN-NC", 15.0)]
        assert [m["id"] for m in g["chambers"]["house"]["misses"]] == ["2026-HOUSE-GA-6"]

    def test_nothing_official_yet_grades_nothing(self):
        counts = {k: (False, t) for k, (_, t) in COUNTS.items()}
        g = accountability.grade(_map(), _record(counts))
        for c in g["chambers"].values():
            assert c["all"] == {"graded": 0, "held80": 0, "share80": None, "interval80": None,
                                "held95": 0, "share95": None, "interval95": None}
            assert c["certified"] == 0 and c["misses"] == [] and c["swing"]["seats"] == 0


class TestClopperPearson:
    def test_known_values(self):
        # Clopper & Pearson's exact interval, 8 of 10 at 95%: 0.444 to 0.975.
        assert accountability.clopper_pearson(8, 10) == [0.444, 0.975]
        assert accountability.clopper_pearson(0, 10) == [0.0, 0.308]
        assert accountability.clopper_pearson(10, 10) == [0.692, 1.0]
        assert accountability.clopper_pearson(0, 0) is None


def _write_map_file(data):
    with open(map_path(), "w") as fh:
        json.dump(data, fh)


def _seed(db, counts=COUNTS, counted=datetime(2026, 11, 4, 3)):
    for chamber, seats in SEATS.items():
        for s in seats:
            db.add(Race(id=s["id"], cycle_year=2026, office="S" if chamber == "senate" else "H", state=s["state"],
                        district=s["district"]))
    for rid, (official, tallies) in counts.items():
        db.add(RaceResult(
            race_id=rid, election_date="2026-11-03", source_name="Secretary of State",
            source_url="https://sos.example",
            tallies=json.dumps([{"name": f"{p} nominee", "party": p, "votes": v} for p, v in tallies]),
            votes_counted=sum(v for _, v in tallies), official=official,
            first_reported_at=counted, last_change_at=counted, fetched_at=counted))
    db.commit()


def _archive(db, freeze_utcnow, counts=COUNTS):
    freeze_utcnow(ELECTION_NIGHT)
    _write_map_file(_map())
    _seed(db, counts)
    freeze(db, DAY)


class TestThroughTheArchive:
    def test_election_night_nothing_official_says_so(self, db_session, freeze_utcnow):
        _archive(db_session, freeze_utcnow, {k: (False, t) for k, (_, t) in COUNTS.items()})
        data = _body(api.archive_accountability(2026, db_session))
        assert data["stillCounting"] is True
        assert data["chambers"]["senate"]["certified"] == 0 and data["chambers"]["senate"]["ranged"] == 3
        assert data["chambers"]["senate"]["all"]["graded"] == 0

    def test_results_window_grades_what_turned_official(self, db_session, freeze_utcnow):
        _archive(db_session, freeze_utcnow, {k: (False, t) for k, (_, t) in COUNTS.items()})
        # Georgia's Senate count turns official mid-window; the next pass copies it.
        db_session.get(RaceResult, "2026-SEN-GA").official = True
        db_session.commit()
        freeze_utcnow(IN_WINDOW)
        freeze(db_session, DAY)
        data = _body(api.archive_accountability(2026, db_session))
        assert data["stillCounting"] is True
        senate = data["chambers"]["senate"]
        assert senate["certified"] == 1 and senate["all"]["graded"] == 1 and senate["all"]["held80"] == 1

    def test_after_january_3_still_served_and_final(self, db_session, freeze_utcnow):
        _archive(db_session, freeze_utcnow)
        freeze_utcnow(AFTER_JAN_3)
        data = _body(api.archive_accountability(2026, db_session))
        assert data["stillCounting"] is False and data["cycle"] == 2026
        assert data["chambers"]["senate"]["all"]["graded"] == 2
        [entry] = _body(api.archive_index(db_session))["cycles"]
        assert entry["accountability"] == {"ranged": 6, "certified": 4, "official": 3, "graded": 3,
                                           "held80": 1, "held95": 2, "swingSeats": 3}

    def test_the_frozen_map_is_used_never_a_rebuild(self, db_session, freeze_utcnow):
        """No look-ahead: once frozen, a later map file (the next build, a
        refit after the result) never changes what the ranges were."""
        _archive(db_session, freeze_utcnow)
        refit = json.loads(json.dumps(SEATS))
        refit["senate"][1]["range80"] = [15.0, 25.0]  # a range that would now "hold" NC
        _write_map_file(_map(refit, as_of="2026-11-20T00:00:00Z"))
        freeze_utcnow(IN_WINDOW)
        freeze(db_session, DAY)
        data = _body(api.archive_accountability(2026, db_session))
        nc = next(s for s in data["chambers"]["senate"]["seats"] if s["id"] == "2026-SEN-NC")
        assert nc["range80"] == [-5.0, 5.0] and nc["inside80"] is False
        assert data["mapAsOf"] == "2026-11-02T23:55:00Z"

    def test_csv(self, db_session, freeze_utcnow):
        _archive(db_session, freeze_utcnow)
        freeze_utcnow(AFTER_JAN_3)
        text = api.archive_swing_csv(2026, db_session).body.decode()
        lines = [ln for ln in text.splitlines() if not ln.startswith("#")]
        assert lines[0].split(",") == list(accountability.CSV_COLUMNS)
        ga = next(ln for ln in lines if ln.startswith("senate,2026-SEN-GA,"))
        assert ",official," in ga and ",-4.0,2.4," in ga and ",True,True," in ga
        oh = next(ln for ln in lines if ln.startswith("senate,2026-SEN-OH,"))
        assert ",counting," in oh and ",True," not in oh
        assert "CC BY-SA 4.0" in text

    def test_no_map_kept_is_a_404(self, db_session, freeze_utcnow):
        from fastapi import HTTPException

        freeze_utcnow(ELECTION_NIGHT)
        _seed(db_session)
        freeze(db_session, DAY)
        with pytest.raises(HTTPException) as exc:
            api.archive_accountability(2026, db_session)
        assert exc.value.status_code == 404
        assert _body(api.archive_index(db_session))["cycles"][0]["accountability"] is None
