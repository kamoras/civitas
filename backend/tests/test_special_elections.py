"""Special elections, vacant seats and the federal election calendar:
reading the sources, the special's count, its swing, the board, the
calendar and the season /elections leads with."""

import json
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app import special_elections as se
from app.database import get_db
from app.live_results import specials as live
from app.live_results.sync import StateRead
from app.main import app
from app.models import FecReportingDeadline, FederalElectionDate, SeatVacancy
from app.pipeline.fetch import special_elections as fetch
from app.pipeline.fetch.election_results import (
    ContestCount, StateCount, UntrustedCount, pick_election, pick_special,
)

MEMBER_DATA = b"""<?xml version="1.0"?><MemberData><members>
<member><statedistrict>TX02</statedistrict><member-info><bioguideID>X000001</bioguideID>
<state postal-code="TX"><state-fullname>Texas</state-fullname></state><district>2nd</district></member-info></member>
<member><statedistrict>TX23</statedistrict><member-info><bioguideID/>
<state postal-code="TX"><state-fullname>Texas</state-fullname></state><district>23rd</district>
<footnote>Vacancy due to the resignation of A Member, April 14, 2026.</footnote></member-info>
<predecessor-info cause="R"><pred-official-name>A Member</pred-official-name><pred-party>R</pred-party>
<pred-vacate-date date="20260414">April 14, 2026</pred-vacate-date></predecessor-info></member>
<member><statedistrict>DC00</statedistrict><member-info><bioguideID/>
<state postal-code="DC"><state-fullname>District of Columbia</state-fullname></state><district>Delegate</district>
</member-info></member>
</members></MemberData>"""


def _fec_row(day, state, office, code, label, district="07", note=None, year=None):
    return {"election_date": day, "election_state": state, "office_sought": office,
            "election_type_id": code, "election_type_full": label, "election_district": district,
            "election_notes": note, "election_year": year or int(day[:4])}


def _special(db, **kw):
    fields = dict(id="2027-03-02-TX-H-7-SG", election_date="2027-03-02", election_year=2027, state="TX",
                  office="H", district=7, type_code="SG", type_label="Special election general",
                  is_special=True, listed_on="2027-01-10", official=False)
    fields.update(kw)
    row = FederalElectionDate(**fields)
    db.add(row)
    db.commit()
    return row


class TestReadingTheSources:
    def test_house_vacancy_from_the_clerks_list(self):
        assert fetch.parse_house_vacancies(MEMBER_DATA) == [{
            "id": "H-TX-23", "office": "H", "state": "TX", "district": 23, "senate_class": None,
            "predecessor": "A Member", "predecessor_party": "R", "vacated_on": "2026-04-14",
            "footnote": "Vacancy due to the resignation of A Member, April 14, 2026.",
            "source_url": fetch.MEMBER_DATA_URL,
        }]

    def test_an_unreadable_or_memberless_list_is_not_no_vacancies(self):
        assert fetch.parse_house_vacancies(b"<html>") is None
        assert fetch.parse_house_vacancies(b"<MemberData><members/></MemberData>") is None

    def test_senate_vacancy_is_a_class_seat_with_nobody_in_it(self):
        xml = b"""<contact_information>
        <member><state>OH</state><class>Class I</class></member>
        <member><state>AK</state><class>Class II</class></member>
        <member><state>AK</state><class>Class III</class></member></contact_information>"""
        classes = {1: frozenset({"OH"}), 2: frozenset({"AK"}), 3: frozenset({"AK", "OH"})}
        assert [v["id"] for v in fetch.parse_senate_vacancies(xml, classes)] == ["S-OH-3"]

    def test_a_state_with_no_senator_at_all_is_a_list_cut_short(self):
        xml = b"<contact_information><member><state>AK</state><class>Class II</class></member></contact_information>"
        assert fetch.parse_senate_vacancies(xml, {1: frozenset({"OH"}), 2: frozenset({"AK"}),
                                                  3: frozenset({"AK"})}) is None

    def test_specials_are_stored_and_a_complete_read_retracts_only_uncounted_rows(self, db_session):
        rows = [_fec_row("2027-03-02", "TX", "H", "SG", "Special election general", note="Rep. A's Seat."),
                _fec_row("2027-01-20", "TX", "H", "SP", "Special primary"),
                _fec_row("2027-11-02", "VA", "S", "G", "General election", district=" ")]
        new = fetch.save_election_rows(db_session, 2027, rows, complete=True, read_on="2027-01-10")
        assert sorted(new) == ["2027-01-20-TX-H-7-SP", "2027-03-02-TX-H-7-SG", "2027-11-02-VA-S-0-G"]
        sg = db_session.get(FederalElectionDate, "2027-03-02-TX-H-7-SG")
        assert sg.is_special and sg.note == "Rep. A's Seat." and sg.district == 7
        assert not db_session.get(FederalElectionDate, "2027-11-02-VA-S-0-G").is_special
        sg.votes_counted = 100
        db_session.commit()
        fetch.save_election_rows(db_session, 2027, rows[2:], complete=True, read_on="2027-01-11")
        assert db_session.get(FederalElectionDate, "2027-03-02-TX-H-7-SG") is not None  # held: its count stays
        assert db_session.get(FederalElectionDate, "2027-01-20-TX-H-7-SP") is None

    def test_an_incomplete_read_retracts_nothing(self, db_session):
        fetch.save_election_rows(db_session, 2027, [_fec_row("2027-01-20", "TX", "H", "SP", "Special primary")],
                                 complete=True, read_on="2027-01-10")
        fetch.save_election_rows(db_session, 2027, [], complete=False, read_on="2027-01-11")
        assert db_session.get(FederalElectionDate, "2027-01-20-TX-H-7-SP") is not None

    def test_reporting_deadlines_grouped_and_only_single_date_types_shown(self, db_session):
        rows = [{"due_date": "2027-04-15", "report_type": "Q1", "report_type_full": "APRIL QUARTERLY",
                 "report_year": 2027}] + [
            {"due_date": d, "report_type": "12P", "report_type_full": "PRE-PRIMARY", "report_year": 2027}
            for d in ("2027-01-02", "2027-06-01", "2027-06-01")]
        fetch.save_reporting_deadlines(db_session, 2027, rows, complete=True, read_on="2027-01-10")
        assert db_session.get(FecReportingDeadline, "2027-06-01-12P").rows == 2
        # Only one pre-primary date is in a window starting after January 2,
        # but the type is due on two dates in its year: not shown as a date.
        shown = se._national_deadlines(
            db_session, db_session.query(FecReportingDeadline).filter(
                FecReportingDeadline.due_date > "2027-01-05").all())
        assert [r.report_type for r in shown] == ["Q1"]


class TestPickingTheSpecial:
    def test_the_lone_special_named_entry(self):
        assert pick_special([("Local Bond", "a"), ("Special General Election", "b")]) == "b"

    def test_the_only_election_that_day(self):
        assert pick_special([("Congressional District 7 General Election", "a")]) == "a"

    def test_demo_copies_drop_out_and_an_ambiguous_day_is_refused(self):
        assert pick_special([("Special Election DEMO", "x"), ("Special Election", "a")]) == "a"
        with pytest.raises(UntrustedCount):
            pick_special([("City Election", "a"), ("County Election", "b")], "TX")
        assert pick_special([]) is None

    def test_a_general_day_still_reads_the_general(self):
        assert pick_election([("2026 General", "g"), ("Special", "s")], "TX", date(2026, 11, 3)) == "g"
        assert pick_election([("2027 General", "g")], "TX", date(2027, 3, 2)) == "g"


def _read(*contests, updated=None, official=False):
    return StateRead("read", count=StateCount(
        source_name="TX results", page_url="https://results.example/tx", official=official,
        contests=list(contests), source_updated=updated))


class TestTheSpecialsCount:
    def test_the_seats_contest_is_stored(self, db_session, freeze_utcnow):
        freeze_utcnow(datetime(2027, 3, 3, 4))
        row = _special(db_session)
        status = live.apply_special(db_session, row, _read(
            ContestCount(office="H", district=7, candidates=[("Ann Doe", "D", 40), ("Bo Roe", "R", 60)],
                         reporting_units=10, total_units=10),
            ContestCount(office="H", district=8, candidates=[("X", "D", 5)]),
        ))
        assert status == "ok"
        assert json.loads(row.tallies)[0] == {"name": "Bo Roe", "party": "R", "votes": 60}
        assert row.votes_counted == 100 and row.last_change_at == datetime(2027, 3, 3, 4)

    def test_two_contests_for_the_seat_are_refused(self, db_session):
        row = _special(db_session)
        c = ContestCount(office="H", district=7, candidates=[("A", "D", 1)])
        assert live.apply_special(db_session, row, _read(c, c)) == "ambiguous"
        assert row.votes_counted is None

    def test_a_feed_that_goes_back_is_refused(self, db_session):
        row = _special(db_session, source_updated_at=datetime(2027, 3, 3, 4), votes_counted=5, tallies="[]")
        c = ContestCount(office="H", district=7, candidates=[("A", "D", 1)])
        assert live.apply_special(db_session, row, _read(c, updated=datetime(2027, 3, 3, 3))) == "stale"
        assert row.votes_counted == 5

    def test_on_a_general_day_only_a_contest_marked_special_counts(self, db_session):
        row = _special(db_session, id="2026-11-03-TX-H-7-SG", election_date="2026-11-03")
        regular = ContestCount(office="H", district=7, candidates=[("A", "D", 1)])
        assert live.apply_special(db_session, row, _read(regular)) == "not_listed"

    def test_a_counted_contest_the_feed_drops_keeps_its_count_and_pages(self, db_session, monkeypatch):
        sent = []
        monkeypatch.setattr(live, "send_ops_alert", lambda *a, **k: sent.append(k["dedupe_key"]))
        row = _special(db_session, votes_counted=5, tallies="[]")
        read = _read(ContestCount(office="H", district=8, candidates=[("X", "D", 1)]))
        assert live.apply_special(db_session, row, read) == "not_listed"
        live._alerts(row, "ok", read)
        assert row.votes_counted == 5 and "special-dropped-2027-03-02-TX-H-7-SG" in sent

    def test_window_and_types(self, db_session, monkeypatch):
        monkeypatch.setattr(live, "live_results_states", lambda: {"TX"})
        _special(db_session)
        _special(db_session, id="p", type_code="SP")
        _special(db_session, id="far", election_date="2027-01-02")
        due = live.due_specials(db_session, today=date(2027, 3, 5), now=datetime(2027, 3, 5, 12))
        assert [r.id for r in due] == ["2027-03-02-TX-H-7-SG"]
        assert live.due_specials(db_session, today=date(2027, 3, 1), now=datetime(2027, 3, 1, 12)) == []


class TestSwing:
    def _usual(self, monkeypatch, lean=8.0):
        monkeypatch.setattr(se, "usual_vote", lambda row: {"lean": lean, "label": f"R+{lean:g}",
                                                           "source": "computed", "congress": 120})

    def test_measured_once_settled(self, db_session, monkeypatch):
        self._usual(monkeypatch)
        row = _special(db_session, tallies=json.dumps([{"name": "A", "party": "R", "votes": 48},
                                                       {"name": "B", "party": "D", "votes": 52}]),
                       votes_counted=100, reporting_units=10, total_units=10, units_complete=True)
        s = se.swing(row)
        assert (s["result"], s["swing"], s["toward"], s["final"]) == (-2.0, -10.0, "D", False)
        assert s["sentence"] == ("The result was D+2 against a usual vote of R+8: 10 points toward Democrats, "
                                 "on the count as it stands (not final).")

    def test_not_measured_unsettled_or_without_both_parties(self, db_session, monkeypatch):
        self._usual(monkeypatch)
        partial = _special(db_session, tallies=json.dumps([{"name": "A", "party": "R", "votes": 1},
                                                           {"name": "B", "party": "D", "votes": 1}]),
                           votes_counted=2, reporting_units=3, total_units=10, units_complete=True)
        assert se.swing(partial) is None
        same_party = _special(db_session, id="b", official=True, votes_counted=2,
                              tallies=json.dumps([{"name": "A", "party": "D", "votes": 1},
                                                  {"name": "B", "party": "D", "votes": 1}]))
        assert se.swing(same_party) is None


class TestBoardAndCalendar:
    def test_a_fixture_vacancy_shows_with_its_scheduled_special(self, db_session, monkeypatch):
        monkeypatch.setattr(se, "_covered", lambda st: True)
        _special(db_session)
        db_session.add(SeatVacancy(id="H-TX-7", office="H", state="TX", district=7, predecessor="A Member",
                                   predecessor_party="R", vacated_on="2026-12-01", source_url="u"))
        db_session.commit()
        board = se.specials_board(db_session, now=datetime(2027, 2, 1, 12))
        [v] = board["vacancies"]
        assert v["seat"] == "TX-7" and v["nextSpecial"]["date"] == "2027-03-02"
        assert board["upcoming"][0]["status"] == "upcoming" and board["upcoming"][0]["daysLeft"] == 29

    def test_an_empty_board_names_the_last_special(self, db_session, monkeypatch):
        monkeypatch.setattr(se, "_covered", lambda st: False)
        _special(db_session, election_date="2025-04-01", id="old")
        board = se.specials_board(db_session, now=datetime(2027, 2, 1, 12))
        assert board["upcoming"] == [] and board["recent"] == []
        assert board["lastSpecial"]["date"] == "2025-04-01" and board["lastSpecial"]["liveCount"] == "not_covered"

    def test_election_day_status_is_on_the_states_clock(self):
        # Texas's last polls close in its Mountain-time corner: at 03:00 UTC
        # it is still election day there, polls closed; the morning of it,
        # voting; the UTC date alone would call both "the next day".
        assert se.day_status("TX", date(2027, 3, 2), datetime(2027, 3, 3, 3)) == (se.POLLS_CLOSED, 0)
        assert se.day_status("TX", date(2027, 3, 2), datetime(2027, 3, 2, 15)) == (se.VOTING, 0)
        assert se.day_status("TX", date(2027, 3, 2), datetime(2027, 3, 1, 15)) == (se.UPCOMING, 1)

    def test_endpoints(self, db_session, freeze_utcnow):
        freeze_utcnow(datetime(2027, 2, 1, 12))
        _special(db_session)
        app.dependency_overrides[get_db] = lambda: db_session
        try:
            client = TestClient(app)
            specials = client.get("/api/elections/specials").json()
            cal = client.get("/api/elections/calendar").json()
        finally:
            app.dependency_overrides.clear()
        assert specials["upcoming"][0]["id"] == "2027-03-02-TX-H-7-SG"
        assert specials["season"]["betweenCycles"] is True
        assert cal["elections"][0]["special"] is True and cal["nextDate"] == "2027-03-02"


def test_new_upcoming_special_generals_go_to_the_feed_only(db_session, bluesky_outbox):
    from app.models import BroadcastPost

    _special(db_session)
    _special(db_session, id="sp", type_code="SP")
    assert se.publish_new_specials(db_session, ["2027-03-02-TX-H-7-SG", "sp"], today=date(2027, 2, 1)) == 1
    assert se.publish_new_specials(db_session, ["2027-03-02-TX-H-7-SG"], today=date(2027, 2, 1)) == 0
    [post] = db_session.query(BroadcastPost).all()
    assert post.kind == "special" and post.bsky_status == "off" and post.state == "TX"
    assert post.text == "TX-7: special election general on March 2, 2027, as the FEC's election calendar lists it."


async def test_a_year_the_fec_counts_as_empty_is_read_not_failed(monkeypatch):
    from app.pipeline.fetch.state_election_dates import fetch_fec_rows

    async def empty(client, url):
        return {"results": [], "pagination": {"count": 0, "pages": 0}}

    async def down(client, url):
        return None

    monkeypatch.setattr("app.pipeline.fetch.fec._fetch_with_retry", empty)
    assert await fetch_fec_rows(None, 2027) == ([], True)
    monkeypatch.setattr("app.pipeline.fetch.fec._fetch_with_retry", down)
    assert await fetch_fec_rows(None, 2027) == ([], False)
