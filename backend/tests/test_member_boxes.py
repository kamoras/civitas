"""A member profile's boxes (services/member_boxes.py): every title, line
and figure the page shows, written by the backend from stored fields."""

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.api.politicians import get_politician
from app.models import PresidentialSupport, Senator, StockTrade
from app.services import member_boxes as mb


def test_committees_fold_to_one_line_with_the_posts_held():
    out = mb.committees_box([
        {"chamber": "senate", "committeeName": "Senate Committee on Finance", "title": "Ranking Member"},
        {"chamber": "senate", "committeeName": "Senate Committee on the Budget", "title": None},
        {"chamber": "senate", "committeeName": "Senate Select Committee on Intelligence", "title": None},
        {"chamber": "joint", "committeeName": "Joint Committee on Taxation", "title": None},
    ])
    assert out["line"] == "4 committees · Ranking Member, Finance"
    assert [c["name"] for c in out["items"]] == ["Finance", "Budget", "Select Committee on Intelligence",
                                                "Joint Committee on Taxation"]
    assert mb.committees_box([])["line"] == "No committee seats on file"


def test_bills_box_counts_the_furthest_stage_as_the_finding_does():
    dim = {"finding": "Of 89 bills sponsored this Congress, 2 passed the Senate; none has become law.",
           "facts": {"bills": 89, "billsByStage": [76, 4, 7, 2, 0]}}
    box = mb.bills_box(dim, "senate")
    assert box["headline"] == "2 of 89 passed the Senate"
    assert box["figure"] == {"type": "stat", "value": 2, "text": "2 of 89", "caption": "passed the Senate"}
    assert box["dek"] == dim["finding"]
    law = mb.bills_box({"facts": {"bills": 10, "billsByStage": [5, 1, 1, 2, 1]}}, "house")
    assert law["headline"] == "1 of 10 became law"
    assert mb.bills_box({"facts": {"bills": 0, "billsByStage": [0] * 5}}, "house")["figure"] is None


def test_votes_and_money_boxes():
    votes = mb.votes_box({"finding": "f", "facts": {"partyVotes": 493, "breaks": 0}})
    assert votes["headline"] == "0 of 493 party-line votes against the party"
    assert mb.votes_box({"facts": {"partyVotes": 12, "breaks": None}})["headline"] == "12 party-line votes on record"
    money = mb.money_box({"totalContributions": 13_000_000}, 2022, {"finding": "Gifts of $200 or less ..."})
    assert (money["kicker"], money["headline"]) == ("Money · 2022 election", "$13.0M in contributions")
    assert mb.money_box({"totalContributions": 5}, None, None)["kicker"] == "Money · most recent completed election"


def test_positions_count_the_areas_leaning_with_the_members_party():
    depth = {"leanSentence": "Votes lean Democratic.", "policyBreakdown": [
        {"alignment": "D"}, {"alignment": "D"}, {"alignment": "R"}, {"alignment": "bipartisan"}]}
    assert mb.positions_box(depth, "D")["headline"] == "2 of 4 areas on the party line"
    assert mb.positions_box(depth, "I") is None


def test_approval_leads_with_the_members_own_party_in_the_seat():
    approval = {"byParty": [{"party": "D", "approve": 0.879}, {"party": "R", "approve": 0.115},
                            {"party": "I", "approve": None}]}
    senate = mb.approval_box(approval, "D", "Oregon", None)
    assert senate["headline"] == "88% of Oregon Democrats approve"
    assert senate["dek"] == "Democrats 88%, Republicans 12%."
    house = mb.approval_box(approval, "R", "Ohio", 9)
    assert house["headline"] == "12% of Republicans in Ohio's 9th district approve"
    assert mb.approval_box({"byParty": []}, "D", "Oregon", None) is None


def test_holdings_box_states_the_total_as_the_schema_prints_it():
    h = SimpleNamespace(available=True, parsed=True, report_label="2025 annual report", filed_date="2026-08-08",
                        holdings_count=179, unvalued_count=1, total_low=21_027_153.0, total_shown="$21.0M+")
    box = mb.holdings_box(h)
    assert box["headline"] == "$21.0M+ disclosed across 179 assets"
    assert box["dek"] == "Filed 2026-08-08. 1 asset with no stated value, not charted."
    assert mb.holdings_box(SimpleNamespace(available=False)) is None


def test_trades_box():
    assert mb.trades_box(1, 1, "senate")["headline"] == "1 trade disclosed, 1 late"
    assert mb.trades_box(0, 0, "house")["headline"] == "No trades disclosed"


def test_a_senators_seat_says_when_it_is_next_up(monkeypatch, db_session):
    election = SimpleNamespace(cycle=2026)
    monkeypatch.setattr(mb, "active_election", lambda db: election)
    monkeypatch.setattr(mb, "elected_congress_sits", lambda cycle: False)
    monkeypatch.setattr(mb, "class_up", lambda y: {2026: 2, 2028: 3, 2030: 1}.get(y))
    senator = SimpleNamespace(bioguide_id="X000001", district=None)
    monkeypatch.setattr(mb, "senator_class", lambda b: 3)
    assert mb.ballot_line(db_session, "senate", senator, None, None) == {
        "line": "Seat next on the ballot in 2028", "raceId": None}
    on = {"raceId": "2026-S-OR", "cycleYear": 2026, "office": "S", "district": None}
    assert mb.ballot_line(db_session, "senate", senator, on, None)["line"] == "On the 2026 ballot for re-election"
    seat = {"raceId": "2026-H-OR-3", "cycleYear": 2026, "status": "notNominee"}
    house = SimpleNamespace(bioguide_id="X", district=3)
    assert mb.ballot_line(db_session, "house", house, None, seat)["line"] == (
        "Seat on the 2026 ballot; not a nominee for it")
    assert mb.ballot_line(db_session, "house", house, None, None) is None


@pytest.fixture
def senator(db_session):
    db_session.add(Senator(
        id="ron-doe", bioguide_id="D000001", name="Ron Doe", state="OR", party="D", years_in_office=30,
        committees=json.dumps([{"chamber": "senate", "committeeName": "Senate Committee on Finance",
                                "title": "Ranking Member"}]),
        total_raised=14_000_000, total_contributions=13_000_000, total_from_pacs=3_380_000,
        small_donor_percentage=13.0, funding_election_year=2022,
    ))
    db_session.add(PresidentialSupport(chamber="senate", member_id="ron-doe", congress=119, party="D",
                                       with_president=4, votes=27, expected=0.206, computed_at=datetime(2026, 10, 21)))
    db_session.add(StockTrade(senator_id="ron-doe", ticker=None, asset_name="Acme", owner="spouse",
                              transaction_type="exchange", transaction_date="2025-04-30",
                              disclosure_date="2026-08-08", days_to_disclose=465, amount_low=100001.0,
                              amount_high=250000.0, industry="UNCLASSIFIED", source_url="https://example.com",
                              filing_id="f1", parse_confidence="text"))
    db_session.commit()


def test_the_profile_serves_every_box(senator, db_session):
    body = json.loads(get_politician("ron-doe", db_session).body)
    boxes = body["boxes"]
    assert boxes["committees"]["line"] == "1 committee · Ranking Member, Finance"
    assert boxes["money"]["kicker"] == "Money · 2022 election"
    assert boxes["money"]["headline"] == "$13.0M in contributions"
    assert boxes["fundingElectionYear"] == 2022 == body["scorecard"]["funding"]["electionYear"]
    assert boxes["trades"]["headline"] == "1 trade disclosed, 1 late"
    president = boxes["president"]
    assert president["headline"] == "4 of 27 votes on the president's stated side"
    assert president["dek"] == "15%, against 21% for the same party in seats that lean the same way: about as expected."
    assert president["verdict"] == "about as expected"
    assert president["finding"].startswith("Voted on the president's stated side on 4 of 27 counted votes (15%).")
    assert boxes["record"]["headline"] == "No documents on record"
    for key in ("votes", "bills"):
        assert boxes[key]["kicker"].endswith("this Congress")
    assert "—" not in json.dumps(boxes)


def test_no_template_names_a_member_with_a_pronoun(senator, db_session):
    text = json.dumps(json.loads(get_politician("ron-doe", db_session).body)["boxes"]).lower()
    for pronoun in (" he ", " she ", " his ", " her ", " they ", " their "):
        assert pronoun not in text
