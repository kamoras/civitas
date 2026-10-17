"""Explore's permanent addresses (services/explore_keys.py): a key from the
document's official identifier, found whichever row holds the document,
and served by the detail endpoint beside the row id it replaces."""

import asyncio
import json

import pytest
from fastapi import HTTPException

from app.api import sitemap
from app.api.explore import get_explore_document
from app.models import ExploreDocument, Representative
from app.services.explore_coverage import coverage_windows
from app.services.explore_keys import find_by_key, public_key

SPEECH = "crec-v1-CREC-2026-09-16-pt1-PgH5836-3-213d7a94"


def _doc(db, external_id, **kw):
    fields = {"doc_type": "Proposed Rule", "source": "Federal Register", "title": "A rule", "summary": "",
              "body": "Text.", "date": "2026-08-21", "chamber": "Regulatory", "external_id": external_id, **kw}
    doc = ExploreDocument(**fields)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def _speech(db, external_id, **kw):
    return _doc(db, external_id, doc_type="House Floor Speech", source="Congressional Record (GovInfo)",
                chamber="House", date="2026-09-16", politician_name="Hale", **kw)


@pytest.mark.parametrize("external_id, key", [
    ("fr-reg-2026-17123", "fr-2026-17123"),       # rulemaking feed
    ("fr-2026-07907", "fr-2026-07907"),           # presidential feed: the same namespace
    (SPEECH, "crec-CREC-2026-09-16-pt1-PgH5836-3-213d7a94"),  # the parser's version dropped
    ("scotus-2025-24-440", "scotus-2025-24-440"),
    (None, "42"),
])
def test_the_key_is_the_official_identifier(external_id, key):
    assert public_key(external_id, 42) == key


def test_a_key_finds_its_document_whichever_feed_or_format_stored_it(db_session):
    rule = _doc(db_session, "fr-reg-2026-17123")
    speech = _speech(db_session, SPEECH)
    assert find_by_key(db_session, "fr-2026-17123").id == rule.id
    assert find_by_key(db_session, "crec-CREC-2026-09-16-pt1-PgH5836-3-213d7a94").id == speech.id
    # A later speech format's row answers the same key.
    db_session.delete(speech)
    db_session.commit()
    reread = _speech(db_session, SPEECH.replace("crec-v1-", "crec-v2-"))
    assert find_by_key(db_session, "crec-CREC-2026-09-16-pt1-PgH5836-3-213d7a94").id == reread.id
    assert find_by_key(db_session, "fr-2026-99999") is None
    assert find_by_key(db_session, "8268") is None  # a row id is not a key


def test_a_dropped_granule_copy_answers_under_the_copy_kept(db_session):
    # explore_pipeline._drop_overlapping_granule_copies keeps one copy of a
    # speech two granules held: same day, same hash.
    kept = _speech(db_session, "crec-v1-CREC-2026-09-16-pt1-PgH5837-1-213d7a94")
    found = find_by_key(db_session, "crec-CREC-2026-09-16-pt1-PgH5836-3-213d7a94")
    assert found.id == kept.id


def _get(db, ref):
    return json.loads(asyncio.run(get_explore_document(ref, db)).body)


def test_the_detail_answers_by_key_and_by_row_id_and_names_the_key(db_session):
    rule = _doc(db_session, "fr-reg-2026-17123")
    by_key, by_id = _get(db_session, "fr-2026-17123"), _get(db_session, str(rule.id))
    assert by_key["id"] == by_id["id"] == rule.id
    assert by_key["key"] == by_id["key"] == "fr-2026-17123"
    for missing in ("fr-2026-99999", "../etc", "a b"):
        with pytest.raises(HTTPException) as e:
            _get(db_session, missing)
        assert e.value.status_code == 404


def test_a_speech_names_its_record_citation_and_speaker(db_session):
    db_session.add(Representative(id="jordan-hale", name="Jordan Hale", state="MI", district=5, party="D"))
    db_session.commit()
    speech = _speech(db_session, SPEECH, politician_id="jordan-hale")
    body = _get(db_session, str(speech.id))
    assert (body["crCitation"], body["speakerName"]) == ("172 Cong. Rec. H5836", "Jordan Hale")
    assert _get(db_session, str(_doc(db_session, "fr-reg-2026-1").id))["crCitation"] == ""


def test_the_sitemap_lists_keys(db_session):
    _doc(db_session, "fr-reg-2026-17123")
    _speech(db_session, SPEECH)
    docs = json.loads(sitemap.sitemap_explore(page=0, db=db_session).body)["documents"]
    assert [d["id"] for d in docs] == ["fr-2026-17123", "crec-CREC-2026-09-16-pt1-PgH5836-3-213d7a94"]


def test_coverage_states_each_types_window(db_session):
    _doc(db_session, "fr-reg-2026-1", date="2026-04-09")
    _doc(db_session, "fr-reg-2026-2", doc_type="Notice", date="2026-10-16")
    _speech(db_session, SPEECH)
    assert coverage_windows(db_session) == [
        {"label": "Rules and notices", "from": "2026-04-09", "to": "2026-10-16", "count": 2},
        {"label": "Floor speeches", "from": "2026-09-16", "to": "2026-09-16", "count": 1},
    ]
