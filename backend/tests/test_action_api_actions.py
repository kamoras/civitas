"""The issues API's committee and comment actions, and the Today tab's
comment periods ranked by salience (app/api/action.py)."""

import json
from datetime import datetime
from unittest.mock import patch

from fastapi import Response

from app.api.action import _build_issue_response, get_open_comments
from app.comment_periods import PAPERWORK_TOC_SUBJECT
from app.models import ActionIssue, ExploreDocument, Representative, RollCall, Senator


def _issue(db, actions, explore_ids=()):
    issue = ActionIssue(
        date="2026-10-10", rank=1, title="Issue", summary="s", is_current=True,
        actions=json.dumps(actions), related_explore_ids=json.dumps(list(explore_ids)),
    )
    db.add(issue)
    db.commit()
    return issue


def test_a_comment_action_is_served_only_while_its_period_is_open(db_session):
    issue = _issue(db_session, [
        {"text": "Comment by October 20", "type": "comment_period", "url": "/explore/7#comment",
         "docId": 7, "closesOn": "2026-10-20"},
        {"text": "Comment by October 1", "type": "comment_period", "url": "/explore/8#comment",
         "docId": 8, "closesOn": "2026-10-01"},
    ])
    with patch("app.api.action.comment_period_today", return_value="2026-10-11"):
        resp = _build_issue_response(issue, db_session)
    assert [(a["docId"], a["closesOn"]) for a in resp["actions"]] == [(7, "2026-10-20")]


def test_a_committee_action_names_the_current_chair_and_ranking_member(db_session):
    name = "Senate Committee on Finance"

    def seat(title):
        return json.dumps([{"committeeName": name, "chamber": "senate", "title": title}])

    db_session.add_all([
        Senator(id="s-chair", name="Pat Doe", state="ID", party="R", is_current=True, committees=seat("Chairman")),
        Senator(id="s-rank", name="Lee Roe", state="OR", party="D", is_current=True, committees=seat("Ranking Member")),
        Senator(id="s-gone", name="Kim Poe", state="UT", party="R", is_current=False, committees=seat("Chair")),
        Senator(id="s-member", name="Ash Moe", state="WA", party="D", is_current=True, committees=seat(None)),
        Representative(id="h-other", name="Sam Foe", state="TX", district="1", party="R", is_current=True,
                       committees=json.dumps([{"committeeName": "House Committee on Ways and Means",
                                               "chamber": "house", "title": "Chairman"}])),
    ])
    issue = _issue(db_session, [{
        "text": f"Contact the {name}", "type": "contact_committee", "url": "https://www.finance.senate.gov/",
        "committee": {"code": "SSFI", "name": name, "chamber": "senate"},
    }])
    committee = _build_issue_response(issue, db_session)["actions"][0]["committee"]
    assert committee["url"] == "https://www.finance.senate.gov/"
    assert committee["chair"]["id"] == "s-chair"
    assert committee["chair"]["chamber"] == "senate"
    assert committee["rankingMember"]["id"] == "s-rank"


def _doc(id_, doc_type, views, close="2026-11-01", toc=None, summary=""):
    return ExploreDocument(
        id=id_, doc_type=doc_type, source="Federal Register", title=f"Doc {id_}", summary=summary, body="b",
        date="2026-10-01", chamber="Regulatory", comment_url="https://www.regulations.gov/x",
        comments_close_on=close, fr_page_views=views, external_id=f"fr-reg-2026-{id_}", fr_toc_subject=toc,
    )


def test_open_comments_lead_with_linked_documents_then_rules_by_readers_then_notices(db_session):
    db_session.add_all([
        _doc(1, "Notice", 50_000, close="2026-10-12"),        # many readers, but a notice
        _doc(2, "Proposed Rule", 900),
        _doc(3, "Final Rule", 4_000),
        _doc(4, "Notice", 10),                                # linked by a recent issue
        _doc(5, "Proposed Rule", None, close="2026-10-12"),   # no count read yet
        _doc(6, "Proposed Rule", 99_999, close="2026-10-01"),  # closed
    ])
    db_session.add(ActionIssue(date="2026-10-09", rank=1, title="i", summary="s",
                               related_explore_ids=json.dumps([4])))
    db_session.commit()
    with patch("app.api.action.comment_period_today", return_value="2026-10-11"):
        items = get_open_comments(Response(), db=db_session)
    assert [i["id"] for i in items] == [4, 3, 2, 5, 1]


def test_a_paperwork_notice_comes_after_every_other_notice(db_session):
    db_session.add_all([
        _doc(1, "Notice", 9_000, toc=PAPERWORK_TOC_SUBJECT, summary="The agency will renew a form. More."),
        _doc(2, "Notice", 10),
    ])
    db_session.commit()
    with patch("app.api.action.comment_period_today", return_value="2026-10-11"):
        items = get_open_comments(Response(), db=db_session)
    assert [(i["id"], i["paperwork"]) for i in items] == [(2, False), (1, True)]
    assert items[1]["firstSentence"] == "The agency will renew a form."
    assert items[1]["commentWindow"]["daysLeft"] == 21


def test_a_roll_call_issue_leads_with_its_vote_page_here(db_session):
    """Issue 801 (2026-10-15) linked only the Clerk's XML: a reader couldn't
    get from the issue to how each member voted."""
    xml = "https://clerk.house.gov/evs/2026/roll314.xml"
    db_session.add(RollCall(chamber="house", congress=119, session=2, number=314, date="2026-10-14",
                            question="On Passage", result="Passed", source_url=xml,
                            fetched_at=datetime(2026, 10, 14)))
    draft = _issue(db_session, [])
    draft.primary_source_url = xml
    news = _issue(db_session, [])
    news.primary_source_url = "https://example.com/article"
    db_session.commit()

    actions = _build_issue_response(draft, db_session)["actions"]
    assert actions[0] == {"text": "How each member voted", "type": "roll_call",
                          "url": "/congress/votes/house/119/2/314",
                          "docId": None, "docKey": None, "closesOn": None, "committee": None}
    assert _build_issue_response(news, db_session)["actions"] == []


def _rule(db, external_id, **kw):
    doc = ExploreDocument(doc_type="Proposed Rule", source="Federal Register", title=kw.pop("title", "A rule"),
                          summary="", body="", date="2026-10-01", chamber="Regulatory",
                          external_id=external_id, **kw)
    db.add(doc)
    db.commit()
    return doc


def test_an_issue_finds_its_documents_by_key_after_their_rows_are_replaced(db_session):
    """An archived issue is permanent and the rows it named are not: its
    documents are found by the keys it stored, and a row id that now names
    another document (a data reset reuses them) is never trusted."""
    old = _rule(db_session, "fr-reg-2026-11111", comments_close_on="2026-10-20",
                comment_url="https://www.regulations.gov/commenton/X-2026-0001-0001")
    issue = _issue(db_session, [{"text": "Comment by October 20", "type": "comment_period",
                                 "url": f"/explore/{old.id}#comment", "docId": old.id,
                                 "docKey": "fr-2026-11111", "closesOn": "2026-10-20"}], [old.id])
    issue.related_explore_keys = json.dumps(["fr-2026-11111"])
    db_session.commit()
    # The row is replaced: the same document under a new row, and an
    # unrelated one in the old row id.
    old_id = old.id
    db_session.delete(old)
    db_session.commit()
    stranger = _rule(db_session, "fr-reg-2026-99999", id=old_id, title="Unrelated")
    new = _rule(db_session, "fr-reg-2026-11111", comments_close_on="2026-10-20",
                comment_url="https://www.regulations.gov/commenton/X-2026-0001-0001")
    with patch("app.api.action.comment_period_today", return_value="2026-10-11"):
        resp = _build_issue_response(issue, db_session)
    assert [(d["id"], d["key"]) for d in resp["relatedExploreDocs"]] == [(new.id, "fr-2026-11111")]
    [action] = resp["actions"]
    assert (action["docId"], action["docKey"], action["url"]) == (
        new.id, "fr-2026-11111", "/explore/fr-2026-11111#comment")
    assert stranger.id == old_id


def test_an_issue_stored_before_keys_still_reads_its_ids(db_session):
    doc = _rule(db_session, "fr-reg-2026-22222")
    issue = _issue(db_session, [], [doc.id])
    assert [d["id"] for d in _build_issue_response(issue, db_session)["relatedExploreDocs"]] == [doc.id]


def test_the_backfill_derives_the_same_keys_the_site_uses():
    import importlib.util
    from pathlib import Path

    from app.services.explore_keys import public_key
    path = Path(__file__).parent.parent / "migrations" / "versions" / "0059_action_issue_explore_keys.py"
    spec = importlib.util.spec_from_file_location("m0059", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for ext in ("fr-reg-2026-1", "fr-2026-2", "crec-v1-CREC-2026-09-16-pt1-PgH5836-3-ab12cd34",
                "scotus-2025-24-440", "senate-floor-DOE-2025-02-20-065f50ec", None):
        assert module._key(ext, 7) == public_key(ext, 7), ext
