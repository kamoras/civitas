"""app.next_cycle: the next cycle's FEC registrations, the FEC deadline event
and how the sitting Congress compares, all counted from stored records."""

import json
from datetime import date

import pytest

from app import next_cycle as nc
from app.election_calendar import seats_up_for_year, senate_classes
from app.models import ApiCache, Candidate, FecReportingDeadline, Race, Representative, Senator
from app.pipeline.fetch.congress import member_detail_cache_key

CYCLE = 2028


def _race(db, rid, office, state, district=None):
    db.add(Race(id=rid, cycle_year=CYCLE, office=office, state=state, district=district, is_special=False))


def _cand(db, cid, race_id, member=None, ic="C"):
    db.add(Candidate(id=cid, race_id=race_id, name=cid, party="DEM", incumbent_challenge=ic,
                     member_bioguide=member))


@pytest.fixture
def senate_state():
    return sorted(seats_up_for_year(CYCLE))[0]


class TestField:
    def test_counts_registrations_never_ballot_places(self, db_session, senate_state):
        db_session.add_all([
            Representative(id="r1", bioguide_id="B1", name="One", state="OH", district=1, party="R"),
            Representative(id="r2", bioguide_id="B2", name="Two", state="OH", district=2, party="D"),
            Representative(id="r3", bioguide_id="B3", name="Three", state="OH", district=3, party="D"),
            Senator(id="s1", bioguide_id="S1", name="Sen", state=senate_state, party="R"),
            Senator(id="s2", bioguide_id="S2", name="Sen Two", state="OH", party="D"),
        ])
        _race(db_session, "2028-HOUSE-OH-1", "H", "OH", 1)
        _race(db_session, "2028-HOUSE-OH-3", "H", "OH", 3)
        _race(db_session, f"2028-SENATE-{senate_state}", "S", senate_state)
        _cand(db_session, "H8OH01001", "2028-HOUSE-OH-1", member="B1", ic="I")
        _cand(db_session, "S8XX00001", f"2028-SENATE-{senate_state}", member="B2")
        _cand(db_session, "S8XX00002", f"2028-SENATE-{senate_state}", member="S1", ic="I")
        _cand(db_session, "H8OH03001", "2028-HOUSE-OH-3", ic="O")
        # A senator registered for the presidency (the national race).
        db_session.add(Race(id="2028-PRES", cycle_year=CYCLE, office="P", state="US", is_special=False))
        _cand(db_session, "P80000001", "2028-PRES", member="S2")
        # A state's ballot-only candidate is no FEC registration.
        _cand(db_session, "ballot:2028-HOUSE-OH-3:Someone", "2028-HOUSE-OH-3")
        db_session.commit()

        f = nc.field(db_session, CYCLE)
        assert f["registered"] == 5 and f["openSeats"] == 1
        assert f["house"] == {"members": 3, "filedForHouse": 1, "noFiling": 1}
        assert f["filedForOtherOffice"] == [
            {"id": "r2", "name": "Two", "party": "D", "state": "OH", "district": 2, "chamber": "H",
             "office": "S", "raceId": f"2028-SENATE-{senate_state}"},
            {"id": "s2", "name": "Sen Two", "party": "D", "state": "OH", "district": None, "chamber": "S",
             "office": "P", "raceId": "2028-PRES"},
        ]
        assert f["senate"]["seatsUp"] == len(seats_up_for_year(CYCLE))
        assert f["senate"]["incumbentFiled"] == 1 and senate_state not in f["senate"]["noIncumbentFiling"]

    def test_an_empty_cycle_is_counted_as_none_filed(self, db_session):
        f = nc.field(db_session, CYCLE)
        assert f["registered"] == 0 and f["senate"]["incumbentFiled"] == 0
        assert len(f["senate"]["noIncumbentFiling"]) == len(seats_up_for_year(CYCLE))
        assert set(f["senate"]["noIncumbentFiling"]) <= frozenset().union(*senate_classes().values())


class TestDeadlineEvent:
    def _deadline(self, db, due, kind="Q1", label="April Quarterly"):
        db.add(FecReportingDeadline(id=f"{due}-{kind}", due_date=due, report_type=kind, label=label,
                                    report_year=int(due[:4]), read_on="2027-01-01"))
        db.commit()

    def test_none_stored(self, db_session):
        assert nc.deadline_event(db_session, date(2027, 4, 1)) is None

    @pytest.mark.parametrize("today, status, leads", [
        (date(2027, 3, 1), "upcoming", False),
        (date(2027, 4, 1), "upcoming", True),
        (date(2027, 4, 15), "due_today", True),
        (date(2027, 4, 18), "passed", True),
    ])
    def test_around_its_day(self, db_session, today, status, leads):
        self._deadline(db_session, "2027-04-15")
        self._deadline(db_session, "2027-07-15", kind="Q2", label="July Quarterly")
        e = nc.deadline_event(db_session, today)
        assert (e["date"], e["status"], e["leads"]) == ("2027-04-15", status, leads)

    def test_moves_on_after_the_window(self, db_session):
        self._deadline(db_session, "2027-04-15")
        self._deadline(db_session, "2027-07-15", kind="Q2", label="July Quarterly")
        e = nc.deadline_event(db_session, date(2027, 4, 19))
        assert e["date"] == "2027-07-15" and e["leads"] is False and e["daysLeft"] == 87

    def test_a_per_state_deadline_is_never_the_event(self, db_session):
        # A pre-primary report falls on many dates, one per state's primary.
        self._deadline(db_session, "2027-04-10", kind="12P", label="Pre-Primary")
        self._deadline(db_session, "2027-05-10", kind="12P", label="Pre-Primary")
        self._deadline(db_session, "2027-04-15")
        assert nc.deadline_event(db_session, date(2027, 4, 1))["reportType"] == "Q1"


class TestNewCongress:
    def _member(self, db, model, bid, terms, birth=None, **kw):
        db.add(model(id=bid.lower(), bioguide_id=bid, name=bid, party="D", **kw))
        record = {"terms": terms}
        if birth:
            record["birthYear"] = str(birth)
        db.add(ApiCache(tier="congress", cache_key=member_detail_cache_key(bid), data_json=json.dumps(record)))

    def test_counts_new_returning_and_switched(self, db_session):
        house, senate = "House of Representatives", "Senate"
        self._member(db_session, Representative, "H1", [{"chamber": house, "congress": 120}], 1980,
                     state="OH", district=1)
        self._member(db_session, Representative, "H2", [{"chamber": house, "congress": c} for c in (118, 119, 120)],
                     1960, state="OH", district=2)
        self._member(db_session, Representative, "H3", [{"chamber": house, "congress": c} for c in (115, 120)],
                     state="OH", district=3)
        db_session.add(Representative(id="h4", bioguide_id="H4", name="H4", party="R", state="OH", district=4))
        # A record stored before Congress.gov added the 120th: not read yet.
        self._member(db_session, Representative, "H5", [{"chamber": house, "congress": 119}], 1950,
                     state="OH", district=5)
        self._member(db_session, Senator, "S1",
                     [{"chamber": house, "congress": 118}, {"chamber": house, "congress": 119},
                      {"chamber": senate, "congress": 120}], 1970, state="OH")
        db_session.commit()

        out = nc.new_congress(db_session, 120)
        assert out["firstYear"] == 2027
        h = out["house"]
        assert (h["members"], h["read"], h["new"], h["returning"], h["fromOtherChamber"]) == (5, 3, 1, 1, 0)
        assert h["newShare"] == round(1 / 3, 3) and h["medianAge"] == 2027 - 1970 and h["ages"] == 2
        s = out["senate"]
        assert (s["new"], s["fromOtherChamber"], s["medianAge"]) == (1, 1, 57)

    def test_no_record_is_unread_not_zero(self, db_session):
        db_session.add(Senator(id="x", bioguide_id="X1", name="X", party="D", state="OH"))
        db_session.commit()
        s = nc.new_congress(db_session, 120)["senate"]
        assert s["read"] == 0 and s["newShare"] is None and s["medianAge"] is None
