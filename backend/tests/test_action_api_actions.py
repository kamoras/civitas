"""The issues API's committee and comment actions, and the Today tab's
comment periods ranked by salience (app/api/action.py)."""

import json
from unittest.mock import patch

from fastapi import Response

from app.api.action import _build_issue_response, get_open_comments
from app.models import ActionIssue, ExploreDocument, Representative, Senator


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


def _doc(id_, doc_type, views, close="2026-11-01"):
    return ExploreDocument(
        id=id_, doc_type=doc_type, source="Federal Register", title=f"Doc {id_}", summary="", body="b",
        date="2026-10-01", chamber="Regulatory", comment_url="https://www.regulations.gov/x",
        comments_close_on=close, fr_page_views=views, external_id=f"fr-reg-2026-{id_}",
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
