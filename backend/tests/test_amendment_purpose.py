"""What an amendment voted on would do, read from the record
(fetch/amendment_purpose.py) and served beside the vote."""

import asyncio

from app.models import RollCall
from app.pipeline.fetch import amendment_purpose
from app.services.bill_record import vote_detail
from app.services.congress_service import amendment_fields

_SENATE_XML = b"""<?xml version="1.0" encoding="UTF-8"?><roll_call_vote>
  <vote_question_text>On the Amendment S.Amdt. 6805 to S.Amdt. 6776 to S. 4668 (No short title on file)</vote_question_text>
  <document><document_type>S.Amdt.</document_type><document_name/></document>
  <amendment>
    <amendment_number>S.Amdt. 6805</amendment_number>
    <amendment_to_document_number>S. 4668</amendment_to_document_number>
    <amendment_purpose>To increase the limitation on funding for post-eligibility
      insurance and catastrophic injury for student athletes.</amendment_purpose>
  </amendment>
  <members><member><last_name>Doe</last_name></member></members>
</roll_call_vote>"""


def _rc(db, chamber, number, question, title="", **kw):
    rc = RollCall(chamber=chamber, congress=119, session=2, number=number, date="2026-09-28",
                  question=question, title=title, source_url=f"https://example.test/{chamber}/{number}.xml", **kw)
    db.add(rc)
    db.commit()
    return rc


def test_the_senate_record_states_the_purpose_verbatim():
    assert amendment_purpose.parse_senate(_SENATE_XML) == (
        "S.Amdt. 6805",
        "To increase the limitation on funding for post-eligibility insurance and catastrophic "
        "injury for student athletes.",
    )


def test_the_senates_no_purpose_placeholder_is_no_purpose():
    xml = _SENATE_XML.replace(
        b"""To increase the limitation on funding for post-eligibility
      insurance and catastrophic injury for student athletes.""", b"No Statement of Purpose on File.")
    assert amendment_purpose.parse_senate(xml) == ("S.Amdt. 6805", "")


def test_a_senate_vote_on_no_amendment_reads_as_none_and_html_as_unread():
    assert amendment_purpose.parse_senate(b"<roll_call_vote><question>On Passage</question></roll_call_vote>") == ("", "")
    # senate.gov answers a missing roll call with an HTML page: not read.
    assert amendment_purpose.parse_senate(b"<html><body>Not available</body></html>") is None


def test_a_house_vote_names_its_amendment_by_congress_gov_number():
    assert amendment_purpose.house_amendment(
        {"houseRollCallVote": {"amendmentType": "HAMDT", "amendmentNumber": "258"}}) == ("HAMDT", "258")
    # A motion to concur in the Senate's amendments names none.
    assert amendment_purpose.house_amendment({"houseRollCallVote": {"legislationType": "HR"}}) is None


def test_only_unread_votes_that_may_be_on_an_amendment_are_read(db_session):
    on = _rc(db_session, "senate", 248, "On the Amendment S.Amdt. 6805 to S. 4668", "Booker Amdt. No. 6805")
    house = _rc(db_session, "house", 273, "On Agreeing to the Amendment")
    _rc(db_session, "senate", 250, "On the Cloture Motion", "A bill to do things")
    _rc(db_session, "senate", 251, "On the Amendment S.Amdt. 1", amendment="S.Amdt. 1", amendment_purpose="x")
    assert {r.id for r in amendment_purpose.candidates(db_session)} == {on.id, house.id}


def test_fill_stores_what_was_read_and_leaves_a_failed_read_unread(db_session, monkeypatch):
    senate = _rc(db_session, "senate", 248, "On the Amendment S.Amdt. 6805", "Booker Amdt. No. 6805")
    house = _rc(db_session, "house", 273, "On Agreeing to the Amendment")
    concur = _rc(db_session, "house", 274, "On Motion to Concur in the Senate Amendments")

    async def senate_read(client, rc):
        return amendment_purpose.parse_senate(_SENATE_XML)

    async def house_read(client, db, rc):
        return {273: None, 274: ("", "")}[rc.number]

    monkeypatch.setattr(amendment_purpose, "_senate", senate_read)
    monkeypatch.setattr(amendment_purpose, "_house", house_read)
    out = asyncio.run(amendment_purpose.fill_amendment_purposes(None, db_session))

    assert out == {"read": 2, "failed": 1, "status": "failed"}
    db_session.refresh(senate), db_session.refresh(house), db_session.refresh(concur)
    assert senate.amendment == "S.Amdt. 6805" and senate.amendment_purpose.startswith("To increase")
    # An outage is not "no purpose stated": the row stays unread.
    assert house.amendment is None and house.amendment_purpose is None
    assert concur.amendment == "" and concur.amendment_purpose == ""


def test_the_source_down_stops_the_run(db_session, monkeypatch):
    for n in range(1, 6):
        _rc(db_session, "house", n, "On Agreeing to the Amendment")

    async def down(client, db, rc):
        return None

    monkeypatch.setattr(amendment_purpose, "_house", down)
    out = asyncio.run(amendment_purpose.fill_amendment_purposes(None, db_session))
    assert out == {"read": 0, "failed": amendment_purpose._FAILURES_TO_STOP, "status": "failed"}


def test_the_vote_carries_its_purpose_only_when_on_an_amendment(db_session):
    rc = _rc(db_session, "senate", 248, "On the Amendment", amendment="S.Amdt. 6805",
             amendment_purpose="To limit price increases for college athletics tickets.")
    detail = vote_detail(db_session, rc)
    assert detail["amendment"] == "S.Amdt. 6805"
    assert detail["amendmentPurpose"] == "To limit price increases for college athletics tickets."
    # A vote read as not on an amendment, or not read yet, carries neither.
    assert amendment_fields(RollCall(amendment="", amendment_purpose="")) == {
        "amendment": None, "amendmentPurpose": None}
    assert amendment_fields(RollCall()) == {"amendment": None, "amendmentPurpose": None}
    # A record that states no purpose says so ("").
    assert amendment_fields(RollCall(amendment="H.Amdt. 9", amendment_purpose=""))["amendmentPurpose"] == ""
