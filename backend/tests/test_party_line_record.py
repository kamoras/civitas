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


def test_a_thin_records_position_barely_moves_the_direction_of_a_break(db_session, monkeypatch):
    """v6.27: R0 (center) and R4 (flank) break together. Read at full weight
    the defectors average 0.55, flank-side of the party's 0.51; but R4's
    position rests on 2 roll calls, so it counts at 2 / (2 + 24) and the
    break is toward the Democrats. Ten Republicans, so two defectors still
    leave a party-line vote."""
    roster = {**DIM1, **{f"R{i}": 0.5 for i in range(5, 10)}}
    monkeypatch.setitem(globals(), "DIM1", roster)
    full = {m: 500 for m in roster}
    section = {"members": roster, "votes": full, "reliability": {"n0": 24}}
    _roll_call(db_session, "house", 30, "On Passage", "HR.5", {"R0": "Nay", "R4": "Nay"})
    db_session.commit()

    monkeypatch.setattr(party_line_record, "_member_ideal_points", lambda chamber: section)
    assert party_line_records(db_session, "house", _members())[0]["flankBreaks"] != []

    section["votes"] = {**full, "R4": 2}
    records = party_line_records(db_session, "house", _members())
    assert records[0]["breaks"] == [{"rollCall": "house-119-2-30", "vote": "Nay"}]
    assert records[4]["breaks"] == [{"rollCall": "house-119-2-30", "vote": "Nay"}]


def test_another_congresss_positions_still_set_a_breaks_direction(db_session, monkeypatch):
    """The flank rule needs only which side of their party the defectors sit,
    and positions carry across Congresses: a section from another Congress
    still classifies these roll calls (stale beats punitive), so R4's break
    from the flank stays a flank break rather than counting."""
    monkeypatch.setattr(party_line_record, "_member_ideal_points",
                        lambda chamber: {"members": DIM1, "congress": 120})
    _roll_call(db_session, "house", 31, "On Passage", "HR.6", {"R4": "Nay"})
    db_session.commit()
    record = party_line_records(db_session, "house", _members())[4]
    assert record["breaks"] == [] and record["flankBreaks"] != []


def test_a_lone_thin_defector_is_placed_by_the_last_congresss_full_record(db_session, monkeypatch):
    """A lone defector's side is its own position's, whatever its weight, so
    early in a Congress the last Congress's full record ("prior") decides it:
    R4's 2-vote position reads center-side (0.3), its full record flank-side
    (0.9). Without the prior the break counts; with it, it is a flank break.
    A full current position is kept over the prior."""
    current = {**DIM1, "R4": 0.3}
    section = {"members": current, "votes": {**{m: 500 for m in DIM1}, "R4": 2}, "reliability": {"n0": 24},
               "congress": 119}
    monkeypatch.setattr(party_line_record, "_member_ideal_points", lambda chamber: section)
    _roll_call(db_session, "house", 32, "On Passage", "HR.7", {"R4": "Nay"})
    db_session.commit()
    assert party_line_records(db_session, "house", _members())[4]["breaks"] != []

    section["prior"] = {"congress": 118, "members": DIM1, "votes": {m: 500 for m in DIM1}, "reliability": {"n0": 24}}
    record = party_line_records(db_session, "house", _members())[4]
    assert record["breaks"] == [] and record["flankBreaks"] != []

    section["votes"]["R4"] = 500
    assert party_line_records(db_session, "house", _members())[4]["breaks"] != []

    # With a full record's count known, a full last record decides until the
    # new record reaches the switch (a full record by default, and without one),
    # whatever the weights, and the new one from then.
    section["reliability"] = {"n0": 24, "reference_votes": 200}
    section["votes"]["R4"] = 199
    assert party_line_records(db_session, "house", _members())[4]["flankBreaks"] != []
    section["votes"]["R4"] = 200
    assert party_line_records(db_session, "house", _members())[4]["breaks"] != []
    section["reliability"]["prior_until_votes"] = 90
    section["votes"]["R4"] = 89
    assert party_line_records(db_session, "house", _members())[4]["flankBreaks"] != []
    section["votes"]["R4"] = 90
    assert party_line_records(db_session, "house", _members())[4]["breaks"] != []
    del section["votes"]["R4"]  # no count reported: read as thin
    assert party_line_records(db_session, "house", _members())[4]["flankBreaks"] != []
    # ... even when a position with no count weighs something on its own: the
    # full last record still decides.
    section["reliability"]["uncounted_weight"] = 0.2
    assert party_line_records(db_session, "house", _members())[4]["flankBreaks"] != []
    del section["reliability"]["uncounted_weight"]
    # A last record of exactly a full record's count is full.
    section["votes"]["R4"] = 10
    section["prior"]["votes"]["R4"] = 200
    assert party_line_records(db_session, "house", _members())[4]["flankBreaks"] != []
    section["prior"]["votes"]["R4"] = 500
    del section["votes"]["R4"]
    # Only a full last record was measured against a thin new one: a thin
    # last record (here under reference_votes) leaves this Congress's...
    section["reliability"]["reference_votes"] = 600
    section["votes"]["R4"] = 2
    assert party_line_records(db_session, "house", _members())[4]["breaks"] != []
    # ...unless this Congress's counts for nothing yet (no votes).
    section["votes"]["R4"] = 0
    assert party_line_records(db_session, "house", _members())[4]["flankBreaks"] != []
    # A section carried from the last Congress (the roll calls' is newer)
    # brings an older prior, which isn't read.
    section["congress"] = 118
    assert party_line_records(db_session, "house", _members())[4]["breaks"] != []

def test_a_member_who_switched_parties_is_never_read_on_the_last_congress(db_session, monkeypatch):
    """A switcher's last record was cast in their old party, so the prior
    never decides their side: neither over a thin record since the switch
    nor in place of a missing one."""
    current = {**DIM1, "R4": 0.3}
    section = {"members": current, "votes": {**{m: 500 for m in DIM1}, "R4": 2},
               "reliability": {"n0": 24, "reference_votes": 200}, "congress": 119,
               "prior": {"congress": 118, "members": DIM1, "votes": {m: 500 for m in DIM1},
                         "reliability": {"n0": 24}}}
    monkeypatch.setattr(party_line_record, "_member_ideal_points", lambda chamber: section)
    _roll_call(db_session, "house", 33, "On Passage", "HR.8", {"R4": "Nay"})
    db_session.commit()
    assert party_line_records(db_session, "house", _members())[4]["flankBreaks"] != []
    section["switched"] = ["R4"]
    assert party_line_records(db_session, "house", _members())[4]["breaks"] != []
    # With no position since the switch, the old one isn't read either: the
    # rule falls back as for any member with none.
    section["members"] = {m: d for m, d in current.items() if m != "R4"}
    del section["votes"]["R4"]
    record = party_line_records(db_session, "house", _members())[4]
    section["switched"] = []
    assert record != party_line_records(db_session, "house", _members())[4]
    # A switch between the two Congresses (a new id in each, so one row in
    # each section): told by the parties the sections record.
    section["members"], section["votes"]["R4"] = current, 2
    section["parties"] = {m: "R" if m.startswith("R") else "D" for m in DIM1}
    section["prior"]["parties"] = {**section["parties"], "R4": "D"}
    assert party_line_records(db_session, "house", _members())[4]["breaks"] != []
    section["prior"]["parties"]["R4"] = "R"
    assert party_line_records(db_session, "house", _members())[4]["flankBreaks"] != []
    # A change to or from a code that isn't a major party (an independent
    # caucusing as before) is a switch too.
    section["parties"]["R4"] = "328"
    assert party_line_records(db_session, "house", _members())[4]["breaks"] != []


def test_a_position_cast_in_the_other_party_stays_out_of_its_mean(db_session, monkeypatch):
    """A last-Congress position recorded under the other major party (R0
    was a Democrat then) is not the Republicans' then: it stays out of their
    mean, so it can't pull a thin member's side (R3's, read on the prior)."""
    section = {"members": DIM1, "votes": {**{m: 500 for m in DIM1}, "R3": 2},
               "reliability": {"n0": 24, "reference_votes": 200}, "congress": 119,
               "parties": {m: m[0] for m in DIM1},
               "prior": {"congress": 118, "members": {**DIM1, "R0": -3.0}, "votes": {m: 500 for m in DIM1},
                         "parties": {m: m[0] for m in DIM1}, "reliability": {"n0": 24}}}
    monkeypatch.setattr(party_line_record, "_member_ideal_points", lambda chamber: section)
    _roll_call(db_session, "house", 34, "On Passage", "HR.9", {"R3": "Nay"})
    db_session.commit()
    # Counted in the mean, R0's -3.0 puts R3's 0.5 on the flank side.
    assert party_line_records(db_session, "house", _members())[3]["flankBreaks"] != []
    section["prior"]["parties"]["R0"] = "D"
    assert party_line_records(db_session, "house", _members())[3]["breaks"] != []


def test_a_position_cast_in_the_other_party_is_never_read(db_session, monkeypatch):
    """Before the new Congress's section is in, every member is read on the
    last one; a position it records under the other major party (R4 was a
    Democrat then) is not read for the member even there."""
    section = {"members": DIM1, "votes": {m: 500 for m in DIM1}, "reliability": {"n0": 24}, "congress": 118,
               "parties": {m: m[0] for m in DIM1}}
    monkeypatch.setattr(party_line_record, "_member_ideal_points", lambda chamber: section)
    _roll_call(db_session, "house", 35, "On Passage", "HR.10", {"R4": "Nay"})
    db_session.commit()
    assert party_line_records(db_session, "house", _members())[4]["flankBreaks"] != []
    section["parties"]["R4"] = "D"
    assert party_line_records(db_session, "house", _members())[4]["breaks"] != []


def test_an_independent_stays_in_the_mean_of_the_party_they_caucus_with(db_session, monkeypatch):
    """Voteview codes an independent 328; caucusing with the Democrats, they
    are no other-party record and stay in the Democrats' mean (D0 here,
    far right, pulls it past D1's thin record, read on its prior)."""
    prior = {**DIM1, "D0": 3.0, "D1": -0.35}
    section = {"members": DIM1, "votes": {**{m: 500 for m in DIM1}, "D1": 2},
               "reliability": {"n0": 24, "reference_votes": 200}, "congress": 119,
               "parties": {**{m: m[0] for m in DIM1}, "D0": "328"},
               "prior": {"congress": 118, "members": prior, "votes": {m: 500 for m in DIM1},
                         "parties": {**{m: m[0] for m in DIM1}, "D0": "328"}, "reliability": {"n0": 24}}}
    monkeypatch.setattr(party_line_record, "_member_ideal_points", lambda chamber: section)
    _roll_call(db_session, "house", 36, "On Passage", "HR.11", {"D1": "Yea"})
    db_session.commit()
    assert party_line_records(db_session, "house", _members())[6]["flankBreaks"] != []


def test_a_successor_of_the_same_surname_gets_only_their_own_votes(db_session, monkeypatch):
    """An appointee took a senator's seat; sharing the surname and state,
    every roll call the predecessor cast in the Congress was credited to the
    appointee (live, 2026-10-03). The roll calls' LIS ids tell them apart."""
    monkeypatch.setattr(party_line_record, "_member_ideal_points", lambda chamber: {"members": {}})
    for number, (first, lis) in enumerate((("Lowell", "S293"), ("Lowell", "S293"), ("Delia", "S441")), start=1):
        rc = RollCall(chamber="senate", congress=119, session=2, number=number, date=f"2026-03-0{number}",
                      question="On Passage", bill_id=f"S.{number}")
        db_session.add(rc)
        db_session.flush()
        db_session.add(RollCallPosition(roll_call_id=rc.id, member_id=lis, last_name="Whitfield", first_name=first,
                                        party="R", state="SC", position="Yea"))
        for i in range(5):
            db_session.add(RollCallPosition(roll_call_id=rc.id, member_id=f"D{i}", last_name=f"Dem{i}",
                                            first_name="X", party="D", state="NY", position="Nay"))
            db_session.add(RollCallPosition(roll_call_id=rc.id, member_id=f"R{i}", last_name=f"Rep{i}",
                                            first_name="X", party="R", state="TX", position="Yea"))
    db_session.commit()
    (record,) = party_line_records(db_session, "senate", [{
        "bioguideId": "G000600", "name": "Delia Whitfield", "lastNameForVoteMatch": "Whitfield", "state": "SC",
        "party": "R", "votingRecord": {"effectiveParty": "R"},
    }])
    assert record["votes"] == 1, record


def test_each_congresss_positions_are_read_from_their_own_partys_mean(db_session, monkeypatch):
    """The whole Republican conference sits 0.3 further right in the last
    Congress's positions. R4 (2 votes now) is read from those, the rest from
    this Congress's: R4's last position, 0.6, is right of every current
    Republican but left of its own Congress's party mean (0.82), so its break
    is toward the Democrats, not from the flank."""
    current = {**DIM1, "R0": 0.4, "R1": 0.5, "R2": 0.5, "R3": 0.5, "R4": 0.45}
    prior = {**DIM1, "R0": 0.9, "R1": 0.8, "R2": 0.8, "R3": 0.8, "R4": 0.6}
    full = {m: 500 for m in DIM1}
    section = {"members": current, "votes": {**full, "R4": 2}, "reliability": {"n0": 24}, "congress": 119,
               "prior": {"congress": 118, "members": prior, "votes": full, "reliability": {"n0": 24}}}
    monkeypatch.setattr(party_line_record, "_member_ideal_points", lambda chamber: section)
    _roll_call(db_session, "house", 33, "On Passage", "HR.8", {"R4": "Nay"})
    db_session.commit()
    assert party_line_records(db_session, "house", _members())[4]["breaks"] != []


def test_a_lone_defector_is_read_against_the_party_center_without_colleagues(db_session, monkeypatch):
    """Passed one senator, no colleague's roll-call vote can be tied to a
    position. A lone defector is still placed: R0 breaking from the center
    counts and R4 breaking from the flank doesn't, each read against the
    party's own center in the section, taken over the chamber's stored
    senators. (The Senate pipeline passes the whole roster on a filtered
    run, so breaks with several defectors are read as in a full run.)"""
    from app.models import Senator
    monkeypatch.setattr(party_line_record, "_member_ideal_points",
                        lambda chamber: {"members": {f"bio-{m}": d for m, d in DIM1.items()}})
    for m in DIM1:
        db_session.add(Senator(id=f"S-{m}", bioguide_id=f"bio-{m}", name=f"{m} Last{m}", state="TN", party=m[0]))
    # Stored but not passed: they mustn't be read back in as departed senators.
    monkeypatch.setattr(party_line_record, "_departed_senators", lambda *a: [])
    _roll_call(db_session, "senate", 24, "On Passage", "S.24", {"R0": "Nay"})
    _roll_call(db_session, "senate", 25, "On Passage", "S.25", {"R4": "Nay"})
    db_session.commit()
    members = _members()
    for m in members:
        m["bioguideId"] = f"bio-{m['bioguideId']}"
    full = party_line_records(db_session, "senate", members)
    for i, rc in ((0, "senate-119-2-24"), (4, "senate-119-2-25")):
        (alone,) = party_line_records(db_session, "senate", [members[i]])
        assert alone["breaks"] == full[i]["breaks"] and alone["flankBreaks"] == full[i]["flankBreaks"]
    assert full[0]["breaks"] == [{"rollCall": "senate-119-2-24", "vote": "Nay"}]
    assert full[4]["flankBreaks"] == [{"rollCall": "senate-119-2-25", "vote": "Nay"}]


def test_members_passed_without_a_voting_record_take_their_stored_caucus(db_session, monkeypatch):
    """A filtered Senate run passes the rest of the chamber as roster
    entries with no voting record. An independent among them reads as the
    party they caucus with (stored), as in a full run: here I0, caucusing
    with the Democrats, breaks with D0, and D0's break reads the same."""
    from app.models import Senator
    dims = {"D0": -0.5, **{f"D{i}": -0.4 for i in range(1, 6)}, "I0": -0.05, **{f"R{i}": 0.5 for i in range(6)}}
    monkeypatch.setattr(party_line_record, "_member_ideal_points",
                        lambda chamber: {"members": {f"bio-{m}": d for m, d in dims.items()}})
    for m in dims:
        db_session.add(Senator(id=f"S-{m}", bioguide_id=f"bio-{m}", name=f"{m} Last{m}", state="TN", party=m[0],
                               caucus_party="D" if m == "I0" else None))
    rc = RollCall(chamber="senate", congress=119, session=2, number=40, date="2026-03-01", question="On Passage",
                  bill_id="S.40")
    db_session.add(rc)
    db_session.flush()
    for m in dims:
        vote = "Yea" if m in ("D0", "I0") or m.startswith("R") else "Nay"
        db_session.add(RollCallPosition(roll_call_id=rc.id, member_id=m, last_name=f"Last{m}", first_name=m,
                                        party=m[0], state="TN", position=vote))
    db_session.commit()

    def member(m, scored):
        out = {"bioguideId": f"bio-{m}", "name": f"{m} Last{m}", "lastNameForVoteMatch": f"Last{m}", "state": "TN",
               "party": m[0]}
        return {**out, "votingRecord": {"effectiveParty": "D" if m == "I0" else m[0]}} if scored else out
    full = party_line_records(db_session, "senate", [member(m, True) for m in dims])
    filtered = party_line_records(db_session, "senate", [member("D0", True)] + [member(m, False) for m in dims if m != "D0"])
    assert filtered[0]["breaks"] == full[0]["breaks"] and filtered[0]["flankBreaks"] == full[0]["flankBreaks"]


def test_a_section_a_congress_ahead_reads_the_roll_calls_congress_from_its_prior(db_session, monkeypatch):
    """After Jan 3 the section can be the new Congress's before its first
    roll call is stored: the 119th roll calls are then read on the prior it
    keeps, the 119th's own positions (R4 flank-side there)."""
    current = {**DIM1, "R4": 0.3}
    section = {"members": current, "reliability": {"n0": 24, "reference_votes": 200}, "congress": 120,
               "prior": {"congress": 119, "members": DIM1, "votes": {m: 500 for m in DIM1},
                         "reliability": {"n0": 24, "reference_votes": 200}}}
    monkeypatch.setattr(party_line_record, "_member_ideal_points", lambda chamber: section)
    _roll_call(db_session, "house", 41, "On Passage", "HR.41", {"R4": "Nay"})
    db_session.commit()
    record = party_line_records(db_session, "house", _members())[4]
    assert record["breaks"] == [] and record["flankBreaks"] != []
    # A member only the newer section has (sworn in since) is read from it.
    section["prior"]["members"] = {m: d for m, d in DIM1.items() if m != "R4"}
    section["members"]["R4"], section["votes"] = 0.9, {**{m: 500 for m in DIM1}, "R4": 40}
    record = party_line_records(db_session, "house", _members())[4]
    assert record["flankBreaks"] != []
    del section["members"]["R4"]
    assert party_line_records(db_session, "house", _members())[4]["breaks"] != []


def test_a_house_independents_stored_caucus_reads_over_the_roll_calls_party(db_session, monkeypatch):
    """The House's roll calls give every member's party, an independent's as
    "I"; the stored caucus party reads over it. R9 ("I" on the roll calls,
    caucusing with the Republicans, sitting far right) breaks with R4, who
    sits center-side: read as a Republican, R9 pulls the defectors' mean to
    the flank, so R4's break is a flank break; read as "I", R9 is left out
    and R4's break counts."""
    from app.models import Representative
    dims = {**DIM1, "R4": 0.3, "R9": 2.0}
    monkeypatch.setattr(party_line_record, "_member_ideal_points", lambda chamber: {"members": dims})
    rc = RollCall(chamber="house", congress=119, session=2, number=50, date="2026-03-01", question="On Passage",
                  bill_id="HR.50")
    db_session.add(rc)
    db_session.flush()
    for m in dims:
        party = "I" if m == "R9" else m[0]
        vote = "Nay" if m in ("R4", "R9") else ("Yea" if m[0] == "R" else "Nay")
        db_session.add(RollCallPosition(roll_call_id=rc.id, member_id=m, last_name=f"Last{m}", first_name=m,
                                        party=party, state="TN", position=vote))
    db_session.commit()
    alone = party_line_records(db_session, "house", _members())[4]
    db_session.add(Representative(id="H-R9", bioguide_id="R9", name="R9 LastR9", state="TN", district=9,
                                  party="I", caucus_party="R"))
    db_session.commit()
    with_caucus = party_line_records(db_session, "house", _members())[4]
    assert alone["breaks"] != [] and with_caucus["flankBreaks"] != []


def test_a_house_roll_calls_label_reads_its_own_parties(db_session, monkeypatch):
    """The label (a party-line roll call or not) reads the roll call's own
    parties, as its stored partySplit does. All Republicans vote Yea; 2 of 7
    Democrats join them (29%, a party line), and so does an independent ("I"
    on the roll call) who caucuses with the Democrats. Counted as a Democrat
    the independent would make it 3 of 8 (38%, no party line); read by the
    roll call it isn't, so the Democrats who joined broke with their party."""
    from app.models import Representative
    dims = {**{f"R{i}": 0.5 for i in range(5)}, **{f"D{i}": -0.4 for i in range(7)}, "I0": -0.3}
    monkeypatch.setattr(party_line_record, "_member_ideal_points", lambda chamber: {"members": dims})
    db_session.add(Representative(id="H-I0", bioguide_id="I0", name="I0 LastI0", state="TN", district=9,
                                  party="I", caucus_party="D"))
    rc = RollCall(chamber="house", congress=119, session=2, number=60, date="2026-03-01", question="On Passage",
                  bill_id="HR.60")
    db_session.add(rc)
    db_session.flush()
    for m in dims:
        vote = "Yea" if m.startswith("R") or m in ("D0", "D1", "I0") else "Nay"
        db_session.add(RollCallPosition(roll_call_id=rc.id, member_id=m, last_name=f"Last{m}", first_name=m,
                                        party="I" if m == "I0" else m[0], state="TN", position=vote))
    db_session.commit()
    members = [{"bioguideId": m, "name": f"{m} Last{m}", "lastNameForVoteMatch": f"Last{m}", "state": "TN",
                "party": m[0], "votingRecord": {"effectiveParty": "D" if m == "I0" else m[0]}} for m in dims]
    records = dict(zip(dims, party_line_records(db_session, "house", members)))
    assert records["D0"]["breaks"] or records["D0"]["flankBreaks"]


def test_a_departed_senators_position_classifies_a_break(db_session, monkeypatch):
    """The Senate's roll calls tie a vote to a position only through the
    members passed, and the roster lists sitting senators only. A senator
    who left during the Congress (stored, not passed) is still read with
    their position: here DX, who sits toward the Republicans, breaks with
    D0, so D0's break is toward the other party; without DX's position it
    would read as a flank break."""
    from app.models import Senator
    dims = {"D0": -0.45, **{f"D{i}": -0.4 for i in range(1, 6)}, "DX": 0.3, **{f"R{i}": 0.5 for i in range(6)}}
    monkeypatch.setattr(party_line_record, "_member_ideal_points",
                        lambda chamber: {"members": {f"bio-{m}": d for m, d in dims.items()}})
    for m in dims:
        db_session.add(Senator(id=f"S-{m}", bioguide_id=f"bio-{m}", name=f"{m} Last{m}", state="TN", party=m[0],
                               is_current=m != "DX"))
    rc = RollCall(chamber="senate", congress=119, session=2, number=41, date="2026-03-01", question="On Passage",
                  bill_id="S.41")
    db_session.add(rc)
    db_session.flush()
    for m in dims:
        vote = "Yea" if m in ("D0", "DX") or m.startswith("R") else "Nay"
        db_session.add(RollCallPosition(roll_call_id=rc.id, member_id=m, last_name=f"Last{m}", first_name=m,
                                        party=m[0], state="TN", position=vote))
    db_session.commit()
    roster = [{"bioguideId": f"bio-{m}", "name": f"{m} Last{m}", "lastNameForVoteMatch": f"Last{m}", "state": "TN",
               "party": m[0], "votingRecord": {"effectiveParty": m[0]}} for m in dims if m != "DX"]
    records = party_line_records(db_session, "senate", roster)
    assert len(records) == len(roster)  # the departed senator's own record isn't returned
    assert len(records[0]["breaks"]) == 1 and not records[0]["flankBreaks"]
    db_session.query(Senator).filter_by(id="S-DX").delete()
    db_session.commit()
    (alone, *_) = party_line_records(db_session, "senate", roster)
    assert not alone["breaks"] and len(alone["flankBreaks"]) == 1


def test_a_stored_namesake_who_never_voted_takes_no_ones_votes(db_session, monkeypatch):
    """A stored senator of a sitting senator's state and surname who cast
    no vote this Congress (a predecessor in the grace window) is not added:
    one LIS id voted under the name, and the sitting senator already claims
    it, so their record stands, read through a nickname on the roll call."""
    from app.models import Senator
    dims = {"D0": -0.4, "D1": -0.4, "R0": 0.5, "R1": 0.5}
    monkeypatch.setattr(party_line_record, "_member_ideal_points",
                        lambda chamber: {"members": {f"bio-{m}": d for m, d in dims.items()}})
    for m in dims:
        db_session.add(Senator(id=f"S-{m}", bioguide_id=f"bio-{m}", name=f"{m} Last{m}", state="VT", party=m[0]))
    db_session.add(Senator(id="S-OLD", bioguide_id="bio-OLD", name="D0nny LastD0", state="VT", party="D",
                           is_current=False))
    rc = RollCall(chamber="senate", congress=119, session=2, number=42, date="2026-03-01", question="On Passage",
                  bill_id="S.42")
    db_session.add(rc)
    db_session.flush()
    for m in dims:
        db_session.add(RollCallPosition(roll_call_id=rc.id, member_id=f"L-{m}", last_name=f"Last{m}",
                                        first_name="D0nny" if m == "D0" else m, party=m[0], state="VT",
                                        position="Yea" if m[0] == "R" else "Nay"))
    db_session.commit()
    roster = [{"bioguideId": f"bio-{m}", "name": f"{m} Last{m}", "lastNameForVoteMatch": f"Last{m}", "state": "VT",
               "party": m[0], "votingRecord": {"effectiveParty": m[0]}} for m in dims]
    assert party_line_records(db_session, "senate", roster)[0] is not None
    assert party_line_record._departed_senators(db_session, roster, {rc.id: list(
        db_session.query(RollCallPosition).filter_by(roll_call_id=rc.id))}) == []


def _namesakes(db_session, monkeypatch, stored, voted):
    """Senators of one state sharing a surname: `stored` (name, bioguide,
    is_current), `voted` {LIS id: first name} on one roll call."""
    from app.models import Senator
    monkeypatch.setattr(party_line_record, "_member_ideal_points",
                        lambda chamber: {"members": {b: 0.0 for _, b, _ in stored}})
    for name, bioguide, current in stored:
        db_session.add(Senator(id=f"S-{bioguide}", bioguide_id=bioguide, name=name, state="SC", party="R",
                               is_current=current))
    rc = RollCall(chamber="senate", congress=119, session=2, number=43, date="2026-03-01", question="On Passage",
                  bill_id="S.43")
    db_session.add(rc)
    db_session.flush()
    for lis, first in voted.items():
        db_session.add(RollCallPosition(roll_call_id=rc.id, member_id=lis, last_name="Lastg", first_name=first,
                                        party="R", state="SC", position="Yea"))
    db_session.commit()
    return {rc.id: list(db_session.query(RollCallPosition).filter_by(roll_call_id=rc.id))}


def _sitting(name, bioguide):
    return {"bioguideId": bioguide, "name": name, "lastNameForVoteMatch": "Lastg", "state": "SC", "party": "R",
            "votingRecord": {"effectiveParty": "R"}}


def test_a_successor_who_hasnt_voted_takes_no_votes_from_their_predecessor(db_session, monkeypatch):
    """A sitting senator with no vote yet doesn't claim the departed
    namesake's LIS id: the departed one is added, and their votes stay
    theirs, so the successor has no record yet."""
    positions = _namesakes(db_session, monkeypatch, [("Ann Lastg", "bio-ANN", True), ("Bob Lastg", "bio-BOB", False)],
                           {"L-BOB": "Bob"})
    roster = [_sitting("Ann Lastg", "bio-ANN")]
    assert [m["bioguideId"] for m in party_line_record._departed_senators(db_session, roster, positions)] == ["bio-BOB"]
    assert party_line_records(db_session, "senate", roster) == [None]


def test_namesakes_take_the_lis_id_their_first_name_matches_in_any_order(db_session, monkeypatch):
    """Two departed namesakes, one free LIS id: it goes to the one whose
    first name voted under it, not to whichever is stored first."""
    positions = _namesakes(db_session, monkeypatch,
                           [("Carl Lastg", "bio-A", False), ("Ann Lastg", "bio-ANN", True), ("Bob Lastg", "bio-B", False)],
                           {"L-ANN": "Ann", "L-BOB": "Bob"})
    roster = [_sitting("Ann Lastg", "bio-ANN")]
    assert [m["bioguideId"] for m in party_line_record._departed_senators(db_session, roster, positions)] == ["bio-B"]


def test_a_member_no_longer_stored_counts_in_their_recorded_partys_center(db_session, monkeypatch):
    """Positions read from different sections are compared from each
    section's party center, and a Senate section's center includes a member
    deleted after the grace period (not stored, not on the roster) by the
    party the section records. D0, thin this Congress, is read on the last
    Congress's full record (-0.0375 from that center); the other Democrats
    on this one, whose center GONE (a Democrat at +0.4) pulls right, so
    they read -0.2 and D0's lone break is toward the Republicans. Without
    GONE's recorded party they read 0 and it is a flank break."""
    from app.models import Senator
    dems = {f"D{i}": -0.4 for i in range(1, 4)}
    reps = {f"R{i}": 0.5 for i in range(4)}
    names = ["D0", *dems, *reps]
    section = {"congress": 119, "reliability": {"n0": 86, "reference_votes": 200},
               "members": {**{f"bio-{m}": d for m, d in {"D0": -0.45, **dems, **reps}.items()}, "bio-GONE": 0.4},
               "votes": {**{f"bio-{m}": 500 for m in names}, "bio-D0": 10, "bio-GONE": 500},
               "parties": {**{f"bio-{m}": m[0] for m in names}, "bio-GONE": "D"},
               "prior": {"congress": 118, "reliability": {"n0": 86, "reference_votes": 200},
                         "members": {f"bio-{m}": d for m, d in {"D0": -0.45, **dems, **reps}.items()},
                         "votes": {f"bio-{m}": 500 for m in names},
                         "parties": {f"bio-{m}": m[0] for m in names}}}
    monkeypatch.setattr(party_line_record, "_member_ideal_points", lambda chamber: section)
    for m in names:
        db_session.add(Senator(id=f"S-{m}", bioguide_id=f"bio-{m}", name=f"{m} Last{m}", state="TN", party=m[0]))
    rc = RollCall(chamber="senate", congress=119, session=2, number=44, date="2026-03-01", question="On Passage",
                  bill_id="S.44")
    db_session.add(rc)
    db_session.flush()
    for m in names:
        db_session.add(RollCallPosition(roll_call_id=rc.id, member_id=m, last_name=f"Last{m}", first_name=m,
                                        party=m[0], state="TN", position="Yea" if m == "D0" or m[0] == "R" else "Nay"))
    db_session.commit()
    roster = [{"bioguideId": f"bio-{m}", "name": f"{m} Last{m}", "lastNameForVoteMatch": f"Last{m}", "state": "TN",
               "party": m[0], "votingRecord": {"effectiveParty": m[0]}} for m in names]
    (record, *_) = party_line_records(db_session, "senate", roster)
    assert len(record["breaks"]) == 1
    section["parties"].pop("bio-GONE")
    (record, *_) = party_line_records(db_session, "senate", roster)
    assert not record["breaks"] and len(record["flankBreaks"]) == 1



def test_namesakes_sharing_a_first_name_prefix_go_by_the_exact_match_in_either_order(db_session, monkeypatch):
    """Two departed namesakes whose first names both loosely match the one
    free LIS id ("Rob" and "Robert" against "Robert"): the exact match takes
    it, whichever is stored first."""
    for first, second in (("bio-A", "bio-B"), ("bio-B", "bio-A")):
        db_session.query(RollCallPosition).delete()
        db_session.query(RollCall).delete()
        from app.models import Senator
        db_session.query(Senator).delete()
        db_session.commit()
        positions = _namesakes(db_session, monkeypatch, [("Rob Lastg", first, False), ("Robert Lastg", second, False)],
                               {"L-R": "Robert"})
        assert [m["bioguideId"] for m in party_line_record._departed_senators(db_session, [], positions)] == [second]


def test_a_lone_defector_is_read_against_the_center_when_colleagues_weigh_nothing():
    """Party colleagues whose positions weigh nothing (no votes, no career
    position) can't stand in for the party: the lone defector is read
    against the party's center, 0."""
    cast = [(0, "R", "Nay", False, (-0.1, 1.0)), (1, "R", "Yea", True, (0.5, 0.0)), (2, "R", "Yea", True, (0.4, 0.0))]
    assert party_line_record._toward_other_party("R", cast) is True


def test_the_warning_names_only_the_members_a_run_scores(db_session, monkeypatch, caplog):
    """The rest of the chamber is passed for the means alone: one of them
    matching no roll call isn't scored on stored votes, so the warning
    doesn't name them."""
    monkeypatch.setattr(party_line_record, "_member_ideal_points", lambda chamber: {"members": DIM1})
    _roll_call(db_session, "house", 45, "On Passage", "HR.45", {})
    db_session.commit()
    rest = {"bioguideId": "Z9", "name": "Zed Nobody", "state": "TN", "party": "R"}
    with caplog.at_level("WARNING"):
        party_line_records(db_session, "house", _members() + [rest])
    assert "Zed Nobody" not in caplog.text


def test_a_sitting_rob_keeps_his_record_beside_a_departed_robert(db_session, monkeypatch):
    """A first name that is a prefix of another ("Rob", "Robert") matches its
    own LIS id exactly: the sitting Rob keeps his votes, and the departed
    Robert is added on his own."""
    positions = _namesakes(db_session, monkeypatch,
                           [("Rob Lastg", "bio-ROB", True), ("Robert Lastg", "bio-ROBERT", False)],
                           {"L-ROB": "Rob", "L-R": "Robert"})
    roster = [_sitting("Rob Lastg", "bio-ROB")]
    assert [m["bioguideId"] for m in party_line_record._departed_senators(db_session, roster, positions)] == [
        "bio-ROBERT"]
    # Matched to his roll-call positions (None would mean scored on stored votes).
    (record,) = party_line_records(db_session, "senate", roster)
    assert record is not None


def test_departed_namesakes_each_keep_their_own_id(db_session, monkeypatch):
    """Two departed namesakes, "Rob" and "Robert", each with their own LIS
    id: both are added, and each is resolved to their own."""
    from app.pipeline.transform.normalize_votes import resolve_senate_lis_ids
    positions = _namesakes(db_session, monkeypatch,
                           [("Ann Lastg", "bio-ANN", True), ("Rob Lastg", "bio-ROB", False),
                            ("Robert Lastg", "bio-ROBERT", False)],
                           {"L-ANN": "Ann", "L-ROB": "Rob", "L-R": "Robert"})
    roster = [_sitting("Ann Lastg", "bio-ANN")]
    departed = party_line_record._departed_senators(db_session, roster, positions)
    assert sorted(m["bioguideId"] for m in departed) == ["bio-ROB", "bio-ROBERT"]
    members = [{**m, "id": i} for i, m in enumerate(roster + departed)]
    seen = [{"lisId": p.member_id, "firstName": p.first_name, "lastName": p.last_name, "state": p.state}
            for ps in positions.values() for p in ps]
    resolved = resolve_senate_lis_ids(members, seen)
    assert {members[i]["name"]: lis for i, lis in resolved.items()} == {
        "Ann Lastg": "L-ANN", "Rob Lastg": "L-ROB", "Robert Lastg": "L-R"}


def test_a_departed_senator_whose_surname_only_contains_the_voters_is_not_added(db_session, monkeypatch):
    """Surnames match as whole words: a stored departed "Ann Lastgard"
    never takes the votes of an "Ann Lastg" who voted."""
    from app.models import Senator
    monkeypatch.setattr(party_line_record, "_member_ideal_points", lambda chamber: {"members": {"bio-LASTGARD": 0.0}})
    db_session.add(Senator(id="S-LASTGARD", bioguide_id="bio-LASTGARD", name="Ann Lastgard", state="SC", party="R",
                           is_current=False))
    rc = RollCall(chamber="senate", congress=119, session=2, number=44, date="2026-03-01", question="On Passage",
                  bill_id="S.44")
    db_session.add(rc)
    db_session.flush()
    db_session.add(RollCallPosition(roll_call_id=rc.id, member_id="L-ANN", last_name="Lastg", first_name="Ann",
                                    party="R", state="SC", position="Yea"))
    db_session.commit()
    positions = {rc.id: list(db_session.query(RollCallPosition).filter_by(roll_call_id=rc.id))}
    assert party_line_record._departed_senators(db_session, [], positions) == []


def test_an_id_two_departed_namesakes_match_equally_goes_to_neither(db_session, monkeypatch):
    """No order decides an LIS id two departed candidates match alike."""
    positions = _namesakes(db_session, monkeypatch,
                           [("Rob Lastg", "bio-ROB", False), ("Rob J Lastg", "bio-ROBJ", False)], {"L-ROB": "Rob"})
    assert party_line_record._departed_senators(db_session, [], positions) == []


def test_a_departed_senator_two_ids_match_is_left_out(db_session, monkeypatch):
    """A candidate two free LIS ids match ("Rob" exactly, "Robby" loosely)
    is left out rather than credited with either by order."""
    positions = _namesakes(db_session, monkeypatch, [("Rob Lastg", "bio-ROB", False)],
                           {"L-ROB": "Rob", "L-ROBBY": "Robby"})
    assert party_line_record._departed_senators(db_session, [], positions) == []
