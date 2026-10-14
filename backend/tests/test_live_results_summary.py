"""The homepage's results entry (live_results/summary.py, GET
/api/elections/results/summary): one entry per state with votes counted,
in fixed sentences that say "leads" and "not final", never "wins"."""

import json
from datetime import datetime

import pytest

from app.api import elections
from app.live_results.summary import state_summaries
from app.election_phase import active_election
from app.models import LiveResultRead, Race, RaceResult

# Election night, 11:15 PM ET, and a day in the campaign.
NIGHT = datetime(2026, 11, 4, 4, 15)
CAMPAIGN = datetime(2026, 10, 14, 16)
COUNTED = datetime(2026, 11, 4, 3, 50)


def _count(db, race_id, state, office, district, tallies, *, official=False, units=(58, 100),
           at=COUNTED, special=False):
    db.add(Race(id=race_id, cycle_year=2026, office=office, state=state, district=district, is_special=special))
    db.add(RaceResult(
        race_id=race_id, election_date="2026-11-03", source_name=f"{state} Secretary of State (Clarity)",
        source_url="https://results.example/", votes_counted=sum(v for _, _, v in tallies),
        tallies=json.dumps([{"name": n, "party": p, "votes": v, "candidateId": None} for n, p, v in tallies]),
        reporting_units=units[0], total_units=units[1], unit_label="counties", units_complete=False,
        official=official, held_by_party="DEM", first_reported_at=at, last_change_at=at, fetched_at=at,
    ))


@pytest.fixture
def night(db_session, freeze_utcnow):
    freeze_utcnow(NIGHT)
    _count(db_session, "2026-SEN-CO", "CO", "S", None, [("Pat Placeholder", "DEM", 5210), ("Lee Example", "REP", 4790)])
    for district, lead in ((1, "DEM"), (2, "DEM"), (3, "REP")):
        trail = "REP" if lead == "DEM" else "DEM"
        _count(db_session, f"2026-HOUSE-CO-{district}", "CO", "H", district,
               [(f"Lead {district}", lead, 600), (f"Trail {district}", trail, 400)])
    _count(db_session, "2026-HOUSE-WY-0", "WY", "H", 0, [("Sam Sample", "REP", 700), ("Kim Case", "DEM", 300)],
           official=True, units=(23, 23), at=datetime(2026, 11, 4, 4, 5))
    db_session.commit()
    return db_session


def test_each_state_reads_as_leading_and_not_final(night):
    wy, co = state_summaries(night, active_election(night))  # most recently changed first
    assert co["state"] == "CO" and co["status"] == "not_final" and co["href"] == "/elections/states/CO"
    assert co["lines"] == [
        "Colorado's U.S. Senate: Pat Placeholder (D) leads with 52.1%. Results from 58 of 100 counties (58%).",
        "3 U.S. House races counted: Democrats lead in 2, Republicans in 1.",
    ]
    assert (co["racesCounted"], co["official"], co["source"]) == (4, 0, "CO Secretary of State")
    assert wy["status"] == "official"
    assert wy["lines"] == [
        "Wyoming's at-large U.S. House seat: Sam Sample (R) leads with 70.0% in the count the state lists as official."]
    text = " ".join(line for e in (wy, co) for line in e["lines"]).lower()
    assert "wins" not in text and "won" not in text and "called" not in text


def test_a_state_part_way_official_says_which_part(night):
    night.get(RaceResult, "2026-HOUSE-CO-1").official = True
    night.commit()
    co = next(e for e in state_summaries(night, active_election(night)) if e["state"] == "CO")
    assert co["status"] == "not_final"
    assert co["lines"][-1] == "The state lists 1 of these 4 counts as official; the rest are not final."


def test_a_failed_read_is_said_not_shown_as_live(night):
    night.add(LiveResultRead(state="CO", election_date="2026-11-03", status="failed",
                             checked_at=NIGHT, last_ok_at=COUNTED))
    night.commit()
    co = next(e for e in state_summaries(night, active_election(night)) if e["state"] == "CO")
    assert co["lines"][-1].startswith("The last check of the state's feed failed")


def test_a_tie_names_no_leader(db_session, freeze_utcnow):
    freeze_utcnow(NIGHT)
    _count(db_session, "2026-SEN-OH", "OH", "S", None, [("A Person", "DEM", 500), ("B Person", "REP", 500)])
    db_session.commit()
    (oh,) = state_summaries(db_session, active_election(db_session))
    assert oh["lines"] == ["Ohio's U.S. Senate: the count is tied."]


def test_nothing_outside_the_results_window(night, freeze_utcnow):
    freeze_utcnow(CAMPAIGN)
    body = json.loads(elections.results_summary(night).body)
    assert body["phase"]["phase"] == "campaign" and body["states"] == []


def test_the_route_serves_the_entries_in_the_window(night):
    body = json.loads(elections.results_summary(night).body)
    assert body["phase"]["phase"] in ("election_day", "results")
    assert [e["state"] for e in body["states"]] == ["WY", "CO"]
