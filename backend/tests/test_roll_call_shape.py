"""Roll calls that aren't yeas against nays: a Senate tie the Vice President
broke, an election of the Speaker and a quorum call (services/roll_call_shape.py),
read from the chambers' own records: a Senate passage tied 50-50 with the
Vice President voting Yea, and the House's first two roll calls of a
Congress (the quorum call by states and the election of the Speaker).
Expected figures are read from the fixtures' own totals, never typed."""

import asyncio
from datetime import date
from types import SimpleNamespace

from lxml import etree

from app.api import notable
from app.models import RollCall
from app.pipeline import congress_activity as ca
from app.pipeline.analyze import early_signal as es
from app.pipeline.fetch.congress import parse_senate_vote_xml
from app.services import congress_service
from app.services.bill_record import bill_votes, vote_detail
from app.services.record_cards import vote_card
from app.services.roll_call_shape import shape
from tests.congress_activity_helpers import FIX, _fake_get

TIE = (FIX / "roll_calls" / "vote_119_1_00372.xml").read_bytes()
UNTIED = (FIX / "roll_calls" / "vote_119_2_00243.xml").read_bytes()
QUORUM = (FIX / "roll_calls" / "house_2025_roll001.xml").read_bytes()
SPEAKER = (FIX / "roll_calls" / "house_2025_roll002.xml").read_bytes()
VP = {"byWhom": "Vice President of the United States", "vote": "Yea"}
_TIE_XML = etree.fromstring(TIE)
# The tied vote's own Congress, session and number, as Senate.gov prints them.
CONGRESS, SESSION, NUMBER = (int(_TIE_XML.findtext(t)) for t in ("congress", "session", "vote_number"))
_SPEAKER_XML = etree.fromstring(SPEAKER)
# The Clerk's own totals for the election, in its order (most votes first),
# without the Present / Not Voting rows.
CANDIDATES = [
    {"name": t.findtext("candidate"), "votes": int(t.findtext("candidate-total"))}
    for t in _SPEAKER_XML.iter("totals-by-candidate")
    if t.findtext("candidate") not in ("Present", "Not Voting")
]
WINNER = _SPEAKER_XML.findtext(".//vote-result")


def _run(coro):
    return asyncio.run(coro)


def _store_senate(db, body, number=NUMBER, session=SESSION):
    ca._store_roll_call(db, "senate", CONGRESS, session, number,
                        parse_senate_vote_xml(body.decode(), CONGRESS, session, number),
                        f"https://www.senate.gov/x/vote_{CONGRESS}_{session}_{number:05d}.xml")
    return db.query(RollCall).filter_by(chamber="senate", number=number).one()


class TestTieBreaker:
    def test_the_senate_record_names_the_vice_presidents_vote(self):
        assert parse_senate_vote_xml(TIE.decode(), CONGRESS, SESSION, NUMBER)["tieBreaker"] == VP
        assert parse_senate_vote_xml(UNTIED.decode(), 119, 2, 243)["tieBreaker"] is None

    def test_stored_and_shown_with_the_tally(self, db_session):
        rc = _store_senate(db_session, TIE)
        assert (rc.yeas, rc.nays, rc.result) == (50, 50, "Bill Passed")
        assert (rc.tie_breaker_by, rc.tie_breaker_vote) == (VP["byWhom"], "Yea")
        detail = vote_detail(db_session, rc)
        assert detail["tieBreaker"] == VP and detail["kind"] == "question"
        assert detail["tally"] == "50–50, Vice President of the United States voted Yea"
        (row,) = bill_votes(db_session, CONGRESS, rc.bill_id)
        assert row["tieBreaker"] == VP
        (day,) = congress_service.bill_days(db_session, rc.bill_id, CONGRESS)
        assert day["entries"][0]["text"].endswith("Bill Passed, 50–50, Vice President of the United States voted Yea")

    def test_an_untied_senate_vote_reads_none_and_the_house_is_not_read(self, db_session):
        rc = _store_senate(db_session, UNTIED, number=243, session=2)
        assert (rc.tie_breaker_by, rc.tie_breaker_vote) == ("", "")
        assert shape(rc)["tieBreaker"] is None
        assert ca._tie_breaker_columns("house", {}) == {"tie_breaker_by": None, "tie_breaker_vote": None}

    def test_ties_stored_before_the_column_are_read_again_once(self, db_session, monkeypatch):
        for number, yeas, nays in ((NUMBER, 50, 50), (NUMBER + 1, 47, 41)):
            db_session.add(RollCall(chamber="senate", congress=CONGRESS, session=SESSION, number=number,
                                    date="2020-07-01", yeas=yeas, nays=nays, result="Bill Passed",
                                    source_url=f"https://www.senate.gov/x/vote_{number:05d}.xml"))
        db_session.add(RollCall(chamber="house", congress=CONGRESS, session=SESSION, number=9, date="2020-07-01",
                                yeas=200, nays=200, source_url="https://clerk.house.gov/evs/2020/roll009.xml"))
        db_session.commit()
        monkeypatch.setattr(ca, "_get", _fake_get({f"vote_{NUMBER:05d}.xml": TIE}))
        assert _run(ca.reread_senate_ties(None, db_session)) == (1, "ok")
        tied = db_session.query(RollCall).filter_by(number=NUMBER).one()
        assert (tied.tie_breaker_by, tied.tie_breaker_vote) == (VP["byWhom"], "Yea")
        # Nothing left to read: no request is made.
        monkeypatch.setattr(ca, "_get", _fake_get({}))
        assert _run(ca.reread_senate_ties(None, db_session)) == (0, "ok")

    def test_a_failed_reread_leaves_the_tie_unread(self, db_session, monkeypatch):
        db_session.add(RollCall(chamber="senate", congress=CONGRESS, session=SESSION, number=NUMBER,
                                date="2020-07-01", yeas=50, nays=50, source_url="https://www.senate.gov/x/vote.xml"))
        db_session.commit()
        monkeypatch.setattr(ca, "_get", _fake_get({}))
        assert _run(ca.reread_senate_ties(None, db_session)) == (0, "failed")
        assert db_session.query(RollCall).one().tie_breaker_vote is None

    def test_the_closest_vote_says_who_broke_the_tie(self, db_session):
        rc = _store_senate(db_session, TIE)
        rc.majority_requirement = "1/2"
        found = notable._closest([rc])
        assert found["sentence"] == ("Tied 50–50 in the Senate, broken by the Vice President of the United States "
                                     "voting Yea: Bill Passed.")
        assert found["vote"]["tieBreaker"] == VP

    def test_the_share_card_names_the_tie_breaker(self, db_session):
        _store_senate(db_session, TIE)
        card = vote_card(db_session, "senate", CONGRESS, SESSION, NUMBER)
        assert "Tie broken: Vice President of the United States voted Yea" in card.lines


class TestHouseRecords:
    def _sync(self, db, monkeypatch):
        monkeypatch.setattr(ca, "_get", _fake_get({
            "roll001.xml": QUORUM, "roll002.xml": SPEAKER, "clerk.house.gov": ca._ABSENT,
        }))
        assert _run(ca.sync_roll_calls(None, db, "house", CONGRESS, 1)) == (2, "ok")

    def test_the_election_of_the_speaker_counts_each_candidate(self, db_session, monkeypatch):
        self._sync(db_session, monkeypatch)
        rc = db_session.query(RollCall).filter_by(number=2).one()
        detail = vote_detail(db_session, rc)
        assert detail["kind"] == "election"
        # Counted from the members' positions, it matches the Clerk's totals.
        assert detail["candidates"] == CANDIDATES
        assert detail["resultLabel"] == f"{WINNER} elected"  # a majority of the votes for a person
        assert detail["tally"] == ", ".join(f"{c['name']} {c['votes']}" for c in CANDIDATES)
        assert (detail["notVoting"], detail["rejected"]) == (0, None)
        assert {m["bucket"] for m in detail["members"]} == {"candidate"}
        cast = sum(c["votes"] for c in CANDIDATES)
        assert sum(sum(p.get("candidates", {}).values()) for p in detail["parties"]) == cast
        assert all(p["notVoting"] == 0 for p in detail["parties"])
        # Every list of the day's votes reads it the same way.
        day = congress_service.day_report(db_session, date.fromisoformat(rc.date))
        listed = {v["number"]: v for v in day["chambers"]["house"]["votes"]}
        assert listed[2]["tally"] == detail["tally"] and listed[1]["kind"] == "quorum"

    def test_a_ballot_no_one_won_is_not_an_election(self):
        # The Clerk's result names the leader of a ballot no one won: here
        # 212 of 434 votes for a person, short of a majority.
        rc = SimpleNamespace(yeas=0, nays=0, present=0, not_voting=434, rejected=None, result="Doe (TX)",
                             tie_breaker_by=None, tie_breaker_vote=None)
        votes = ["Doe (TX)"] * 212 + ["Roe"] * 203 + ["Poe"] * 10 + ["Loe"] * 9
        out = shape(rc, [SimpleNamespace(position=v) for v in votes])
        assert (out["kind"], out["resultLabel"]) == ("election", "No one elected")

    def test_a_quorum_call_decides_nothing(self, db_session, monkeypatch):
        self._sync(db_session, monkeypatch)
        rc = db_session.query(RollCall).filter_by(number=1).one()
        present = int(etree.fromstring(QUORUM).findtext(".//totals-by-vote/present-total"))
        assert (rc.result, rc.present) == ("Passed", present)
        detail = vote_detail(db_session, rc)
        assert (detail["kind"], detail["tally"], detail["resultLabel"], detail["rejected"]) == (
            "quorum", f"{present} present", "Quorum call", None)
        card = vote_card(db_session, "house", rc.congress, rc.session, rc.number)
        assert [row["label"] for row in card.tally] == ["Present", "Not voting"]

    def test_the_share_card_has_a_row_per_candidate(self, db_session, monkeypatch):
        self._sync(db_session, monkeypatch)
        rc = db_session.query(RollCall).filter_by(number=2).one()
        card = vote_card(db_session, "house", rc.congress, rc.session, rc.number)
        assert [(row["label"], row["total"]) for row in card.tally][:len(CANDIDATES)] == [
            (c["name"], c["votes"]) for c in CANDIDATES]
        assert card.lines == [f"Result: {WINNER} elected"]


class TestEarlySignal:
    def test_a_house_recorded_vote_counts_ayes_and_noes(self):
        vote = {"members": [{"voteCast": "Aye"}] * 3 + [{"voteCast": "No"}] * 2 + [{"voteCast": "Not Voting"}]}
        assert es._tally(vote) == (3, 2, 1)

    def test_a_broken_tie_is_stated(self):
        vote = parse_senate_vote_xml(TIE.decode(), CONGRESS, SESSION, NUMBER)
        title, summary, facts = es._compose_developing_issue(vote)
        assert title.endswith(": Bill Passed, 50-50, Vice President of the United States voted Yea")
        assert "50 to 50, Vice President of the United States voted Yea" in summary
        assert facts[0] == "Tally: 50 yea, 50 nay, 0 not voting, Vice President of the United States voted Yea."


def test_positions_outside_the_vote_vocabulary_are_candidates():
    from app.services.roll_call_shape import candidate_of

    assert [candidate_of(p) for p in ("Yea", "aye", "No", "Not Guilty", "Present", "Not Voting", "Doe (TX)")] == [
        None, None, None, None, None, None, "Doe (TX)"]
