"""Nominations: the Congress.gov sync (fetch/nominations.py), its cursor and
write discipline, the FJC join, and the read side (services/nomination_service.py,
api/nominations.py) with its link from each nomination roll call."""

import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError

from app import database
from app.database import get_db
from app.main import app
from app.models import LearnedClassification, Nomination, RollCall, RollCallPosition
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch import nominations as nm
from app.services import nomination_service as ns
from app.services.nomination_links import citations_in

CONGRESS = 119


def _item(citation, updated, *, org="Department of Labor", military=False, latest="Confirmed by the Senate by Voice Vote."):
    return {
        "citation": citation, "congress": CONGRESS, "number": int(citation[2:].split("-")[0]),
        "partNumber": citation.split("-")[1] if "-" in citation else "00", "organization": org,
        "description": f"Someone, of Ohio, to be something at {org}", "receivedDate": "2026-01-05",
        "updateDate": updated, "nominationType": {"isMilitary": True} if military else {"isCivilian": True},
        "latestAction": {"actionDate": "2026-03-02", "text": latest},
    }


def _api(listing, *, broken=(), confirmed="2026-03-02", position="Secretary of Labor", last="Doe"):
    """A fake Congress.gov: answers by path, None for `broken` citations or a
    None listing. Records every path asked."""
    asked = []

    async def get(client, path):
        asked.append(path)
        if path.startswith(f"/nomination/{CONGRESS}?"):
            return None if listing is None else {"nominations": listing}
        rest = path.split(f"/nomination/{CONGRESS}/", 1)[1].split("?")[0]
        number, _, sub = rest.partition("/")
        citation = f"PN{number}"
        if citation in broken:
            return None
        if sub == "":
            return {"nomination": {"citation": citation, "number": int(number.split("-")[0]),
                                   "partNumber": "00", "receivedDate": "2026-01-05",
                                   "description": "Jane Doe, of Ohio, to be Secretary of Labor",
                                   "nominees": [{"ordinal": 1, "positionTitle": position,
                                                 "organization": "Department of Labor", "nomineeCount": 1}]}}
        if sub == "actions":
            return {"actions": [
                {"actionCode": "S05310", "actionDate": confirmed, "type": "Floor",
                 "text": "Confirmed by the Senate by Yea-Nay Vote. 50 - 47. Record Vote Number: 241."},
                {"actionCode": "S05750", "actionDate": "2026-02-01", "type": "Committee",
                 "text": "Committee on Health. Hearings held.",
                 "committees": [{"systemCode": "sshr00", "name": "HELP Committee"}]},
                {"actionCode": "S05120", "actionDate": "2026-01-05", "type": "IntroReferral",
                 "text": "Received in the Senate and referred to the Committee on Health.",
                 "committees": [{"systemCode": "sshr00", "name": "HELP Committee"}]},
            ]}
        if sub == "hearings":
            return {"hearings": [{"citation": "S.Hrg.119-442", "jacketNumber": 63995, "date": "2026-02-01"}]}
        if sub == "1":
            return {"nominees": [{"firstName": "Jane", "lastName": last, "state": "OH", "ordinal": 1}]}
        raise AssertionError(path)

    get.asked = asked
    return get


@pytest.fixture
def no_fjc(monkeypatch):
    async def none(client, db):
        return None
    monkeypatch.setattr(nm, "fetch_fjc_appointments", none)


def _run(db, **kw):
    import asyncio
    return asyncio.run(nm.sync_nominations(db, CONGRESS, **kw))


def _cursor(db):
    return (api_cache_get(db, "nominations", f"cursor-{CONGRESS}") or {}).get("cursor")


# --- Vocabulary ------------------------------------------------------------

def test_status_reads_the_latest_ending_action_code():
    assert nm.status_of([{"code": "S05120", "date": "2026-01-01"}]) == ("pending", None)
    assert nm.status_of([
        {"code": "S05310", "date": "2026-03-01"}, {"code": "S05360", "date": "2026-03-02"},
    ]) == ("confirmed", "2026-03-01")
    assert nm.status_of([
        {"code": "S05330", "date": "2026-05-01"}, {"code": "S05120", "date": "2026-01-01"},
    ]) == ("withdrawn", "2026-05-01")
    assert nm.status_of([{"code": "S05335", "date": "2026-01-03"}])[0] == "returned"


def test_citations_are_read_as_tokens():
    assert citations_in("On the Nomination PN999-1") == ["PN999-1"]
    assert citations_in("On the Nomination PN961-1 and PN1012 and PN730-38") == ["PN961-1", "PN1012", "PN730-38"]
    # PN999-1 is not in PN999-10, nor PN99 in PN999.
    assert "PN999-1" not in citations_in("On the Cloture Motion PN999-10")
    assert citations_in("On Passage of the Bill S. 5") == []


def test_fjc_court_translates_only_titles_the_directory_covers():
    assert nm.fjc_court("United States District Judge for the Southern District of Texas") == \
        "U.S. District Court for the Southern District of Texas"
    assert nm.fjc_court("United States Circuit Judge for the Fifth Circuit") == "U.S. Court of Appeals for the Fifth Circuit"
    assert nm.fjc_court("Judge of the United States Court of Federal Claims for a term of fifteen years") is None


def test_fjc_match_needs_court_date_and_surname_exactly_once():
    row = Nomination(congress=CONGRESS, citation="PN999-1", number=999, received_date="2026-05-12",
                     position_title="United States District Judge for the Southern District of Texas",
                     nominees=json.dumps([{"names": [{"firstName": "Angela", "lastName": "Colmenero"}]}]))
    court = "U.S. District Court for the Southern District of Texas"
    hit = {"court": court, "nominated": "2026-05-12", "surname": "colmenero", "jid": "1", "nid": "2"}
    assert nm.fjc_match(row, [hit]) == hit
    assert nm.fjc_match(row, [{**hit, "nominated": "2026-05-13"}]) is None
    assert nm.fjc_match(row, [{**hit, "surname": "colmeneros"}]) is None
    assert nm.fjc_match(row, [hit, {**hit, "jid": "3"}]) is None  # two: neither
    assert nm.surname_key("Luján") == nm.surname_key("LUJAN")
    assert nm.surname_key("St. John") == nm.surname_key("St John")


def test_a_joint_district_seat_matches_its_two_fjc_appointments():
    row = Nomination(congress=CONGRESS, citation="PN1", number=1, received_date="2025-05-12",
                     position_title="United States District Judge for the Eastern and Western Districts of Missouri",
                     nominees=json.dumps([{"names": [{"lastName": "Divine"}]}]))
    both = [{"court": f"U.S. District Court for the {d} District of Missouri", "nominated": "2025-05-12",
             "surname": "divine", "jid": "7", "nid": "7"} for d in ("Eastern", "Western")]
    assert nm.fjc_match(row, both)["jid"] == "7"
    assert nm.fjc_match(row, both[:1]) is None


# --- The sync --------------------------------------------------------------

def test_a_full_pass_stores_everything_and_moves_the_cursor(db_session, monkeypatch, no_fjc):
    monkeypatch.setattr(nm, "_get_json", _api([_item("PN1129", "2026-03-03T11:00:00Z")]))
    summary = _run(db_session)
    assert summary["stored"] == 1 and summary["complete"]
    row = db_session.query(Nomination).one()
    assert (row.status, row.status_date, row.committee_code) == ("confirmed", "2026-03-02", "sshr00")
    assert row.position_title == "Secretary of Labor" and row.organization == "Department of Labor"
    assert json.loads(row.nominees)[0]["names"] == [{"firstName": "Jane", "middleName": "", "lastName": "Doe", "state": "OH"}]
    assert json.loads(row.hearings) == [{"citation": "S.Hrg.119-442", "jacketNumber": 63995, "date": "2026-02-01"}]
    assert json.loads(row.actions)[0]["code"] == "S05310"  # newest first
    assert _cursor(db_session) is not None


def test_an_unreadable_listing_keeps_the_cursor_and_stores_nothing(db_session, monkeypatch, no_fjc):
    monkeypatch.setattr(nm, "_get_json", _api(None))
    summary = _run(db_session)
    assert summary["unreadable"] == ["listing"] and not summary["complete"]
    assert db_session.query(Nomination).count() == 0 and _cursor(db_session) is None


def test_an_unchanged_nomination_is_not_read_again(db_session, monkeypatch, no_fjc):
    listing = [_item("PN1129", "2026-03-03T11:00:00Z")]
    monkeypatch.setattr(nm, "_get_json", _api(listing))
    _run(db_session)
    again = _api(listing)
    monkeypatch.setattr(nm, "_get_json", again)
    assert _run(db_session)["stored"] == 0
    assert all("?" in p for p in again.asked)  # only the listing


def test_the_cursor_stops_before_the_first_unreadable_nomination(db_session, monkeypatch, no_fjc):
    listing = [_item("PN3", "2026-03-03T00:00:00Z"), _item("PN1", "2026-03-01T00:00:00Z"),
               _item("PN2", "2026-03-02T00:00:00Z")]
    monkeypatch.setattr(nm, "_get_json", _api(listing, broken={"PN2"}))
    summary = _run(db_session)
    # Oldest first: PN1 stored, PN2 failed, PN3 still read.
    assert summary["unreadable"] == ["PN2"] and summary["stored"] == 2 and not summary["complete"]
    assert _cursor(db_session) == datetime(2026, 3, 1).isoformat()


def test_a_limited_pass_resumes_at_the_first_update_not_applied(db_session, monkeypatch, no_fjc):
    listing = [_item(f"PN{i}", f"2026-03-0{i}T00:00:00Z") for i in (1, 2, 3)]
    monkeypatch.setattr(nm, "_get_json", _api(listing))
    summary = _run(db_session, limit=2)
    assert summary["stored"] == 2 and not summary["complete"]
    assert _cursor(db_session) == datetime(2026, 3, 2).isoformat()
    marker = nm.read_marker(db_session, CONGRESS)
    assert (marker["backfillComplete"], marker["expected"]) == (False, 3)
    assert _run(db_session)["stored"] == 1  # PN3; PN1-2 skipped as stored
    marker = nm.read_marker(db_session, CONGRESS)
    assert (marker["backfillComplete"], marker["expected"]) == (True, 3)


def test_a_failed_pass_keeps_the_backfill_state(db_session, monkeypatch, no_fjc):
    monkeypatch.setattr(nm, "_get_json", _api([_item("PN1", "2026-03-01T00:00:00Z")]))
    _run(db_session)
    monkeypatch.setattr(nm, "_get_json", _api(None))
    _run(db_session)
    assert nm.read_marker(db_session, CONGRESS)["backfillComplete"] is True


def test_three_unreadable_in_a_row_stop_the_pass(db_session, monkeypatch, no_fjc):
    listing = [_item(f"PN{i}", f"2026-03-0{i}T00:00:00Z") for i in range(1, 6)]
    api = _api(listing, broken={"PN1", "PN2", "PN3", "PN4", "PN5"})
    monkeypatch.setattr(nm, "_get_json", api)
    assert _run(db_session)["unreadable"] == ["PN1", "PN2", "PN3"]


def test_no_write_lock_is_held_across_a_request(file_sessionmaker, monkeypatch, no_fjc):
    """At every Congress.gov request, another writer gets the lock (#990)."""
    results = []
    inner = _api([_item("PN1", "2026-03-01T00:00:00Z"), _item("PN2", "2026-03-02T00:00:00Z")])

    async def get(client, path):
        other = file_sessionmaker()
        try:
            other.execute(text("PRAGMA busy_timeout = 200"))
            other.execute(sqlite_insert(LearnedClassification).values(
                entity_name=f"X{len(results)}", entity_type="donor_type", value="INDIVIDUAL",
                confidence=1.0, source="fec").on_conflict_do_nothing())
            other.commit()
            results.append(True)
        except OperationalError as error:
            other.rollback()
            assert database.is_locked(error)
            results.append(False)
        finally:
            other.close()
        return await inner(client, path)

    monkeypatch.setattr(nm, "_get_json", get)
    db = file_sessionmaker()
    try:
        assert _run(db)["stored"] == 2
    finally:
        db.close()
    assert results and all(results)


def test_confirmed_judgeships_are_linked_to_the_fjc(db_session, monkeypatch):
    item = _item("PN999-1", "2026-03-03T00:00:00Z", org=nm.JUDICIARY)
    monkeypatch.setattr(nm, "_get_json", _api(
        [item], position="United States District Judge for the Southern District of Texas", last="Colmenero"))

    async def fjc(client, db):
        return [{"court": "U.S. District Court for the Southern District of Texas", "nominated": "2026-01-05",
                 "surname": "colmenero", "jid": "13762219", "nid": "13762219"}]
    monkeypatch.setattr(nm, "fetch_fjc_appointments", fjc)
    assert _run(db_session)["fjcLinked"] == 1
    assert db_session.query(Nomination).one().fjc_judge_id == "13762219"


# --- The read side ---------------------------------------------------------

def _nom(db, citation, *, status="confirmed", received="2026-01-01", ended="2026-03-02", org="Department of Labor",
         military=False, title="Secretary of Labor"):
    row = Nomination(
        congress=CONGRESS, citation=citation, number=int(citation[2:].split("-")[0]),
        description=f"Jane Doe, of Ohio, to be {title}", received_date=received, organization=org,
        position_title=title, is_military=military, nominee_count=1, status=status,
        status_date=ended if status != "pending" else None, latest_action_date=ended or received,
        latest_action_text="Confirmed by the Senate by Yea-Nay Vote.", update_date="2026-03-03T00:00:00Z",
        nominees=json.dumps([{"positionTitle": title, "names": [{"firstName": "Jane", "lastName": "Doe", "state": "OH"}]}]),
    )
    db.add(row)
    return row


def _roll_call(db, number, question, positions=()):
    rc = RollCall(chamber="senate", congress=CONGRESS, session=2, number=number, date="2026-03-02",
                  question=question, title="Jane Doe, of Ohio, to be Secretary of Labor",
                  result="Nomination Confirmed", yeas=50, nays=47)
    db.add(rc)
    db.flush()
    for last, state, party, position in positions:
        db.add(RollCallPosition(roll_call_id=rc.id, member_id=f"S{len(last)}{state}", last_name=last,
                                state=state, party=party, position=position))
    return rc


@pytest.fixture
def client(db_session):
    _nom(db_session, "PN1129")
    _nom(db_session, "PN12", status="pending", ended=None)
    _nom(db_session, "PN13", status="withdrawn")
    _nom(db_session, "PN999-1", org=nm.JUDICIARY, received="2026-01-01", ended="2026-04-01",
         title="United States District Judge for the Southern District of Texas")
    _nom(db_session, "PN636", military=True, org="Army", title="Major")
    _roll_call(db_session, 255, "On the Cloture Motion PN1129")
    _roll_call(db_session, 256, "On the Nomination PN1129", [("Doe", "OH", "R", "Yea"), ("Roe", "OH", "D", "Nay")])
    _roll_call(db_session, 257, "On the Nomination PN11290")  # not PN1129
    db_session.commit()
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_the_list_filters_and_counts(client):
    body = client.get("/api/nominations?kind=civilian").json()
    assert body["counts"] == {"pending": 1, "confirmed": 1, "returned": 0, "withdrawn": 1}
    assert body["allStatuses"] == 3
    assert {i["citation"] for i in body["items"]} == {"PN1129", "PN12", "PN13"}
    assert [i["citation"] for i in client.get("/api/nominations?status=pending").json()["items"]] == ["PN12"]
    assert client.get("/api/nominations?agency=Army").json()["total"] == 1
    assert client.get("/api/nominations?q=district+texas").json()["items"][0]["citation"] == "PN999-1"
    assert client.get("/api/nominations?status=bogus").status_code == 422


def test_stats_count_days_to_confirm_by_kind(client):
    body = client.get("/api/nominations/stats").json()
    civilian = body["kinds"]["civilian"]["timeToConfirm"]
    assert civilian["n"] == 1 and civilian["median"] == 60  # Jan 1 -> Mar 2
    assert civilian["bins"][-1] == {"start": 60, "end": 90, "count": 1}
    assert body["kinds"]["judicial"]["timeToConfirm"]["median"] == 90
    assert body["counts"]["confirmed"] == 3 and body["total"] == 5 and body["people"] == 5


def test_a_nomination_page_has_its_roll_calls_and_each_senators_vote(client):
    body = client.get("/api/nominations/119/PN1129").json()
    assert [rc["number"] for rc in body["rollCalls"]] == [255, 256]  # not 257's PN11290
    assert body["finalVote"]["number"] == 256
    assert {(m["lastName"], m["position"]) for m in body["finalVote"]["members"]} == {("Doe", "Yea"), ("Roe", "Nay")}
    assert body["congressGovUrl"] == "https://www.congress.gov/nomination/119th-congress/1129"
    assert client.get("/api/nominations/119/PN4242").status_code == 404
    assert client.get("/api/nominations/119/nonsense").status_code == 422


def test_a_nomination_roll_call_links_its_nomination_labelled_by_its_question(client):
    vote = client.get("/api/congress/votes/senate/119/2/256").json()["nominationVote"]
    assert (vote["kind"], vote["label"], vote["citationCount"]) == ("confirm", "Vote to confirm", 1)
    assert [n["citation"] for n in vote["nominations"]] == ["PN1129"]
    cloture = client.get("/api/congress/votes/senate/119/2/255").json()["nominationVote"]
    assert (cloture["kind"], cloture["label"]) == ("cloture", "Vote to end debate (cloture)")
    # Names PN11290, which isn't stored: the vote is still one on a nomination.
    other = client.get("/api/congress/votes/senate/119/2/257").json()["nominationVote"]
    assert other["citationCount"] == 1 and other["nominations"] == []


def test_a_partly_stored_en_bloc_vote_counts_every_nomination_it_names(db_session, client):
    _roll_call(db_session, 300, "On the Nomination PN1129 and PN777 and PN778-2")
    db_session.commit()
    vote = client.get("/api/congress/votes/senate/119/2/300").json()["nominationVote"]
    assert vote["citationCount"] == 3 and [n["citation"] for n in vote["nominations"]] == ["PN1129"]


def test_stats_say_whether_the_backfill_has_read_everything(db_session, client):
    assert client.get("/api/nominations/stats").json()["backfill"] == {
        "complete": False, "read": 5, "expected": None, "nightsLeft": None}  # before any run
    api_cache_set(db_session, "nominations", f"cursor-{CONGRESS}",
                  {"cursor": None, "backfillComplete": False, "expected": 905})
    backfill = client.get("/api/nominations/stats").json()["backfill"]
    assert backfill == {"complete": False, "read": 5, "expected": 905, "nightsLeft": 3}
    api_cache_set(db_session, "nominations", f"cursor-{CONGRESS}",
                  {"cursor": None, "backfillComplete": True, "expected": 905})
    assert client.get("/api/nominations/stats").json()["backfill"]["complete"] is True


def test_the_sitemap_lists_every_nomination(client):
    cited = {(n["congress"], n["citation"]) for n in client.get("/api/sitemap").json()["nominations"]}
    assert (CONGRESS, "PN999-1") in cited and len(cited) == 5


def test_congress_gov_url_for_a_part():
    assert ns.congress_gov_url(Nomination(congress=119, number=730, part_number="47")) == \
        "https://www.congress.gov/nomination/119th-congress/730/47"
