"""The homepage's "On the record" entries: the closest vote and the lone
party breaks of Congress's latest week of record votes."""

from datetime import date, datetime

from fastapi.testclient import TestClient

from app.api import notable as nt
from app.database import get_db
from app.main import app
from app.models import RollCall, RollCallPosition, Senator

_NUMBER = iter(range(1, 10_000))


def _rc(db, day, yeas, nays, *, chamber="senate", question="On Passage", requirement="1/2", rejected=False):
    rc = RollCall(chamber=chamber, congress=119, session=2, number=next(_NUMBER), date=day, question=question,
                  title="A bill", result="Rejected" if rejected else "Passed", rejected=rejected,
                  majority_requirement=requirement, yeas=yeas, nays=nays, bill_id="S.1")
    db.add(rc)
    db.flush()
    return rc


def _party_votes(db, rc, party, yea, nay, *, prefix=None, lone=None):
    """`yea` and `nay` members of `party`; `lone` = (last name, state, side)
    for a named member added on that side."""
    prefix = prefix or party
    for i in range(yea):
        db.add(RollCallPosition(roll_call_id=rc.id, member_id=f"{prefix}y{i}", last_name=f"{prefix}Y{i}",
                                party=party, state="ZZ", position="Yea"))
    for i in range(nay):
        db.add(RollCallPosition(roll_call_id=rc.id, member_id=f"{prefix}n{i}", last_name=f"{prefix}N{i}",
                                party=party, state="ZZ", position="Nay"))
    if lone:
        last, state, side = lone
        db.add(RollCallPosition(roll_call_id=rc.id, member_id=f"L-{last}", last_name=last, first_name="Pat",
                                party=party, state=state, position=side))


def _senators(db):
    db.add_all([
        Senator(id="pat-lone", name="Pat Lone", state="AA", party="R", is_current=True),
        Senator(id="sam-other", name="Sam Other", state="AA", party="D", is_current=True),
        Senator(id="pat-dee", name="Pat Dee", state="BB", party="D", is_current=True),
        Senator(id="lee-leader", name="Lee Leader", state="CC", party="R", is_current=True,
                leadership_title="Senate Majority Leader"),
    ])


class TestClosest:
    def test_smallest_gap_among_simple_majority_questions(self, db_session):
        _rc(db_session, "2026-09-28", 60, 38)
        close = _rc(db_session, "2026-09-29", 49, 50, rejected=True)
        # A 3/5 question is left out: its distance from passing is the threshold's.
        _rc(db_session, "2026-09-30", 50, 50, requirement="3/5")
        db_session.commit()
        r = nt.notable(db_session, date(2026, 10, 2))
        assert r["weekStart"] == "2026-09-28" and r["weekEnd"] == "2026-10-04"
        first = r["entries"][0]
        assert first["kind"] == "closest"
        assert first["vote"]["number"] == close.number
        assert first["sentence"] == "Decided by 1 vote in the Senate, 49–50: Rejected."

    def test_a_tie_and_the_newest_of_equal_gaps(self, db_session):
        _rc(db_session, "2026-09-28", 48, 48)
        newer = _rc(db_session, "2026-09-30", 47, 47)
        db_session.commit()
        first = nt.notable(db_session, date(2026, 10, 2))["entries"][0]
        assert first["vote"]["number"] == newer.number
        assert first["sentence"] == "Tied 47–47 in the Senate: Passed."

    def test_housekeeping_questions_are_not_votes_on_anything(self, db_session):
        _rc(db_session, "2026-09-29", 50, 49, question="On the Motion to Table")
        db_session.commit()
        assert nt.notable(db_session, date(2026, 10, 2))["entries"] == []


class TestLoneBreak:
    def test_one_per_party_newest_first_with_a_same_state_comparison(self, db_session):
        _senators(db_session)
        older = _rc(db_session, "2026-09-28", 52, 47)
        _party_votes(db_session, older, "R", 0, 51, lone=("Lone", "AA", "Yea"))
        newer = _rc(db_session, "2026-09-30", 52, 47)
        _party_votes(db_session, newer, "R", 0, 40, lone=("Lone", "AA", "Yea"))
        _party_votes(db_session, newer, "D", 30, 0, lone=("Dee", "BB", "Nay"))
        db_session.commit()
        breaks = [e for e in nt.notable(db_session, date(2026, 10, 2))["entries"] if e["kind"] == "loneBreak"]
        assert [(e["party"], e["vote"]["number"]) for e in breaks] == [("D", newer.number), ("R", newer.number)]
        r = breaks[1]
        assert r["sentence"] == "The only Republican to vote Yea: Pat Lone (R-AA), against 40 Republicans voting Nay."
        assert r["memberId"] == "pat-lone"
        assert (r["compareWithId"], r["compareWithName"]) == ("sam-other", "Sam Other")
        # BB has one senator on file: nobody to compare with.
        assert breaks[0]["compareWithId"] is None

    def test_too_few_voting_is_not_a_lone_break(self, db_session):
        _senators(db_session)
        rc = _rc(db_session, "2026-09-30", 10, 10)
        _party_votes(db_session, rc, "R", 0, nt.LONE_BREAK_MIN_VOTING - 2, lone=("Lone", "AA", "Yea"))
        db_session.commit()
        assert [e for e in nt.notable(db_session, date(2026, 10, 2))["entries"] if e["kind"] == "loneBreak"] == []

    def test_the_majority_leaders_reconsider_switch_is_not_a_break(self, db_session):
        _senators(db_session)
        rc = _rc(db_session, "2026-09-30", 48, 51, rejected=True)
        # The party voted Yea on a question that failed; the leader moved to
        # the prevailing Nay side to be able to move to reconsider.
        _party_votes(db_session, rc, "R", 48, 0, lone=("Leader", "CC", "Nay"))
        db_session.commit()
        assert [e for e in nt.notable(db_session, date(2026, 10, 2))["entries"] if e["kind"] == "loneBreak"] == []


class TestWindow:
    def test_the_last_week_in_session_shows_through_a_short_recess(self, db_session):
        _rc(db_session, "2026-09-30", 49, 50)
        db_session.commit()
        r = nt.notable(db_session, date(2026, 10, 27))
        assert r["weekStart"] == "2026-09-28" and len(r["entries"]) == 1

    def test_a_long_recess_shows_nothing(self, db_session):
        _rc(db_session, "2026-09-30", 49, 50)
        db_session.commit()
        assert nt.notable(db_session, date(2026, 10, 29)) == {"weekStart": None, "weekEnd": None, "entries": []}

    def test_no_votes_on_record(self, db_session):
        assert nt.notable(db_session, date(2026, 10, 2))["entries"] == []


def test_the_endpoint_reads_the_eastern_clock(db_session, freeze_utcnow):
    _rc(db_session, "2026-09-30", 49, 50)
    db_session.commit()
    # 03:00 UTC on Oct 29 is still Oct 28 in the East: 28 days after the vote.
    freeze_utcnow(datetime(2026, 10, 29, 3))
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        r = TestClient(app).get("/api/notable")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200
    assert r.headers["cache-control"].startswith("public, max-age=300")
    assert len(r.json()["entries"]) == 1
