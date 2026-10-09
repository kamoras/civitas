"""Explore documents kept as their sources have them (2026-10-09 audit):
Federal Register metadata re-read after first ingest, provisional document
numbers retired, memoranda requested by the API's own value, the Court's
own decision date, one copy of a speech from overlapping Record granules,
and the presidency a document belongs to by its date."""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import httpx

from app.models import ExploreDocument, President
from app.pipeline import explore_pipeline
from app.pipeline.explore_pipeline import (
    _drop_overlapping_granule_copies,
    _president_id_for,
    _refresh_federal_register,
)
from app.pipeline.fetch import presidential_actions
from app.pipeline.fetch.fr_rulemaking import fetch_fr_metadata
from app.pipeline.fetch.supreme_court import parse_slip_opinions

# Trimmed from the Federal Register API's answer for three stored numbers on
# 2026-10-09: a placeholder title since replaced, a comment period since
# moved, and a provisional number it no longer has.
FR_ANSWER = {
    "count": 2,
    "results": [
        {"document_number": "2026-17842", "title": "Further Ensuring Affordable Beef for the American Consumer",
         "publication_date": "2026-08-31", "signing_date": "2026-08-27", "comments_close_on": None,
         "comment_url": None, "executive_order_number": None},
        {"document_number": "2026-15646", "title": "Certain Pipe From China; Institution of Five-Year Reviews",
         "publication_date": "2026-08-03", "signing_date": None, "comments_close_on": "2026-09-02",
         "comment_url": None, "executive_order_number": None},
    ],
    "errors": {"not_found": ["X26-10831"]},
}


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _fr_doc(ext_id, title, date, chamber, doc_type, close=None):
    return ExploreDocument(
        doc_type=doc_type, source="Federal Register", title=title, summary="", body="b",
        date=date, chamber=chamber, external_id=ext_id, comments_close_on=close,
    )


class TestFetchFrMetadata:
    def test_found_and_not_found_from_one_batch(self):
        client = _client(lambda req: httpx.Response(200, json=FR_ANSWER))
        found, missing = asyncio.run(fetch_fr_metadata(client, ["2026-17842", "2026-15646", "X26-10831"]))
        assert set(found) == {"2026-17842", "2026-15646"}
        assert missing == {"X26-10831"}

    def test_one_number_answers_with_the_document_or_a_404(self):
        doc = FR_ANSWER["results"][0]
        found, missing = asyncio.run(fetch_fr_metadata(_client(lambda req: httpx.Response(200, json=doc)), ["2026-17842"]))
        assert list(found) == ["2026-17842"] and not missing
        found, missing = asyncio.run(fetch_fr_metadata(
            _client(lambda req: httpx.Response(404, json={"status": 404})), ["X26-10831"]))
        assert not found and missing == {"X26-10831"}

    def test_an_outage_is_not_not_found(self):
        assert asyncio.run(fetch_fr_metadata(_client(lambda req: httpx.Response(503, text="down")), ["a", "b"])) is None


class TestRefreshFederalRegister:
    def _rows(self, db_session):
        db_session.add_all([
            _fr_doc("fr-2026-17842", "[No title available]", "2026-08-27", "Executive", "Proclamation"),
            _fr_doc("fr-X26-10831", "Further Ensuring Affordable Beef", "2026-08-27", "Executive", "Proclamation"),
            _fr_doc("fr-reg-2026-15646", "Certain Pipe From China; Institution of Five-Year Reviews",
                    "2026-08-03", "Regulatory", "Notice", close="2026-10-16"),
        ])
        db_session.commit()

    def _run(self, db_session, handler):
        with patch.object(explore_pipeline, "utcnow", return_value=datetime(2026, 10, 9, 15)), \
                patch("app.pipeline.fetch.fr_rulemaking.asyncio.sleep"):
            return asyncio.run(_refresh_federal_register(db_session, _client(handler)))

    def test_the_register_now_wins(self, db_session):
        self._rows(db_session)
        assert self._run(db_session, lambda req: httpx.Response(200, json=FR_ANSWER)) == {"updated": 2, "removed": 1}
        by_ext = {d.external_id: d for d in db_session.query(ExploreDocument)}
        assert by_ext["fr-2026-17842"].title == "Further Ensuring Affordable Beef for the American Consumer"
        # Closed on 2026-09-02, not still open until 2026-10-16.
        assert by_ext["fr-reg-2026-15646"].comments_close_on == "2026-09-02"
        assert "fr-X26-10831" not in by_ext

    def test_nothing_changes_when_the_register_cannot_be_asked(self, db_session):
        self._rows(db_session)
        assert self._run(db_session, lambda req: httpx.Response(503)) == {"updated": 0, "removed": 0}
        assert db_session.query(ExploreDocument).count() == 3

    def test_old_closed_documents_are_not_asked_for(self, db_session):
        db_session.add(_fr_doc("fr-reg-2025-00001", "Old notice", "2025-01-02", "Regulatory", "Notice",
                               close="2025-02-01"))
        db_session.commit()
        asked = []
        self._run(db_session, lambda req: asked.append(req) or httpx.Response(200, json={"results": []}))
        assert asked == []


def test_memoranda_are_requested_by_the_apis_own_value():
    # The API answers "presidential_memorandum" with a 400 and lists 807
    # documents under "memorandum".
    assert "memorandum" in presidential_actions.DOC_TYPES
    assert presidential_actions.DOC_TYPE_LABELS["memorandum"] == "Presidential Memorandum"


def test_the_courts_own_date_is_read_from_its_table():
    slips = parse_slip_opinions((Path(__file__).parent / "fixtures" / "scotus_slipopinion_25.html").read_text())
    # Oyez dated this decision 2026-07-29; the Court's table says 6/29/26.
    assert slips["25-332"]["date"] == "2026-06-29"
    assert slips["26A388"]["date"] == "2026-09-25"


def test_a_speech_from_overlapping_granules_is_kept_once(db_session):
    # A section heading's granule and the statement's own granule held the
    # same speaker and text on one day: the same hash ends both ids.
    db_session.add_all([
        ExploreDocument(id=1, doc_type="Senate Floor Speech", source="s", title="Remarks on STATEMENTS ON INTRODUCED BILLS",
                        summary="", body="Same text.", date="2026-09-30", chamber="Senate",
                        external_id="crec-v1-CREC-2026-09-30-pt1-PgS5264-4b121071"),
        ExploreDocument(id=2, doc_type="Senate Floor Speech", source="s", title="Introductory Statement on S. 1",
                        summary="", body="Same text.", date="2026-09-30", chamber="Senate",
                        external_id="crec-v1-CREC-2026-09-30-pt1-PgS5265-4b121071"),
        ExploreDocument(id=3, doc_type="Senate Floor Speech", source="s", title="Another speech",
                        summary="", body="Other text.", date="2026-09-30", chamber="Senate",
                        external_id="crec-v1-CREC-2026-09-30-pt1-PgS5265-0badf00d"),
        # The same speech on another day is another speech.
        ExploreDocument(id=4, doc_type="Senate Floor Speech", source="s", title="Introductory Statement on S. 1",
                        summary="", body="Same text.", date="2026-10-01", chamber="Senate",
                        external_id="crec-v1-CREC-2026-10-01-pt1-PgS9-4b121071"),
    ])
    db_session.commit()
    assert _drop_overlapping_granule_copies(db_session) == 1
    assert sorted(d.id for d in db_session.query(ExploreDocument)) == [2, 3, 4]


def test_a_document_belongs_to_the_presidency_its_date_falls_in(db_session):
    db_session.add_all([
        President(id="pres-a-1", name="Pat Q. Doe", party="R", number=1, term_start="2017-01-20", term_end="2021-01-20"),
        President(id="pres-b-2", name="Lee Roe Jr.", party="D", number=2, term_start="2021-01-20", term_end="2025-01-20"),
        President(id="pres-a-3", name="Pat Q. Doe", party="R", number=3, term_start="2025-01-20", term_end=None),
    ])
    db_session.commit()
    assert _president_id_for(db_session, "Pat Doe", "2018-05-01") == "pres-a-1"
    assert _president_id_for(db_session, "Pat Doe", "2026-05-01") == "pres-a-3"
    assert _president_id_for(db_session, "Lee Roe Jr.", "2022-05-01") == "pres-b-2"
    assert _president_id_for(db_session, "Lee Roe", "2026-05-01") is None


def test_open_comment_periods_carry_no_empty_policy_areas(db_session):
    from fastapi import Response

    from app.api.action import get_open_comments
    with patch("app.api.action.comment_period_today", return_value=datetime(2026, 10, 9).date()):
        doc = _fr_doc("fr-reg-2026-1", "Open", "2026-10-01", "Regulatory", "Proposed Rule", close="2026-11-01")
        doc.comment_url = "https://www.regulations.gov/x"
        db_session.add(doc)
        db_session.commit()
        items = get_open_comments(Response(), db=db_session)
    assert items and "policyAreas" not in items[0]
    json.dumps(items)
