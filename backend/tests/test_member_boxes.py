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


def _bill(bill_id, bill_type, stage, introduced, latest, title):
    return {"billId": bill_id, "billType": bill_type, "stage": stage, "introducedDate": introduced,
            "latestActionDate": latest, "title": title}


def test_bills_box_names_the_bill_furthest_along_not_the_strips_count():
    """How many bills got how far is the peer strip's and the stage chart's;
    the box names the bill, so no "N of M" restates them."""
    bills = [
        _bill("S.1", "S", "REFERRED", "2025-01-03", "2025-01-03", "A referred bill"),
        _bill("S.7", "S", "PASSED_CHAMBER", "2025-02-01", "2025-06-01", "Older Act"),
        _bill("S.9", "S", "PASSED_CHAMBER", "2025-03-01", "2025-09-10", "Clean Water Act."),
        _bill("SRES.4", "SRES", "ENACTED", "2025-01-05", "2025-01-06", "A resolution, not a bill"),
    ]
    box = mb.bills_box(bills, "senate")
    assert box["headline"] == "S. 9 passed the Senate"
    assert box["dek"] == "Clean Water Act. Latest action September 10, 2025."
    assert box["figure"] is None and " of " not in box["headline"]
    law = mb.bills_box([_bill("HR.5", "HR", "ENACTED", "2025-01-03", "2025-08-01", "Roads Act")], "house")
    assert law["headline"] == "H.R. 5 became law"
    quiet = mb.bills_box([bills[0], _bill("S.3", "S", "REFERRED", "2025-04-01", "2025-04-01", "Newer")], "senate")
    assert quiet["headline"] == "Newest bill: S. 3"
    assert quiet["dek"] == "Newer. Introduced April 1, 2025."
    assert mb.bills_box([bills[3]], "house")["headline"] == "No bills sponsored this Congress"


def _break(date, nomination=False, bill_id=None):
    return {"vote": "Yea", "rollCall": {"date": date, "billId": bill_id,
                                        "nominationVote": {"kind": "confirm"} if nomination else None}}


def test_votes_box_tells_what_the_breaks_were_on_not_how_many():
    """The count against the seat is the peer strip's; the box leads with
    the breaks themselves."""
    listed = [_break("2026-06-23", nomination=True), _break("2025-02-04", nomination=True),
              _break("2026-01-30", bill_id="HR.7148"), _break("2025-10-09")]
    box = mb.votes_box({"finding": "Breaks ... 3 times ...",
                        "facts": {"partyVotes": 499, "breaks": 4, "breakVotes": listed}})
    assert box["headline"] == "2 breaks were on nominations"
    assert box["dek"] == "1 on a bill or resolution, 1 on another question. Newest on June 23, 2026."
    assert "499" not in box["headline"] + box["dek"] and "times" not in box["dek"]
    # All of one kind: the kind's count would be the strip's count again.
    same = [_break("2026-07-15", bill_id="HR.8595"), _break("2026-05-01", bill_id="HR.1")]
    same[1]["rollCall"]["amendment"] = "H.Amdt. 9"
    mixed = mb.votes_box({"facts": {"partyVotes": 322, "breaks": 2, "breakVotes": same}})
    assert mixed["headline"] == "1 break was on an amendment"
    assert mixed["dek"] == "1 on a bill or resolution. Newest on July 15, 2026."
    one_kind = mb.votes_box({"facts": {"partyVotes": 322, "breaks": 2, "breakVotes": same[:1] * 2}})
    assert (one_kind["headline"], one_kind["figure"]) == ("Every break was on bills and resolutions", None)
    one = mb.votes_box({"facts": {"partyVotes": 10, "breaks": 1, "breakVotes": [_break("2025-05-01")]}})
    assert (one["headline"], one["dek"]) == ("The break was on another question", "Newest on May 1, 2025.")
    none = mb.votes_box({"facts": {"partyVotes": 493, "breaks": 0, "breakVotes": []}})
    assert none["headline"] == "No votes against the party to list" and none["figure"] is None
    flank = mb.votes_box({"facts": {"partyVotes": 493, "breaks": 0, "breakVotes": [],
                                    "flankBreakVotes": [_break("2025-05-01")]}})
    assert flank["headline"] == "1 vote against the party from its flank, not counted"
    assert mb.votes_box({"facts": {"partyVotes": 12, "breaks": 3}})["headline"] == (
        "Votes against the party, as recorded")
    assert mb.votes_box({"facts": {"partyVotes": 0}})["headline"] == "No party-line votes on record yet"


def test_money_box_names_the_largest_donor_not_the_strips_shares():
    funding = {"totalContributions": 13_000_000, "topDonors": [
        {"name": "Doe for Senate", "total": 90_000.0, "type": "CandidateAffiliated"},
        {"name": "DSCC", "total": 51_200.0, "type": "Party/Ideological"}]}
    money = mb.money_box(funding, 2022)
    assert (money["kicker"], money["headline"]) == ("Money · 2022 election", "$13.0M in contributions")
    assert money["dek"] == "Largest donor: DSCC, $51,200."
    employer = mb.money_box({"totalContributions": 9, "topDonors": [
        {"name": "Google", "total": 28_232.0, "type": "Org/Employees"}]}, 2024)
    assert employer["dek"] == "Largest donor: Google employees, $28,232."
    bare = mb.money_box({"totalContributions": 5}, None)
    assert bare["kicker"] == "Money · most recent completed election" and bare["dek"] is None


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
    assert box["dek"] == "Filed August 8, 2026. 1 asset with no stated value, not charted."
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


def test_a_thin_record_draws_no_verdict(db_session):
    """Under the measures page's minimum record, no comparison, as there."""
    db_session.add(PresidentialSupport(chamber="senate", member_id="new-doe", congress=119, party="D",
                                       with_president=0, votes=4, expected=0.9, computed_at=datetime(2026, 10, 21)))
    db_session.commit()
    box = mb.president_box(db_session, "new-doe")
    assert box["verdict"] is None and box["dek"] == "0% of the counted votes."
