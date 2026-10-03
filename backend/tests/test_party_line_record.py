"""A member's party-line record over the whole Congress (v6.20): breaks
toward the other party count, breaks from the flank are listed but not
counted, and each measure counts once however many times it was voted on."""

import json

from app.models import Representative, RollCall, RollCallPosition
from app.pipeline.analyze import party_line_record
from app.pipeline.analyze.party_line_record import measure_key, party_line_records
from app.pipeline.analyze.score_calculator import party_break_rate
from app.services.representative_service import get_representative_score_breakdown

# Five Republicans from the center (R0) to the flank (R4), five Democrats.
DIM1 = {"R0": 0.2, "R1": 0.5, "R2": 0.5, "R3": 0.5, "R4": 0.9, **{f"D{i}": -0.4 for i in range(5)}}


def _roll_call(db, chamber, number, question, bill_id, votes, *, date="2026-03-01", rejected=False):
    """Every Republican votes Yea and every Democrat Nay, except `votes`
    ({member: position})."""
    rc = RollCall(chamber=chamber, congress=119, session=2, number=number, date=date, question=question,
                  bill_id=bill_id, rejected=rejected)
    db.add(rc)
    db.flush()
    for member in DIM1:
        db.add(RollCallPosition(roll_call_id=rc.id, member_id=member, last_name=f"Last{member}", first_name=member,
                                party=member[0], state="TN", position=votes.get(member, "Yea" if member[0] == "R" else "Nay")))


def _members():
    return [{"bioguideId": m, "name": f"{m} Last{m}", "lastNameForVoteMatch": f"Last{m}", "state": "TN", "party": m[0],
             "votingRecord": {"effectiveParty": m[0]}} for m in DIM1]


def test_measure_key_groups_stages_of_one_measure_and_nothing_else():
    assert measure_key("On the Cloture Motion PN11-19", None) == "PN11-19"
    assert measure_key("On the Nomination PN11-19", None) == "PN11-19"
    assert measure_key("On Cloture on the Motion to Proceed S. 5", "S.5") == "S.5"
    assert measure_key("On Passage of the Bill S. 5", "S.5") == "S.5"
    assert measure_key("On Motion to Suspend the Rules and Pass, as Amended", "HR.9") == "HR.9"
    assert measure_key("On Motion to Concur in the Senate Amendment", "HR.9") == "HR.9"
    # Its own question, though it names the bill.
    assert measure_key("On the Amendment S.Amdt. 2360 to H.R. 1", "HR.1") is None
    assert measure_key("On Agreeing to the Amendment", "HR.8800") is None
    assert measure_key("On the Motion (Motion to Waive Section 425(a)(2) of the CBA re: H.R. 1)", "HR.1") is None


def test_breaks_toward_the_other_party_count_once_per_measure(db_session, monkeypatch):
    monkeypatch.setattr(party_line_record, "_member_ideal_points", lambda chamber: {"members": DIM1})
    # R0, from the center, votes with the Democrats on HR.1 twice.
    _roll_call(db_session, "house", 10, "On Passage", "HR.1", {"R0": "Nay"}, date="2026-03-01")
    _roll_call(db_session, "house", 11, "On Passage", "HR.1", {"R0": "No"}, date="2026-03-02")
    # R4, from the flank, votes down HR.2 with the Democrats.
    _roll_call(db_session, "house", 12, "On Passage", "HR.2", {"R4": "Nay"})
    # Housekeeping: no break however R0 votes.
    _roll_call(db_session, "house", 13, "On Ordering the Previous Question", "HRES.5", {"R0": "Nay"})
    # An amendment to HR.1 is its own question.
    _roll_call(db_session, "house", 14, "On Agreeing to the Amendment", "HR.1", {})
    db_session.commit()

    records = party_line_records(db_session, "house", _members() + [{"bioguideId": "GONE", "party": "R"}])
    r0, r4 = records[0], records[4]
    assert r0 == {"congress": 119, "votes": 3, "breaks": [{"rollCall": "house-119-2-11", "vote": "Nay"}],
                  "flankBreaks": []}
    assert r4["votes"] == 3 and r4["breaks"] == []
    assert r4["flankBreaks"] == [{"rollCall": "house-119-2-12", "vote": "Nay"}]
    # Not found in any roll call: no record, rather than a record of no votes.
    assert records[-1] is None

    assert party_break_rate({"partyLineRecord": r0, "keyVotes": [{"votedWithParty": False}] * 9}) == (1 / 3, 3)
    assert party_break_rate({"partyLineRecord": r4}) == (0.0, 3)


def test_a_member_of_neither_party_never_stops_the_run(db_session, monkeypatch):
    # A roll-call position whose party is neither R nor D (and not resolved
    # to a caucus) used to reach toward[party] and raise KeyError, which
    # took the whole chamber's scoring down.
    monkeypatch.setattr(party_line_record, "_member_ideal_points", lambda chamber: {"members": DIM1})
    _roll_call(db_session, "house", 20, "On Passage", "HR.3", {})
    db_session.add(RollCallPosition(roll_call_id=db_session.query(RollCall).one().id, member_id="X1",
                                    last_name="Lx", first_name="X", party="L", state="TN", position="Nay"))
    db_session.commit()
    members = _members() + [{"bioguideId": "X1", "party": "L", "votingRecord": {"effectiveParty": "L"}}]
    records = party_line_records(db_session, "house", members)
    assert records[0]["votes"] == 1
    assert records[-1] == {"congress": 119, "votes": 0, "breaks": [], "flankBreaks": []}


def test_senators_are_found_by_name_and_state_and_the_leaders_switch_is_not_a_break(db_session, monkeypatch):
    monkeypatch.setattr(party_line_record, "_member_ideal_points",
                        lambda chamber: {"members": {f"bio-{m}": d for m, d in DIM1.items()}})
    # Cloture fails; the majority leader (R0) votes Nay to be able to move
    # to reconsider. Then cloture and confirmation of the same nominee.
    _roll_call(db_session, "senate", 21, "On the Cloture Motion S. 9", "S.9", {"R0": "Nay"}, rejected=True)
    _roll_call(db_session, "senate", 22, "On the Cloture Motion PN12-3", None, {"R1": "Nay"})
    _roll_call(db_session, "senate", 23, "On the Nomination PN12-3", None, {"R1": "Nay"}, date="2026-03-05")
    # R2 shares R1's last name and state; the first name tells them apart.
    db_session.query(RollCallPosition).filter_by(member_id="R2").update({"last_name": "LastR1"})
    db_session.commit()

    members = _members()
    for m in members:
        m["bioguideId"] = f"bio-{m['bioguideId']}"  # the Senate's roll calls carry no bioguide id
    members[0]["leadershipTitle"] = "Senate Majority Leader"
    members[2]["lastNameForVoteMatch"] = "LastR1"
    records = party_line_records(db_session, "senate", members)
    assert records[0]["breaks"] == [] and records[0]["votes"] == 1
    assert records[1]["votes"] == 2
    assert records[1]["breaks"] == [{"rollCall": "senate-119-2-23", "vote": "Nay"}]
    assert records[2]["votes"] == 2 and records[2]["breaks"] == []


def test_the_breakdown_serves_each_counted_break_with_its_roll_call(db_session):
    _roll_call(db_session, "house", 10, "On Passage", "HR.1", {"R0": "Nay"})
    _roll_call(db_session, "house", 12, "On Passage", "HR.2", {"R4": "Nay"})
    record = {"congress": 119, "votes": 40, "breaks": [{"rollCall": "house-119-2-10", "vote": "Nay"}],
              "flankBreaks": [{"rollCall": "house-119-2-12", "vote": "Nay"}]}
    db_session.add(Representative(id="r0", bioguide_id="R0", name="R0 LastR0", state="TN", district=2, party="R",
                                  party_line_record=json.dumps(record)))
    db_session.commit()

    facts = get_representative_score_breakdown(db_session, "r0")["constituentAlignment"]["facts"]
    assert (facts["partyVotes"], facts["breaks"], facts["flankBreaks"]) == (40, 1, 1)
    assert facts["breakVotes"][0]["vote"] == "Nay"
    assert facts["breakVotes"][0]["rollCall"]["parties"][0] == {"party": "R", "yea": 4, "nay": 1, "present": 0, "notVoting": 0}
    assert facts["flankBreakVotes"][0]["rollCall"]["billId"] == "HR.2"


def test_a_successor_of_the_same_surname_gets_only_their_own_votes(db_session, monkeypatch):
    """Darline Graham took Lindsey Graham's seat after his death; matched by
    last name and state, every roll call he cast in the Congress was hers
    (live, 2026-10-03). The roll calls' LIS ids tell them apart."""
    monkeypatch.setattr(party_line_record, "_member_ideal_points", lambda chamber: {"members": {}})
    for number, (first, lis) in enumerate((("Lindsey", "S293"), ("Lindsey", "S293"), ("Darline", "S441")), start=1):
        rc = RollCall(chamber="senate", congress=119, session=2, number=number, date=f"2026-03-0{number}",
                      question="On Passage", bill_id=f"S.{number}")
        db_session.add(rc)
        db_session.flush()
        db_session.add(RollCallPosition(roll_call_id=rc.id, member_id=lis, last_name="Graham", first_name=first,
                                        party="R", state="SC", position="Yea"))
        for i in range(5):
            db_session.add(RollCallPosition(roll_call_id=rc.id, member_id=f"D{i}", last_name=f"Dem{i}",
                                            first_name="X", party="D", state="NY", position="Nay"))
            db_session.add(RollCallPosition(roll_call_id=rc.id, member_id=f"R{i}", last_name=f"Rep{i}",
                                            first_name="X", party="R", state="TX", position="Yea"))
    db_session.commit()
    (record,) = party_line_records(db_session, "senate", [{
        "bioguideId": "G000600", "name": "Darline Graham", "lastNameForVoteMatch": "Graham", "state": "SC",
        "party": "R", "votingRecord": {"effectiveParty": "R"},
    }])
    assert record["votes"] == 1, record
