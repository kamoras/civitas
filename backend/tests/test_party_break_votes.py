"""A vote marked "against party" carries the Congress record's own roll
call — date, question and how each party voted — so a reader can see why
it counts as a break."""

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import KeyVote, RepKeyVote, Representative, RollCall, RollCallPosition, Senator
from app.pipeline.transform.normalize_votes import roll_call_ref, stamp_roll_call_outcome
from app.services.bill_record import roll_call_summaries


def _roll_call(db, chamber, number, positions, **kw):
    rc = RollCall(chamber=chamber, congress=119, session=2, number=number, date="2026-07-22",
                  question=kw.get("question", "On Motion to Recommit"), result="Failed", bill_id="HR.8800")
    db.add(rc)
    db.flush()
    for i, (party, pos) in enumerate(positions):
        db.add(RollCallPosition(roll_call_id=rc.id, member_id=f"M{i:03d}", last_name=f"L{i}", party=party,
                                state="TN", position=pos))
    return rc


def test_the_stamp_records_the_roll_call_and_how_the_parties_split():
    members = [{"party": "R", "voteCast": "Nay"}] * 5 + [{"party": "D", "voteCast": "Yea"}] * 5
    bill = {}
    stamp_roll_call_outcome(bill, {"chamber": "House", "congress": 119, "session": 2, "rollNumber": 277,
                                   "voteDate": "22-Jul-2026", "rejected": True, "members": members})
    assert bill["rollCall"] == "house-119-2-277"
    assert bill["partySplit"] == "D"
    assert roll_call_ref({"congress": 119, "session": 2, "rollNumber": 243}) == "senate-119-2-243"
    assert roll_call_ref({"congress": 119}) is None


def test_summaries_count_each_partys_positions(db_session):
    _roll_call(db_session, "house", 277, [("R", "No")] * 3 + [("R", "Aye")] + [("D", "Aye")] * 2)
    db_session.commit()
    got = roll_call_summaries(db_session, ["house-119-2-277", "house-119-2-9999", None, "junk"])
    assert list(got) == ["house-119-2-277"]
    s = got["house-119-2-277"]
    assert s["question"] == "On Motion to Recommit" and s["billLabel"] == "H.R. 8800"
    assert s["parties"][0] == {"party": "R", "yea": 1, "nay": 3, "present": 0, "notVoting": 0}


def test_every_break_is_listed_with_its_roll_call_in_both_chambers(db_session):
    db_session.add(Representative(id="tim-burchett", bioguide_id="B001309", name="Tim Burchett",
                                  state="TN", district=2, party="R"))
    db_session.add(Senator(id="rand-paul", bioguide_id="P000603", name="Rand Paul", state="KY", party="R"))
    _roll_call(db_session, "house", 277, [("R", "No")] * 3 + [("D", "Aye")] * 3)
    _roll_call(db_session, "senate", 240, [("R", "Yea")] * 3 + [("D", "Nay")] * 3, question="On Cloture")
    # One break in each category: a list of breaks must not hide either.
    db_session.add(RepKeyVote(representative_id="tim-burchett", bill_name="H R 8800", bill_id="HouseRC-2026-277",
                              date="2026-07-22", vote="Yea", voted_with_party=False, vote_category="recent",
                              roll_call="house-119-2-277"))
    db_session.add(RepKeyVote(representative_id="tim-burchett", bill_name="S.32", bill_id="S.32", date="2026-03-01",
                              vote="Nay", voted_with_party=False, vote_category="key"))
    db_session.add(KeyVote(senator_id="rand-paul", bill_name="Cloture", bill_id="S.1", date="2026-07-22",
                           vote="Nay", voted_with_party=False, vote_category="recent", roll_call="senate-119-2-240"))
    db_session.commit()
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        house = client.get("/api/representatives/tim-burchett/votes?category=all&filter=against-party").json()
        assert house["total"] == 2
        by_id = {v["billId"]: v for v in house["votes"]}
        rc = by_id["HouseRC-2026-277"]["rollCall"]
        assert rc["question"] == "On Motion to Recommit" and rc["date"] == "2026-07-22"
        assert rc["parties"][0]["party"] == "R" and rc["parties"][0]["nay"] == 3
        assert by_id["S.32"]["rollCall"] is None  # stored before the roll call was recorded
        senate = client.get("/api/senators/rand-paul/votes?category=all&filter=against-party").json()
        assert senate["votes"][0]["rollCall"]["question"] == "On Cloture"
    finally:
        app.dependency_overrides.clear()


def test_housekeeping_questions_never_count_for_or_against_the_party():
    from app.pipeline.transform.normalize_votes import is_housekeeping

    members = [{"party": "R", "voteCast": "Nay"}] * 5 + [{"party": "D", "voteCast": "Yea"}] * 5
    for question in ("On Motion to Recommit", "On Ordering the Previous Question", "On Motion to Table",
                     "On the Motion to Table S.J.Res. 55", "On Motion to Adjourn", "On Approving the Journal"):
        bill = {}
        stamp_roll_call_outcome(bill, {"chamber": "House", "congress": 119, "session": 2, "rollNumber": 1,
                                       "question": question, "members": members})
        assert bill["partySplit"] is None, question
    # Rule votes, cloture, passage and nominations still count.
    for question in ("On Agreeing to the Resolution", "On the Cloture Motion", "On Passage",
                     "On the Nomination", "On Agreeing to the Amendment"):
        assert not is_housekeeping(question), question
