"""An Action Center issue's ballot links: matched from its own words when
it is written (action_center.ballot_links), served with the state's voter
information (app/ballot_links.py)."""

import json
from datetime import date, datetime

import pytest

from app import ballot_links as served
from app.api.action import _build_issue_response
from app.models import ActionIssue, Candidate, Race
from app.pipeline.analyze.action_center import ballot_links, link_issue_ballots, race_named, states_named


@pytest.fixture
def races(db_session):
    rows = [
        (Race(id="2026-SEN-GA", cycle_year=2026, office="S", state="GA"),
         [("ROE, DANA A JR", "Dana Roe", True), ("DOE, SAMUEL", "Sam Doe", True)]),
        (Race(id="2026-SEN-MI", cycle_year=2026, office="S", state="MI"),
         [("QUILL, PATRICK J", "Pat Quill", True)]),
        (Race(id="2026-HOUSE-AL-3", cycle_year=2026, office="H", state="AL", district=3),
         [("QUILL, PATRICK", "Pat Quill", True)]),
        (Race(id="2026-SEN-SC", cycle_year=2026, office="S", state="SC"),
         [("MARSH, LEE", "Lee Marsh", True)]),
        (Race(id="2026-SEN-AK", cycle_year=2026, office="S", state="AK"),
         [("VALE, MARY", "Mary Vale", True), ("LOSER, PAT", "Pat Loser", False)]),
    ]
    for race, cands in rows:
        db_session.add(race)
        for i, (name, ballot, confirmed) in enumerate(cands):
            db_session.add(Candidate(id=f"{race.id}-{i}", race_id=race.id, name=name, ballot_name=ballot,
                                     party="REP", confirmed_general=confirmed))
    db_session.commit()
    return db_session


def links(db, title, summary="", facts=(), election=False):
    return [(link["via"], link["raceId"] or link["state"], link["candidate"])
            for link in ballot_links(db, 2026, title, summary, list(facts), election_story=lambda: election)]


def test_states_named_longest_first_and_not_the_capital():
    assert states_named("West Virginia and Virginia; Washington, D.C. hosts it") == ["WV", "VA"]
    assert states_named("the georgia-pacific mill") == []


def test_a_nominee_by_full_name(races):
    assert links(races, "Doe criticizes plan", facts=["Sen. Sam Doe said it was wrong."]) == [
        ("candidate", "2026-SEN-GA", "Sam Doe")]


def test_an_unconfirmed_candidate_links_nothing(races):
    assert links(races, "Pat Loser files in Alaska") == []


def test_a_full_name_two_races_share_needs_its_state(races):
    assert links(races, "Pat Quill urges an end to the war") == []
    assert links(races, "Pat Quill urges an end to the war", facts=["Michigan Democrats objected."]) == [
        ("candidate", "2026-SEN-MI", "Pat Quill")]


def test_a_surname_beside_its_state(races):
    assert links(races, "The president stumps for Roe in Georgia") == [("surname", "2026-SEN-GA", "Roe")]


def test_a_surname_another_person_holds_names_no_one(races):
    title = "Hearing honors retired Senator Jordan Marsh"
    assert links(races, title, facts=["Marsh's papers will go to South Carolina."]) == []


def test_the_seat_phrase(races):
    assert links(races, "Alaska Senate race tightens") == [("seat", "2026-SEN-AK", None)]
    assert race_named("2026-SEN-AK", "Alaska Senate race tightens", races, need_candidate=False)
    # The legislature's chamber is not the U.S. Senate race.
    assert not race_named("2026-SEN-AK", "Alaska state Senate race tightens", races, need_candidate=False)


def test_a_state_alone_only_in_an_election_story(races):
    title = "Agency sues California over mail ballots"
    assert links(races, title, election=False) == []
    assert links(races, title, election=True) == [("state", "CA", None)]


def test_served_with_the_state_voter_information(monkeypatch):
    monkeypatch.setattr(served, "calendar_for_state", lambda state, day, now=None: {
        "registration": {"status": "open", "sentence": "Register online by Monday, October 19",
                         "closesOn": "2026-10-19", "daysLeft": 3, "source": {}},
        "register": {"url": "https://example.gov/register", "publisher": "Secretary of State",
                     "confirmed": True, "statePage": True}})
    monkeypatch.setattr(served, "lookup_for_state", lambda state: {"url": "https://example.gov/lookup"})
    raw = json.dumps([
        {"state": "GA", "raceId": "2026-SEN-GA", "candidate": "Roe", "via": "surname"},
        {"state": "GA", "raceId": "2026-HOUSE-GA-2", "candidate": None, "via": "seat"},
        {"state": "ZZ", "raceId": None, "candidate": None, "via": "state"},
    ])
    [ga] = served.issue_ballot(raw, date(2026, 11, 3), datetime(2026, 10, 16))
    assert ga["state_name"] == "Georgia"
    assert [(r["label"], r["candidate"]) for r in ga["races"]] == [("Senate", "Roe"), ("GA-2", None)]
    assert (ga["deadline"]["kind"], ga["deadline"]["daysLeft"]) == ("registration", 3)
    assert ga["register_link"]["url"] == "https://example.gov/register"
    assert ga["lookup"]["url"] == "https://example.gov/lookup"


def test_after_registration_closes_the_next_deadline():
    cal = {"registration": {"status": "closed", "sentence": "Registration for this election has closed."},
           "next": {"kind": "early_voting", "sentence": "Early voting ends Friday, October 30", "daysLeft": 14,
                    "closesOn": "2026-10-30"}}
    assert served._deadline(cal) == {"kind": "early_voting", "sentence": "Early voting ends Friday, October 30",
                                     "closesOn": "2026-10-30", "daysLeft": 14}
    assert served._deadline({"registration": cal["registration"]})["kind"] == "registration_closed"
    assert served._deadline({}) is None


@pytest.mark.parametrize("race_id, label", [
    ("2026-SEN-OH-SPECIAL", "Senate special"), ("2026-HOUSE-AK-0", "AK-AL"), ("2026-HOUSE-GA-02", "GA-2"),
])
def test_race_label(race_id, label):
    assert served.race_label(race_id) == label


def test_the_issue_response_carries_its_ballot(db_session, freeze_utcnow):
    freeze_utcnow(datetime(2026, 10, 16, 15))
    issue = ActionIssue(date="2026-10-16", rank=1, title="The president stumps for Roe in Georgia", summary="s",
                        is_current=True, ballot_links=json.dumps([
                            {"state": "GA", "raceId": "2026-SEN-GA", "candidate": "Roe", "via": "surname"}]))
    db_session.add(issue)
    db_session.commit()
    [ga] = _build_issue_response(issue, db_session)["ballot"]
    assert (ga["state"], ga["stateName"], ga["races"][0]["label"]) == ("GA", "Georgia", "Senate")
    assert ga["lookup"]["url"].startswith("https://")
    assert set(ga) == {"state", "stateName", "races", "deadline", "registerLink", "lookup"}
    # A row the refresh hasn't linked yet has none.
    issue.ballot_links = None
    assert _build_issue_response(issue, db_session)["ballot"] == []


def test_earlier_rows_are_linked_once(races):
    news = ActionIssue(date="2026-10-16", rank=1, title="Doe criticizes plan", summary="",
                       facts=json.dumps(["Sen. Sam Doe said it was wrong."]), is_current=True)
    draft = ActionIssue(date="2026-10-16", rank=2, title="Doe vote", summary="", is_current=True,
                        source_type="senate_roll_call_vote")
    races.add_all([news, draft])
    races.commit()
    assert link_issue_ballots(races, 2026) == 1
    assert json.loads(news.ballot_links)[0]["raceId"] == "2026-SEN-GA"
    assert draft.ballot_links is None
    assert link_issue_ballots(races, 2026) == 0
