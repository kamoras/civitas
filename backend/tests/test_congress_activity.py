"""The /congress record: Daily Digest and floor-log parsing against real
issues (tests/fixtures, public domain), and the sync's handling of absent
versus failed sources."""

import asyncio
import json
from datetime import date

import pytest

from app.models import CongressDay, CongressEvent, RollCall, RollCallPosition
from app.pipeline import congress_activity as ca
from app.pipeline.fetch import daily_digest as dd
from app.pipeline.fetch import floor_logs as fl

from tests.congress_activity_helpers import FIX, _digest_responses, _fake_get


def _digest(name: str) -> str:
    return dd.digest_text((FIX / "daily_digest" / f"{name}.htm").read_text())


def _events(action: dict, kind: str) -> list[dict]:
    return [e for e in action["events"] if e["kind"] == kind]


class TestDigestSenate:
    def test_a_day_of_senate_business(self):
        # 2026-09-24: three bills and four resolutions passed, the Iran war
        # powers resolution failed 49-50, one farm bill reported.
        a = dd.parse_chamber_action(_digest("CREC-2026-09-24-pt1-PgD935"))
        passed = _events(a, "passed")
        assert [e["bill_id"] for e in passed] == [
            "S.3257", "S.3258", "HR.2388", "SRES.902", "SRES.903", "SRES.904", "SRES.905",
        ]
        assert passed[0]["name"] == "John A. Hauser Mental Health in Aviation Act"
        # The substitute amendment line is S. 3257's detail, not an event.
        assert "Amendment No. 6834" in passed[0]["text"]
        assert passed[0]["text"].startswith("Senate passed S. 3257, to require the Administrator")
        failed = _events(a, "failed")
        assert [(e["bill_id"], e["name"]) for e in failed] == [("HCONRES.89", "Hostilities with Iran")]
        assert [e["bill_id"] for e in _events(a, "reported")] == ["S.5526"]
        assert (a["bills_introduced"], a["resolutions_introduced"]) == (74, 14)
        assert (a["convened_at"], a["adjourned_at"]) == ("10 a.m.", "4:05 p.m.")

    def test_reported_items_ending_in_a_report_number_stay_separate(self):
        a = dd.parse_chamber_action(_digest("CREC-2026-09-23-pt1-PgD929"))
        assert [e["bill_id"] for e in _events(a, "reported")] == [None, "S.1055", "S.3219", "S.3313"]
        assert len(_events(a, "passed")) == 14

    def test_confirmations_keep_one_entry_per_page_reference(self):
        a = dd.parse_chamber_action(_digest("CREC-2026-09-23-pt1-PgD929"))
        confirmed = [e["text"] for e in _events(a, "confirmed")]
        assert confirmed[0].startswith("By 50 yeas to 47 nays (Vote No. EX. 241), Angela Veronica Colmenero")
        assert confirmed[1] == "3 Coast Guard nominations in the rank of admiral. A routine list in the Coast Guard."

    def test_an_adjournment_in_memory_of_a_senator_still_reads_its_time(self):
        a = dd.parse_chamber_action(_digest("CREC-2026-09-23-pt1-PgD929"))
        assert a["adjourned_at"] == "7:28 p.m."

    def test_not_in_session_is_a_finding(self):
        a = dd.parse_chamber_action(_digest("CREC-2026-09-21-pt1-PgD921"))
        assert a["in_session"] is False
        assert a["adjournment_text"].startswith("The Senate was not in session")
        assert a["events"] == []


class TestDigestHouse:
    def test_a_day_of_house_business(self):
        a = dd.parse_chamber_action(_digest("CREC-2026-09-16-pt1-PgD906"))
        passed = _events(a, "passed")
        ids = [e["bill_id"] for e in passed]
        # Measures taken up one by one, each its own heading ...
        assert ids[:4] == ["HR.5334", "HRES.1543", "HR.9576", "HR.10326"]
        # ... a multi-line heading kept whole ...
        assert passed[1]["name"].startswith("Recommending that the House of Representatives find Leon D. Black")
        # ... and every measure passed under suspension of the rules.
        assert "HR.9497" in ids and "HR.8193" in ids and "S.766" in ids
        assert len(passed) == 23
        assert (a["bills_introduced"], a["resolutions_introduced"]) == (72, 17)
        assert (a["convened_at"], a["adjourned_at"]) == ("9 a.m.", "9:59 p.m.")

    def test_reports_filed_after_a_semicolon_and(self):
        a = dd.parse_chamber_action(_digest("CREC-2026-09-24-pt1-PgD937"))
        assert [e["bill_id"] for e in _events(a, "reported")] == ["HR.8052", "HR.5436", "HR.4986"]

    def test_house_not_in_session(self):
        a = dd.parse_chamber_action(_digest("CREC-2026-09-22-pt1-PgD925-2"))
        assert a["in_session"] is False


class TestDigestOtherGranules:
    def test_committee_meetings_across_a_page_break(self):
        c = dd.parse_committee_meetings(_digest("CREC-2026-09-24-pt1-PgD936"))
        assert [e["name"] for e in c][:3] == [
            "Committee on Armed Services", "Committee on Finance", "Committee on Foreign Relations",
        ]
        # The sentence runs over [[Page D937]] and is kept whole.
        help_meeting = next(e for e in c if "Health, Education" in e["name"])
        assert "Commissioner of Food and Drugs" in help_meeting["text"]

    def test_no_committee_meetings(self):
        assert dd.parse_committee_meetings(_digest("CREC-2026-09-24-pt1-PgD937-2")) == []

    def test_next_meetings(self):
        n = dd.parse_next_meetings(_digest("CREC-2026-09-24-pt1-PgD937-4"))
        assert n["senate"]["when"] == "3 p.m., Monday, September 28"
        assert "vote on passage of the bill, as amended" in n["senate"]["program"]
        assert n["house"]["program"] == "Program for Monday: House will meet in Pro Forma session at 12 noon."

    @pytest.mark.parametrize("title,role", [
        ("Daily Digest/Senate", ("senate", "floor")),
        # A day's first granule carries the Highlights with its chamber.
        ("Daily Digest/Highlights + Senate", ("senate", "floor")),
        ("Daily Digest/Highlights + House of Representatives", ("house", "floor")),
        ("Daily Digest/House of Representatives", ("house", "floor")),
        ("Daily Digest/Senate Committee Meetings", ("senate", "committees")),
        ("Daily Digest/House Committee Meetings", ("house", "committees")),
        ("Daily Digest/Next Meeting of the SENATE + Next Meeting of the HOUSE OF REPRESENTATIVES", ("both", "next")),
        ("Daily Digest/COMMITTEE MEETINGS FOR 2026-09-28", None),
    ])
    def test_granule_roles(self, title, role):
        assert dd.granule_role(title) == role


@pytest.mark.parametrize("text,n", [
    ("Seventy-four", 74), ("fourteen", 14), ("85", 85), ("One hundred twelve", 112),
    ("two hundred and five", 205), ("a", 1), ("several", None),
])
def test_words_to_int(text, n):
    assert dd.words_to_int(text) == n


@pytest.mark.parametrize("text,bill", [
    ("Senate passed S. 3257, to", "S.3257"), ("agree to H. Con. Res. 89,", "HCONRES.89"),
    ("agreed to S. Res. 902", "SRES.902"), ("H.J. Res. 213", "HJRES.213"), ("no measure", None),
])
def test_first_bill_id(text, bill):
    assert dd.first_bill_id(text) == bill


class TestFloorLogs:
    def test_house_floor_log(self):
        h = fl.parse_house_floor((FIX / "floor_logs" / "house_20260924.xml").read_bytes())
        assert h["finished"] is True
        assert h["next_meeting_iso"] == "20260928T12:00"
        assert (h["convened_at"], h["adjourned_at"]) == ("2:30:00 P.M.", "2:33:35 P.M.")
        assert h["events"][0]["text"] == "The House convened, starting a new legislative day."

    def test_busy_house_day_names_its_bills(self):
        h = fl.parse_house_floor((FIX / "floor_logs" / "house_20260916.xml").read_bytes())
        assert len(h["events"]) == 170
        assert any(e["bill_id"] == "HRES.1544" for e in h["events"])

    def test_senate_floor_log(self):
        s = fl.parse_senate_floor((FIX / "floor_logs" / "09_24_2026_Senate_Floor.xml").read_bytes())
        assert s["in_session"] is True  # a meeting day (cf. the day-off file below)
        assert (s["convened_at"], s["adjourned_at"]) == ("10 a.m.", "4:05 p.m.")
        iran = next(e for e in s["events"] if e["bill_id"] == "HCONRES.89")
        assert "Failed of passage in Senate by Yea-Nay Vote. 49 - 50" in iran["text"]

    @pytest.mark.parametrize("text,bill", [
        ("H R 9576", "HR.9576"), ("H RES 1543", "HRES.1543"), ("H CON RES 5", "HCONRES.5"),
        ("H.Con.Res. 89", "HCONRES.89"), ("S. 4668", "S.4668"), ("PN123", None), ("", None),
    ])
    def test_bill_ids_in_chamber_spellings(self, text, bill):
        assert fl.bill_id_from_number(text) == bill

    def test_tally_counts_positions(self):
        members = [{"voteCast": v} for v in ["Yea", "Aye", "Nay", "No", "Present", "Not Voting"]]
        assert fl.tally(members) == {"yeas": 2, "nays": 2, "present": 1, "not_voting": 1}


def test_senate_vote_date():
    assert ca._senate_vote_date("September 24, 2026,  11:46 AM") == "2026-09-24"


def test_next_meeting_from_iso_reads_like_the_digest():
    assert ca._next_meeting_from_iso("20260928T12:00") == "12 noon, Monday, September 28"
    assert ca._next_meeting_from_iso("20260917T10:30") == "10:30 a.m., Thursday, September 17"


# ── The sync ──────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


class TestSyncDigest:
    def test_a_digest_makes_both_chambers_final(self, db_session, monkeypatch):
        monkeypatch.setattr(ca, "_get", _fake_get(_digest_responses()))
        assert _run(ca.sync_digest(None, db_session, date(2026, 9, 24))) == "ok"
        senate = db_session.query(CongressDay).filter_by(chamber="senate", date="2026-09-24").one()
        assert senate.is_final and senate.in_session and senate.source == "digest"
        assert senate.next_meeting == "3 p.m., Monday, September 28"
        assert senate.bills_introduced == 74
        kinds = {e.kind for e in db_session.query(CongressEvent).filter_by(chamber="senate")}
        assert kinds == {"passed", "failed", "reported", "committee"}
        house = db_session.query(CongressDay).filter_by(chamber="house", date="2026-09-24").one()
        assert house.is_final and house.adjourned_at == "2:33 p.m."

    def test_a_chamber_missing_from_the_digest_did_not_meet(self, db_session, monkeypatch):
        listing = json.dumps({"granules": [
            {"granuleId": "CREC-2026-09-21-pt1-PgD921", "granuleClass": "DAILYDIGEST", "title": "Daily Digest/Senate"},
        ]}).encode()
        monkeypatch.setattr(ca, "_get", _fake_get({
            "api.govinfo.gov/packages/CREC-2026-09-21/granules": listing,
            "PgD921.htm": (FIX / "daily_digest" / "CREC-2026-09-21-pt1-PgD921.htm").read_bytes(),
        }))
        assert _run(ca.sync_digest(None, db_session, date(2026, 9, 21))) == "ok"
        house = db_session.query(CongressDay).filter_by(chamber="house", date="2026-09-21").one()
        assert (house.in_session, house.is_final, house.source) == (False, True, "digest")

        # Final for both chambers, so the recent-days pass leaves the day alone.
        asked = []

        async def fake_digest(client, db, day):
            asked.append(day)
            return "absent"

        monkeypatch.setattr(ca, "sync_digest", fake_digest)
        monkeypatch.setattr(ca, "_DIGEST_BACKFILL_BATCH", 0)
        _run(ca.sync_digests(None, db_session, date(2026, 9, 22)))
        assert date(2026, 9, 21) not in asked and date(2026, 9, 20) in asked

    def test_a_floor_log_that_says_a_chamber_met_is_not_overwritten(self, db_session, monkeypatch):
        db_session.add(CongressDay(chamber="house", date="2026-09-21", in_session=True, source="floor_log"))
        db_session.commit()
        listing = json.dumps({"granules": [
            {"granuleId": "CREC-2026-09-21-pt1-PgD921", "granuleClass": "DAILYDIGEST", "title": "Daily Digest/Senate"},
        ]}).encode()
        monkeypatch.setattr(ca, "_get", _fake_get({
            "api.govinfo.gov/packages/CREC-2026-09-21/granules": listing,
            "PgD921.htm": (FIX / "daily_digest" / "CREC-2026-09-21-pt1-PgD921.htm").read_bytes(),
        }))
        _run(ca.sync_digest(None, db_session, date(2026, 9, 21)))
        house = db_session.query(CongressDay).filter_by(chamber="house", date="2026-09-21").one()
        assert (house.in_session, house.is_final, house.source) == (True, False, "floor_log")

    def test_the_highlights_granule_is_the_senate_floor(self, db_session, monkeypatch):
        listing = json.dumps({"granules": [
            {"granuleId": "CREC-2025-01-06-pt1-PgD14", "granuleClass": "DAILYDIGEST",
             "title": "Daily Digest/Highlights + Senate"},
        ]}).encode()
        monkeypatch.setattr(ca, "_get", _fake_get({
            "api.govinfo.gov/packages/CREC-2025-01-06/granules": listing,
            "PgD14.htm": (FIX / "daily_digest" / "CREC-2025-01-06-pt1-PgD14.htm").read_bytes(),
        }))
        assert _run(ca.sync_digest(None, db_session, date(2025, 1, 6))) == "ok"
        senate = db_session.query(CongressDay).filter_by(chamber="senate", date="2025-01-06").one()
        assert senate.in_session and senate.is_final
        assert senate.adjourned_at == "1:36 p.m."

    def test_an_empty_listing_is_absent(self, db_session, monkeypatch):
        # GovInfo's answer for a day with no Record: 200, no granules.
        empty = json.dumps({"count": 0, "granules": []}).encode()
        monkeypatch.setattr(ca, "_get", _fake_get({"api.govinfo.gov": empty}))
        assert _run(ca.sync_digest(None, db_session, date(2025, 1, 4))) == "absent"
        assert db_session.query(CongressDay).count() == 0

    def test_no_record_that_day_is_absent(self, db_session, monkeypatch):
        monkeypatch.setattr(ca, "_get", _fake_get({"api.govinfo.gov": ca._ABSENT}))
        assert _run(ca.sync_digest(None, db_session, date(2026, 9, 26))) == "absent"
        assert db_session.query(CongressDay).count() == 0

    def test_a_failed_granule_writes_nothing(self, db_session, monkeypatch):
        responses = _digest_responses()
        responses["CREC-2026-09-24-pt1-PgD936.htm"] = None  # the fetch failed
        monkeypatch.setattr(ca, "_get", _fake_get(responses))
        assert _run(ca.sync_digest(None, db_session, date(2026, 9, 24))) == "failed"
        assert db_session.query(CongressDay).count() == 0

    def test_the_backfill_cursor_stops_before_a_failed_day(self, db_session, monkeypatch):
        outcomes = {date(2025, 1, 3): "ok", date(2025, 1, 4): "absent", date(2025, 1, 5): "failed"}

        async def fake_digest(client, db, day):
            return outcomes.get(day, "ok")

        monkeypatch.setattr(ca, "sync_digest", fake_digest)
        _run(ca.sync_digests(None, db_session, date(2026, 9, 27)))
        assert ca.digest_cursor(db_session) == date(2025, 1, 4)


class TestSyncFloorLogs:
    def test_a_live_day_then_the_digest_keeps_the_final_row(self, db_session, monkeypatch):
        monkeypatch.setattr(ca, "_get", _fake_get({
            "clerk.house.gov": (FIX / "floor_logs" / "house_20260924.xml").read_bytes(),
            "senate.gov": (FIX / "floor_logs" / "09_24_2026_Senate_Floor.xml").read_bytes(),
        }))
        assert _run(ca.sync_floor_logs(None, db_session, date(2026, 9, 24))) == {"house": "ok", "senate": "ok"}
        house = db_session.query(CongressDay).filter_by(chamber="house").one()
        assert (house.source, house.is_final, house.next_meeting) == ("floor_log", False, "12 noon, Monday, September 28")

        monkeypatch.setattr(ca, "_get", _fake_get(_digest_responses()))
        _run(ca.sync_digest(None, db_session, date(2026, 9, 24)))
        monkeypatch.setattr(ca, "_get", _fake_get({
            "clerk.house.gov": (FIX / "floor_logs" / "house_20260924.xml").read_bytes(),
            "senate.gov": ca._ABSENT,
        }))
        _run(ca.sync_floor_logs(None, db_session, date(2026, 9, 24)))
        house = db_session.query(CongressDay).filter_by(chamber="house").one()
        assert (house.source, house.is_final) == ("digest", True)
        # The floor log's timed entries are still there beside the Digest's.
        assert db_session.query(CongressEvent).filter_by(chamber="house", source="floor_log").count() == 7

    def test_failed_and_absent_are_different(self, db_session, monkeypatch):
        monkeypatch.setattr(ca, "_get", _fake_get({"clerk.house.gov": None, "senate.gov": ca._ABSENT}))
        assert _run(ca.sync_floor_logs(None, db_session, date(2026, 9, 27))) == {"house": "failed", "senate": "absent"}


class TestSyncRollCalls:
    def test_stores_votes_and_positions_until_the_chamber_has_no_more(self, db_session, monkeypatch):
        vote = (FIX / "roll_calls" / "vote_119_2_00243.xml").read_bytes()
        monkeypatch.setattr(ca, "_get", _fake_get({"_00001.xml": vote, "_00002.xml": vote, "vote1192": ca._ABSENT}))
        stored, status = _run(ca.sync_roll_calls(None, db_session, "senate", 119, 2))
        assert (stored, status) == (2, "ok")
        rc = db_session.query(RollCall).filter_by(number=1).one()
        assert (rc.yeas, rc.nays, rc.not_voting, rc.bill_id, rc.date) == (74, 25, 1, "S.4668", "2026-09-24")
        assert db_session.query(RollCallPosition).filter_by(roll_call_id=rc.id).count() == 100

    def test_a_skipped_number_does_not_stall_the_run(self, db_session, monkeypatch):
        vote = (FIX / "roll_calls" / "house_2026_roll309.xml").read_bytes()
        monkeypatch.setattr(ca, "_get", _fake_get({
            "roll001.xml": vote, "roll002.xml": ca._ABSENT, "roll003.xml": vote, "clerk.house.gov": ca._ABSENT,
        }))
        stored, status = _run(ca.sync_roll_calls(None, db_session, "house", 119, 2))
        assert (stored, status) == (2, "ok")
        assert sorted(n for (n,) in db_session.query(RollCall.number)) == [1, 3]

    def test_the_clerks_error_page_past_the_last_roll_is_absent(self, db_session, monkeypatch):
        vote = (FIX / "roll_calls" / "house_2026_roll309.xml").read_bytes()
        error = b'<xml>Error sanitizing file "roll002.xml". Please try again.</xml>'
        monkeypatch.setattr(ca, "_get", _fake_get({"roll001.xml": vote, "clerk.house.gov": error}))
        assert _run(ca.sync_roll_calls(None, db_session, "house", 119, 2)) == (1, "ok")

    def test_a_failed_fetch_is_reported(self, db_session, monkeypatch):
        monkeypatch.setattr(ca, "_get", _fake_get({"clerk.house.gov": None}))
        assert _run(ca.sync_roll_calls(None, db_session, "house", 119, 2)) == (0, "failed")

    def test_a_late_file_passed_over_is_fetched_on_a_later_run(self, db_session, monkeypatch):
        vote = (FIX / "roll_calls" / "house_2026_roll309.xml").read_bytes()  # dated 2026-09-16
        monkeypatch.setattr(ca, "eastern_today", lambda: date(2026, 9, 20))
        monkeypatch.setattr(ca, "_get", _fake_get({
            "roll001.xml": vote, "roll002.xml": ca._ABSENT, "roll003.xml": vote, "clerk.house.gov": ca._ABSENT,
        }))
        _run(ca.sync_roll_calls(None, db_session, "house", 119, 2))
        # Number 2 is posted after the first run moved past it.
        monkeypatch.setattr(ca, "_get", _fake_get({
            "roll001.xml": vote, "roll002.xml": vote, "roll003.xml": vote, "clerk.house.gov": ca._ABSENT,
        }))
        stored, status = _run(ca.sync_roll_calls(None, db_session, "house", 119, 2))
        assert (stored, status) == (1, "ok")
        assert sorted(n for (n,) in db_session.query(RollCall.number)) == [1, 2, 3]

    def test_a_long_standing_gap_is_no_longer_asked_for(self, db_session, monkeypatch):
        vote = (FIX / "roll_calls" / "house_2026_roll309.xml").read_bytes()
        monkeypatch.setattr(ca, "_get", _fake_get({
            "roll001.xml": vote, "roll002.xml": ca._ABSENT, "roll003.xml": vote, "clerk.house.gov": ca._ABSENT,
        }))
        monkeypatch.setattr(ca, "eastern_today", lambda: date(2026, 9, 20))
        _run(ca.sync_roll_calls(None, db_session, "house", 119, 2))
        assert ca._gaps_to_retry(db_session, "house", 119, 2, date(2026, 9, 20)) == [2]
        assert ca._gaps_to_retry(db_session, "house", 119, 2, date(2026, 10, 1)) == []


class _Resp:
    def __init__(self, status, url, history=()):
        import httpx
        self.status_code, self.url, self.history, self.content = status, httpx.URL(url), list(history), b"<x/>"


@pytest.mark.parametrize("requested,landed,redirected,absent", [
    # senate.gov's "not found" pages
    ("https://www.senate.gov/legislative/LIS/floor_activity/09_27_2026_Senate_Floor.xml",
     "https://www.senate.gov/pagelayout/general/one_item_and_teasers/file_not_found.htm", True, True),
    ("https://www.senate.gov/legislative/LIS/roll_call_votes/vote1192/vote_119_2_00999.xml",
     "https://www.senate.gov/legislative/roll-call-vote-not-available.htm", True, True),
    # clerk.house.gov moving a file that exists
    ("https://clerk.house.gov/FloorSummary/20260924.xml", "https://clerk.house.gov/floor/20260924.xml", True, False),
    ("https://clerk.house.gov/floor/20260924.xml", "https://clerk.house.gov/floor/20260924.xml", False, False),
])
def test_a_redirect_is_absent_only_when_it_lands_on_another_kind_of_page(monkeypatch, requested, landed, redirected, absent):
    async def fake_fetch(*args, **kwargs):
        return _Resp(200, landed, history=[object()] if redirected else [])
    monkeypatch.setattr(ca, "fetch_with_retry", fake_fetch)
    body = asyncio.run(ca._get(None, requested, label="t"))
    assert (body is ca._ABSENT) is absent


def test_a_senate_vote_on_an_amendment_belongs_to_its_bill(db_session, monkeypatch):
    # Vote 242 (2026-09-24) adopted Amendment 6776 to S. 4668: the XML's
    # <document> is the bare "S.Amdt."; the bill is the amendment's target.
    vote = (FIX / "roll_calls" / "vote_119_2_00242.xml").read_bytes()
    monkeypatch.setattr(ca, "_get", _fake_get({"_00001.xml": vote, "vote1192": ca._ABSENT}))
    asyncio.run(ca.sync_roll_calls(None, db_session, "senate", 119, 2))
    assert db_session.query(RollCall).one().bill_id == "S.4668"


def test_a_house_suspension_needs_two_thirds():
    from app.pipeline.fetch.congress import parse_house_vote_xml

    text = (FIX / "roll_calls" / "house_2026_roll309.xml").read_text()
    assert parse_house_vote_xml(text, 2026, 309)["majorityRequirement"] == "1/2"
    suspension = text.replace("<vote-type>YEA-AND-NAY</vote-type>", "<vote-type>2/3 YEA-AND-NAY</vote-type>")
    assert parse_house_vote_xml(suspension, 2026, 309)["majorityRequirement"] == "2/3"


def test_house_votes_stored_without_a_requirement_are_repaired_once(db_session, monkeypatch):
    text = (FIX / "roll_calls" / "house_2026_roll309.xml").read_text()
    suspension = text.replace("<vote-type>YEA-AND-NAY</vote-type>", "<vote-type>2/3 YEA-AND-NAY</vote-type>")
    db_session.add(RollCall(chamber="house", congress=119, session=2, number=309, date="2026-09-16",
                            source_url="https://clerk.house.gov/evs/2026/roll309.xml", majority_requirement=""))
    db_session.add(RollCall(chamber="senate", congress=119, session=2, number=1, date="2026-09-16",
                            source_url="https://www.senate.gov/x.xml", majority_requirement=""))
    db_session.commit()
    monkeypatch.setattr(ca, "_get", _fake_get({"roll309.xml": suspension.encode()}))
    assert _run(ca.repair_house_requirements(None, db_session)) == (1, "ok")
    assert db_session.query(RollCall).filter_by(chamber="house").one().majority_requirement == "2/3"
    # Nothing left to repair: no request is made.
    monkeypatch.setattr(ca, "_get", _fake_get({}))
    assert _run(ca.repair_house_requirements(None, db_session)) == (0, "ok")


def test_a_run_reads_the_newest_first(monkeypatch):
    # Floor logs, then recent Digests, then the current session's votes
    # before the previous session's: a fresh database shows this week
    # within one run (the first production run spent twenty minutes on
    # January 2025 first).
    from contextlib import asynccontextmanager
    from unittest.mock import MagicMock

    calls = []

    async def floor(client, db, day):
        calls.append(("floor", day.isoformat()))
        return {"house": "ok", "senate": "ok"}

    async def digests(client, db, today):
        calls.append(("digests",))
        return {}

    async def votes(client, db, chamber, congress, session, limit=250):
        calls.append(("votes", chamber, session))
        return 0, "ok"

    @asynccontextmanager
    async def client():
        yield None

    monkeypatch.setattr(ca, "sync_floor_logs", floor)
    monkeypatch.setattr(ca, "sync_digests", digests)
    monkeypatch.setattr(ca, "sync_roll_calls", votes)
    monkeypatch.setattr(ca, "make_async_client", client)
    monkeypatch.setattr(ca, "SessionLocal", MagicMock)
    monkeypatch.setattr(ca, "api_cache_set", lambda *a, **k: None)
    monkeypatch.setattr(ca, "eastern_today", lambda: date(2026, 9, 27))
    monkeypatch.setattr(ca, "expected_current_congress", lambda: 119)
    result = asyncio.run(ca.run_congress_sync())
    assert calls == [
        ("floor", "2026-09-26"), ("floor", "2026-09-27"), ("digests",),
        ("votes", "senate", 2), ("votes", "house", 2), ("votes", "senate", 1), ("votes", "house", 1),
    ]
    assert set(result["rollCalls"]) == {"senate-2", "house-2", "senate-1", "house-1"}
    assert not ca.is_congress_sync_running()


def test_a_senate_file_for_a_day_off_is_not_a_session(db_session, monkeypatch):
    # 2026-09-26 (a Saturday): senate.gov publishes a file holding only
    # "scheduled to reconvene" — it read as "The Senate met" in production.
    s = fl.parse_senate_floor((FIX / "floor_logs" / "09_26_2026_Senate_Floor.xml").read_bytes())
    assert s["in_session"] is False and s["events"] == []
    assert s["next_meeting"].startswith("The Senate is scheduled to reconvene at 3 p.m. Monday, September 28")
    monkeypatch.setattr(ca, "_get", _fake_get({
        "clerk.house.gov": ca._ABSENT,
        "senate.gov": (FIX / "floor_logs" / "09_26_2026_Senate_Floor.xml").read_bytes(),
    }))
    asyncio.run(ca.sync_floor_logs(None, db_session, date(2026, 9, 26)))
    row = db_session.query(CongressDay).filter_by(chamber="senate").one()
    assert row.in_session is False


def test_a_pending_day_of_the_last_week_is_read_again(db_session):
    db_session.add(CongressDay(chamber="senate", date="2026-09-25", in_session=True, is_final=False, source="floor_log"))
    db_session.add(CongressDay(chamber="senate", date="2026-09-24", in_session=True, is_final=True, source="digest"))
    db_session.add(CongressDay(chamber="senate", date="2026-08-01", in_session=True, is_final=False, source="floor_log"))
    db_session.commit()
    days = ca._floor_log_days(db_session, date(2026, 9, 27))
    assert [d.isoformat() for d in days] == ["2026-09-25", "2026-09-26", "2026-09-27"]


def test_a_pro_forma_day_has_its_times_and_says_so():
    from app.pipeline.fetch.daily_digest import parse_chamber_action
    from app.services.congress_service import _chamber_sentence, _minutes

    text = ("Chamber Action\nThe Senate met at 10:30:06 a.m. in pro forma session, and adjourned at "
            "10:33:29 a.m., until 11 a.m., on Monday, October 5, 2026.")
    parsed = parse_chamber_action(text)
    assert (parsed["convened_at"], parsed["adjourned_at"]) == ("10:30:06 a.m.", "10:33:29 a.m.")
    day = {"status": "final", "counts": {"billsPassed": 0, "resolutionsPassed": 0, "confirmed": 0, "recordVotes": 0},
           "minutesInSession": _minutes(parsed["convened_at"], parsed["adjourned_at"]),
           "adjournmentText": parsed["adjournment_text"]}
    assert _chamber_sentence("senate", day) == "The Senate met in pro forma session for 3 minutes."


def test_the_house_xml_gives_no_first_name_and_none_is_invented():
    from app.pipeline.fetch.congress import parse_house_vote_xml

    vote = (FIX / "roll_calls" / "house_2026_roll309.xml").read_text()
    members = parse_house_vote_xml(vote, 2026, 309)["members"]
    assert members and all(m["firstName"] == "" and m["lastName"] for m in members)
