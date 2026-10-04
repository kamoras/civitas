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
    # new record reaches the measured switch (a full record without one),
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
