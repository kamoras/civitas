"""/compare's voting agreement: how often two members of one chamber voted
the same way this Congress."""

from fastapi.testclient import TestClient

from app.api import compare
from app.database import get_db
from app.main import app
from app.models import Representative, RollCall, RollCallPosition, Senator

_NUMBER = iter(range(1, 10_000))


def _rc(db, chamber, day, *, congress=119, question="On Passage"):
    rc = RollCall(chamber=chamber, congress=congress, session=2, number=next(_NUMBER), date=day,
                  question=question, title="A bill", result="Passed", yeas=60, nays=40, bill_id="S.1")
    db.add(rc)
    db.flush()
    return rc


def _pos(db, rc, member_id, last, state, position):
    db.add(RollCallPosition(roll_call_id=rc.id, member_id=member_id, last_name=last, state=state, position=position))


def _senate(db):
    db.add_all([Senator(id="pat-able", name="Pat Able", state="AA", party="D", is_current=True),
                Senator(id="lee-baker", name="Lee Baker", state="AA", party="R", is_current=True)])
    votes = [
        ("2026-09-01", "Yea", "Yea"), ("2026-09-02", "Nay", "Nay"), ("2026-09-03", "Yea", "Nay"),
        ("2026-09-04", "Nay", "Yea"), ("2026-09-05", "Yea", "Not Voting"),
    ]
    rcs = []
    for day, a, b in votes:
        rc = _rc(db, "senate", day)
        _pos(db, rc, "S001", "Able", "AA", a)
        _pos(db, rc, "S002", "Baker", "AA", b)
        rcs.append(rc)
    # A housekeeping split and a split in the last Congress: neither counts.
    for rc in (_rc(db, "senate", "2026-09-06", question="On the Motion to Table"),
               _rc(db, "senate", "2024-09-06", congress=118)):
        _pos(db, rc, "S001", "Able", "AA", "Yea")
        _pos(db, rc, "S002", "Baker", "AA", "Nay")
    db.commit()
    return rcs


def test_senators_matched_by_their_roll_call_ids(db_session):
    rcs = _senate(db_session)
    r = compare.agreement(db_session, "senate", "pat-able", "lee-baker")
    assert (r["bothVoted"], r["agreed"], r["split"], r["agreementPct"]) == (4, 2, 2, 50)
    assert r["sentence"] == ("Voted the same way on 2 of the 4 roll calls this Congress "
                             "where both cast a Yea or a Nay (50%).")
    # Newest split first, each member's own vote.
    assert [(s["number"], s["left"], s["right"]) for s in r["recentSplits"]] == [
        (rcs[3].number, "Nay", "Yea"), (rcs[2].number, "Yea", "Nay")]


def test_house_members_by_bioguide_and_the_aye_no_spelling(db_session):
    db_session.add_all([
        Representative(id="rep-a", name="Ann A", state="BB", district=1, party="D", bioguide_id="A000001"),
        Representative(id="rep-b", name="Bo B", state="BB", district=2, party="R", bioguide_id="B000001"),
    ])
    for i in range(compare.SPLITS_SHOWN + 2):
        rc = _rc(db_session, "house", f"2026-09-{i + 1:02d}")
        _pos(db_session, rc, "A000001", "A", "BB", "Aye")
        _pos(db_session, rc, "B000001", "B", "BB", "No")
    db_session.commit()
    r = compare.agreement(db_session, "house", "rep-a", "rep-b")
    assert (r["bothVoted"], r["agreed"], r["agreementPct"]) == (compare.SPLITS_SHOWN + 2, 0, 0)
    assert len(r["recentSplits"]) == compare.SPLITS_SHOWN
    assert r["recentSplits"][0]["left"] == "Yea" and r["recentSplits"][0]["right"] == "Nay"


def test_no_shared_votes(db_session):
    db_session.add_all([Senator(id="pat-able", name="Pat Able", state="AA", party="D"),
                        Senator(id="lee-baker", name="Lee Baker", state="AA", party="R")])
    db_session.commit()
    r = compare.agreement(db_session, "senate", "pat-able", "lee-baker")
    assert r["agreementPct"] is None and r["recentSplits"] == []
    assert r["sentence"] == "No roll call this Congress has a Yea or a Nay from both."


def test_the_endpoint_refuses_a_member_of_the_other_chamber(db_session):
    _senate(db_session)
    db_session.add(Representative(id="rep-a", name="Ann A", state="BB", district=1, party="D"))
    db_session.commit()
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        ok = client.get("/api/compare/agreement?left=pat-able&right=lee-baker&chamber=senate")
        mixed = client.get("/api/compare/agreement?left=pat-able&right=rep-a&chamber=senate")
    finally:
        app.dependency_overrides.clear()
    assert ok.status_code == 200 and ok.json()["agreementPct"] == 50
    assert ok.headers["cache-control"].startswith("public, max-age=300")
    assert mixed.status_code == 404
