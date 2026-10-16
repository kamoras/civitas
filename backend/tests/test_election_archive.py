"""The election archive (app/election_archive.py, app/api/election_archive.py):
what the site keeps about a held general election once its results window
closes and the site moves on to the next cycle.

Every test pins the clock to one moment of the 2026 cycle: the day before
the election, election night, the results window, after January 3, and an
odd year with no federal general at all."""

import json
from datetime import date, datetime

import pytest
from fastapi import HTTPException

from app.api import election_archive as api
from app.api import elections
from app.database import RESET_KEEPS
from app.election_archive import freeze
from app.forecast.build import map_path
from app.models import BallotMeasure, Candidate, ElectionArchive, Race, RaceResult

DAY = date(2026, 11, 3)
DAY_BEFORE = datetime(2026, 11, 2, 16, 0)
ELECTION_NIGHT = datetime(2026, 11, 4, 3, 0)  # 10 PM ET on election day
IN_WINDOW = datetime(2026, 11, 10, 16, 0)
AFTER_JAN_3 = datetime(2027, 2, 1, 16, 0)
ODD_YEAR = datetime(2027, 6, 15, 16, 0)


@pytest.fixture(autouse=True)
def _the_119th_sits(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "CURRENT_CONGRESS", 119)


def _body(response):
    return json.loads(response.body)


def _write_map(election_day="2026-11-03", as_of="2026-11-02T23:55:00Z", lean=-3.2):
    seat = {"id": "2026-SEN-GA", "state": "GA", "stateName": "Georgia", "special": False, "district": None,
            "lean": lean, "leanLabel": "D+3", "usualBin": "D_leans", "dem": "Dana Smith", "rep": "Ray Jones",
            "incumbentParty": "D", "status": "range", "prior": -2.0, "range80": [-8.0, 2.0], "range95": [-11.0, 5.0],
            "pollAverage": -3.0, "pollSource": {"state": "ok"}, "difference": {"strength": 0, "direction": None},
            "polls": {"count": 2, "used": 2, "latest": "2026-10-30", "list": [], "all": [{"pollster": "X"}]}}
    data = {"schemaVersion": 2, "asOf": as_of, "electionDay": election_day, "cycle": 2026,
            "sources": {"lean": {"house": {"congress": 120}}},
            "chambers": {"senate": {"seats": [seat]}, "house": {"seats": []}}}
    with open(map_path(), "w") as fh:
        json.dump(data, fh)


def _seed(db, counted=datetime(2026, 11, 4, 3)):
    db.add(Race(id="2026-SEN-GA", cycle_year=2026, office="S", state="GA", district=None))
    db.add(Race(id="2026-HOUSE-GA-6", cycle_year=2026, office="H", state="GA", district=6))
    db.add_all([
        Candidate(id="S6GA1", race_id="2026-SEN-GA", name="SMITH, DANA", ballot_name="Dana Smith", party="DEM",
                  confirmed_general=True, incumbent_challenge="I"),
        Candidate(id="S6GA2", race_id="2026-SEN-GA", name="JONES, RAY", ballot_name="Ray Jones", party="REP",
                  confirmed_general=True, contributions=1_000_000.0),
        # Lost the primary: not on the November ballot, so not archived.
        Candidate(id="S6GA3", race_id="2026-SEN-GA", name="DOE, PAT", party="REP"),
        Candidate(id="H6GA1", race_id="2026-HOUSE-GA-6", name="ROE, LEE", party="DEM"),
    ])
    db.add(RaceResult(
        race_id="2026-SEN-GA", election_date="2026-11-03", source_name="Georgia Secretary of State",
        source_url="https://results.example/ga",
        tallies=json.dumps([{"name": "Dana Smith", "party": "DEM", "votes": 520, "candidateId": "S6GA1"},
                            {"name": "Ray Jones", "party": "REP", "votes": 480, "candidateId": "S6GA2"}]),
        votes_counted=1000, reporting_units=159, total_units=159, unit_label="counties", official=True,
        held_by_party="DEM", first_reported_at=counted, last_change_at=counted, fetched_at=counted,
    ))
    db.add(BallotMeasure(id="ga-2026-1", state="GA", election_date="2026-11-03", number="Amendment 1",
                         title="Amendment 1", official_summary="Shall the Constitution be amended ...?",
                         title_authority="General Assembly", source_name="Georgia Secretary of State",
                         source_url="https://sos.example/ga"))
    # An earlier election's measure never lands in this election's archive.
    db.add(BallotMeasure(id="ga-2024-1", state="GA", election_date="2024-11-05", number="Amendment 1",
                         source_name="Georgia Secretary of State"))
    db.commit()


class TestFreeze:
    def test_copies_the_final_map_once_and_the_record_each_pass(self, db_session, freeze_utcnow):
        freeze_utcnow(ELECTION_NIGHT)
        _write_map()
        _seed(db_session)
        row = freeze(db_session, DAY)
        assert row.map_as_of == datetime(2026, 11, 2, 23, 55)
        frozen = json.loads(row.map_json)
        # The full poll rows the API never serves are left out.
        assert frozen["chambers"]["senate"]["seats"][0]["polls"] == {
            "count": 2, "used": 2, "latest": "2026-10-30", "list": []}
        record = json.loads(row.record_json)
        senate = next(r for r in record["races"] if r["id"] == "2026-SEN-GA")
        assert [c["ballotName"] for c in senate["candidates"]] == ["Dana Smith", "Ray Jones"]
        assert "contributions" not in senate["candidates"][0]
        assert senate["result"]["official"] is True and senate["result"]["leaderParty"] == "DEM"
        house = next(r for r in record["races"] if r["id"] == "2026-HOUSE-GA-6")
        assert house["result"] is None and house["candidateSource"] == "filers"
        assert [m["id"] for m in record["measures"]] == ["ga-2026-1"]
        assert record["measures"][0]["titleAuthority"] == "General Assembly"

        # A later pass: the count moved, and the map file changed under it.
        _write_map(as_of="2026-11-09T12:00:00Z", lean=9.9)
        result = db_session.get(RaceResult, "2026-SEN-GA")
        result.tallies = json.dumps([{"name": "Dana Smith", "party": "DEM", "votes": 530, "candidateId": None}])
        result.votes_counted = 530
        db_session.commit()
        freeze_utcnow(IN_WINDOW)
        row = freeze(db_session, DAY)
        assert json.loads(row.map_json)["chambers"]["senate"]["seats"][0]["lean"] == -3.2
        assert row.map_as_of == datetime(2026, 11, 2, 23, 55)
        senate = next(r for r in json.loads(row.record_json)["races"] if r["id"] == "2026-SEN-GA")
        assert senate["result"]["votesCounted"] == 530
        assert row.record_as_of == IN_WINDOW

    def test_another_elections_map_is_never_copied(self, db_session, freeze_utcnow):
        freeze_utcnow(ELECTION_NIGHT)
        _write_map(election_day="2028-11-07")
        _seed(db_session)
        row = freeze(db_session, DAY)
        assert row.map_json is None and row.record_json is not None

    def test_a_wiped_database_never_replaces_the_record(self, db_session, freeze_utcnow):
        freeze_utcnow(ELECTION_NIGHT)
        _write_map()
        _seed(db_session)
        freeze(db_session, DAY)
        for model in (RaceResult, Candidate, Race, BallotMeasure):
            db_session.query(model).delete()
        db_session.commit()
        freeze_utcnow(IN_WINDOW)
        row = freeze(db_session, DAY)
        assert len(json.loads(row.record_json)["races"]) == 2
        assert row.record_as_of == ELECTION_NIGHT

    def test_nothing_to_keep_writes_no_row(self, db_session, freeze_utcnow):
        freeze_utcnow(ELECTION_NIGHT)
        assert freeze(db_session, DAY) is None
        assert db_session.query(ElectionArchive).count() == 0

    def test_a_data_reset_keeps_the_archive(self):
        assert "election_archives" in RESET_KEEPS


def _archived(db, freeze_utcnow):
    freeze_utcnow(ELECTION_NIGHT)
    _write_map()
    _seed(db)
    freeze(db, DAY)


class TestArchiveIndex:
    """What the Archive tab reads, at each moment of the cycle."""

    @pytest.mark.parametrize("when,cycle,phase", [
        (DAY_BEFORE, 2026, "campaign"),
        (AFTER_JAN_3, 2028, "campaign"),
        (ODD_YEAR, 2028, "campaign"),
    ])
    def test_nothing_archived_names_the_next_election(self, db_session, freeze_utcnow, when, cycle, phase):
        freeze_utcnow(when)
        data = _body(api.archive_index(db_session))
        assert data["cycles"] == []
        assert data["current"]["cycle"] == cycle and data["current"]["phase"] == phase

    @pytest.mark.parametrize("when,phase,counting", [
        (ELECTION_NIGHT, "election_day", True),
        (IN_WINDOW, "results", True),
        (AFTER_JAN_3, "campaign", False),
        (ODD_YEAR, "campaign", False),
    ])
    def test_the_held_election_from_election_day_on(self, db_session, freeze_utcnow, when, phase, counting):
        _archived(db_session, freeze_utcnow)
        freeze_utcnow(when)
        data = _body(api.archive_index(db_session))
        [entry] = data["cycles"]
        assert entry["cycle"] == 2026 and entry["hasMap"] is True
        assert entry["stillCounting"] is counting
        assert (entry["races"], entry["counted"], entry["official"], entry["measures"]) == (2, 1, 1, 1)
        assert data["current"]["phase"] == phase


class TestPastCycle:
    """After the window: /api/elections/results is empty, and the archive
    still answers for the election it held."""

    def test_results_map_and_race_links_resolve_after_january_3(self, db_session, freeze_utcnow):
        _archived(db_session, freeze_utcnow)
        freeze_utcnow(AFTER_JAN_3)
        assert _body(elections.live_results(None, db_session))["races"] == []

        record = _body(api.archive_record(2026, db_session))
        assert record["stillCounting"] is False and record["hasMap"] is True
        assert record["totals"]["official"] == 1
        assert [s["state"] for s in record["states"]] == ["GA"]

        senate_map = _body(api.archive_map(2026, "senate", db_session))
        assert senate_map["frozen"] is True and senate_map["stale"] is False
        assert senate_map["chambersAvailable"] == ["senate", "house"] and list(senate_map["chambers"]) == ["senate"]
        csv = api.archive_map_csv(2026, db_session)
        assert b"2026-SEN-GA" in csv.body and "final" in csv.headers["content-disposition"]

        race = _body(elections.race_detail("2026-HOUSE-GA-6", db_session))
        assert race["archiveCycle"] == 2026 and race["state"] == "GA"

    def test_a_race_link_resolves_from_the_archive_once_its_rows_are_gone(self, db_session, freeze_utcnow):
        _archived(db_session, freeze_utcnow)
        freeze_utcnow(AFTER_JAN_3)
        for model in (RaceResult, Candidate, Race):
            db_session.query(model).delete()
        db_session.commit()
        race = _body(elections.race_detail("2026-SEN-GA", db_session))
        assert race == {**race, "state": "GA", "archiveCycle": 2026, "office": "S"}
        assert [c["ballotName"] for c in race["candidates"]] == ["Dana Smith", "Ray Jones"]
        for missing in ("2026-SEN-ZZ", "nonsense", "2030-SEN-GA"):
            with pytest.raises(HTTPException) as err:
                elections.race_detail(missing, db_session)
            assert err.value.status_code == 404

    @pytest.mark.parametrize("when", [ELECTION_NIGHT, IN_WINDOW])
    def test_the_sites_own_election_is_not_sent_to_the_archive(self, db_session, freeze_utcnow, when):
        """Through the results window the state page still shows the held
        ballot and its count, so its race links stay there."""
        _archived(db_session, freeze_utcnow)
        freeze_utcnow(when)
        assert _body(elections.race_detail("2026-SEN-GA", db_session))["archiveCycle"] is None

    def test_no_archive_is_a_404(self, db_session, freeze_utcnow):
        freeze_utcnow(AFTER_JAN_3)
        for call in (lambda: api.archive_record(2024, db_session), lambda: api.archive_map(2024, None, db_session)):
            with pytest.raises(HTTPException) as err:
                call()
            assert err.value.status_code == 404

    def test_an_archive_without_a_map_says_so(self, db_session, freeze_utcnow):
        freeze_utcnow(ELECTION_NIGHT)
        _seed(db_session)
        freeze(db_session, DAY)
        freeze_utcnow(AFTER_JAN_3)
        assert _body(api.archive_record(2026, db_session))["hasMap"] is False
        with pytest.raises(HTTPException) as err:
            api.archive_map(2026, None, db_session)
        assert err.value.status_code == 404


def test_the_sitemap_lists_each_archived_election(db_session, freeze_utcnow):
    from app.api.sitemap import sitemap_entries

    _archived(db_session, freeze_utcnow)
    freeze_utcnow(AFTER_JAN_3)
    assert _body(sitemap_entries(db_session))["electionArchives"] == [{"id": "2026", "lastmod": "2026-11-04"}]


def _seed_state_contests(db):
    """Georgia's governor, one state senate seat and a supreme court seat,
    and North Carolina's governor alone (a state with no federal race
    seeded): the state ballot page's own sections."""
    from app.models import JudicialNominee, StateLegNominee, StatewideNominee
    from app.pipeline.cache import api_cache_set
    from app.pipeline.fetch.state_candidates_common import (
        JUDICIAL_MARKER_TIER, STATEWIDE_MARKER_TIER, judicial_marker_key, statewide_marker_key,
    )

    db.add_all([
        StatewideNominee(state="GA", cycle_year=2026, office="governor", party="D", display_name="Dana Smith"),
        StatewideNominee(state="GA", cycle_year=2026, office="governor", party="R", display_name="Ray Jones"),
        StateLegNominee(state="GA", cycle_year=2026, chamber="upper", district="5", party="D",
                        display_name="Lee Roe"),
        JudicialNominee(state="GA", cycle_year=2026, court="supreme", seat="3", party="N",
                        party_label="Nonpartisan", display_name="Pat Doe"),
        StatewideNominee(state="NC", cycle_year=2026, office="governor", party="R", display_name="Sam Lee"),
    ])
    for st in ("GA", "NC"):
        api_cache_set(db, STATEWIDE_MARKER_TIER, statewide_marker_key(st, 2026),
                      {"checkedAt": "2026-11-01T00:00:00Z", "sourceName": f"{st} SOS", "ballotList": st == "GA"})
    api_cache_set(db, JUDICIAL_MARKER_TIER, judicial_marker_key("GA", 2026),
                  {"checkedAt": "2026-11-01T00:00:00Z", "sourceName": "GA SOS", "ballotList": True})
    db.commit()


class TestStateContests:
    """The statewide offices, legislative seats and judgeships the state
    ballot page shows are archived with the federal ballot, and outlive
    their rows the same way."""

    def test_kept_with_their_sources_and_without_the_finding_aids(self, db_session, freeze_utcnow):
        freeze_utcnow(ELECTION_NIGHT)
        _seed(db_session)
        _seed_state_contests(db_session)
        record = json.loads(freeze(db_session, DAY).record_json)
        states = {s["state"]: s for s in record["states"]}
        ga = states["GA"]["stateContests"]
        assert [n["name"] for n in ga["statewideRaces"][0]["nominees"]] == ["Dana Smith", "Ray Jones"]
        assert "areas" not in ga["statewideRaces"][0]
        [chamber] = ga["stateLegRaces"]
        [district] = chamber["districts"]
        assert district["district"] == "5" and "towns" not in district
        assert [n["name"] for n in district["nominees"]] == ["Lee Roe"]
        assert ga["judicialRaces"][0]["seats"][0]["nominees"][0]["partyLabel"] == "Nonpartisan"
        assert ga["statewideSource"] == {"sourceName": "GA SOS", "ballotList": True}
        # A state whose only contests are its own is still in the record.
        assert states["NC"]["stateContests"]["statewideSource"]["ballotList"] is False

    def test_served_after_january_3_once_their_rows_are_gone(self, db_session, freeze_utcnow):
        from app.models import ApiCache, JudicialNominee, StateLegNominee, StatewideNominee

        freeze_utcnow(ELECTION_NIGHT)
        _seed(db_session)
        _seed_state_contests(db_session)
        freeze(db_session, DAY)
        freeze_utcnow(AFTER_JAN_3)
        # What a data reset outside the window wipes.
        for model in (StatewideNominee, StateLegNominee, JudicialNominee, ApiCache):
            db_session.query(model).delete()
        db_session.commit()
        record = _body(api.archive_record(2026, db_session))
        assert record["totals"]["stateContests"] == 4  # GA governor, senate 5, supreme 3; NC governor
        ga = next(s for s in record["states"] if s["state"] == "GA")
        assert ga["stateContests"]["judicialRaces"][0]["seats"][0]["nominees"][0]["name"] == "Pat Doe"
        assert _body(api.archive_index(db_session))["cycles"][0]["stateContests"] == 4
