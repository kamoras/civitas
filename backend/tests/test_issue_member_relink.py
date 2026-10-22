"""Stored issues' members are matched again when the linking rule changes
(action_center.member_link_stamp, relink_issue_members): the refresh rewrites
only the rows it matches to a story that run, so a corrected rule never
reached the archive (2026-10: "Trump's Education Department overhaul much
harder" kept offering "Contact Rep. Harder" after the rule stopped linking
it)."""

import json

import pytest

from app.api.action import _build_issue_response
from app.models import ActionIssue, FeedArticle, Representative, Senator
from app.pipeline.analyze import action_center
from app.pipeline.analyze.action_center import (
    _ISSUE_UPDATE_ATTRS,
    member_link_stamp,
    relink_issue_members,
)

HARDER_TITLE = "How Democrats could make the Education Department overhaul much bolder"


@pytest.fixture(autouse=True)
def _fresh_stamp():
    """Another test's patched linking function may have computed the cached
    stamp (test_action_center_candidate_pool stubs _find_related_officials)."""
    member_link_stamp.cache_clear()
    yield
    member_link_stamp.cache_clear()


def _entry(member_id, name, reason, chamber="house"):
    return {"id": member_id, "name": name, "state": "OR", "party": "D", "chamber": chamber,
            "match_reason": reason, "contact_form_url": "https://example.house.gov/contact"}


@pytest.fixture()
def db(db_session, monkeypatch):
    # The principal actor's fallback embeds the story; not under test here.
    monkeypatch.setattr(action_center, "_principal_actor", lambda title, summary, officials: "relabelled")
    db_session.add_all([
        Representative(id="robin-bolder", name="Robin Bolder", state="OR", party="D", district=4,
                       contact_form_url="https://bolder.house.gov/contact"),
        Senator(id="ann-ash", name="Ann Ash", state="VT", party="D"),
        # The stored prose writes "bolder" in lower case: an ordinary word.
        FeedArticle(url="https://example.org/a", source_name="Example", text="",
                    title="A bolder plan for roads", description="", embedding=b"", model="m"),
    ])
    db_session.commit()
    return db_session


def _issue(db, *, title=HARDER_TITLE, stamp=None, texts=None, senators=(), status="confirmed",
           is_current=False, date="2026-10-20"):
    row = ActionIssue(
        date=date, rank=1, title=title, summary="A plan.", facts="[]", status=status,
        is_current=is_current, principal_actor="president",
        related_senators=json.dumps(list(senators)), # As the refresh stores it: officials are matched from the
        # issue's own text, never the reporting.
        related_officials=json.dumps([s for s in senators if s["match_reason"] != "named in the reporting"]),
        member_link_stamp=stamp, member_link_texts=None if texts is None else json.dumps(texts),
    )
    db.add(row)
    db.commit()
    return row


def test_a_link_the_rule_no_longer_makes_is_dropped_with_its_contact_row(db):
    row = _issue(db, senators=[_entry("robin-bolder", "Robin Bolder", "referenced in coverage")])
    before = _build_issue_response(row, db)["relatedSenators"]
    assert [m["id"] for m in before] == ["robin-bolder"]  # the "Contact Rep." row's source

    assert relink_issue_members(db) == 1

    db.refresh(row)
    assert json.loads(row.related_senators) == []
    assert json.loads(row.related_officials) == []
    assert row.member_link_stamp == member_link_stamp()
    assert row.principal_actor == "relabelled"
    assert _build_issue_response(row, db)["relatedSenators"] == []


def test_a_row_matched_under_the_current_rule_is_left_alone(db):
    wrong = [_entry("robin-bolder", "Robin Bolder", "referenced in coverage")]
    row = _issue(db, stamp=member_link_stamp(), senators=wrong)
    assert relink_issue_members(db) == 0
    db.refresh(row)
    assert [m["id"] for m in json.loads(row.related_senators)] == ["robin-bolder"]


def test_drafts_are_not_matched(db):
    row = _issue(db, status="developing", senators=[_entry("robin-bolder", "Robin Bolder", "referenced in coverage")])
    relink_issue_members(db)
    db.refresh(row)
    assert row.member_link_stamp is None


def test_the_stored_reporting_is_read_again(db):
    named = _entry("ann-ash", "Ann Ash", "named in the reporting", "senate")
    kept = _issue(db, title="Senators call for a reversal", texts=["Sen. Ann Ash led the letter."], senators=[named])
    gone = _issue(db, title="Senators call for a reversal", texts=["Ash signed it."], senators=[named])
    relink_issue_members(db)
    db.refresh(kept)
    db.refresh(gone)
    assert [(m["id"], m["match_reason"]) for m in json.loads(kept.related_senators)] == [("ann-ash", "named in the reporting")]
    assert json.loads(gone.related_senators) == []


def test_a_row_from_before_the_reporting_was_kept_keeps_what_its_articles_named(db):
    named = _entry("ann-ash", "Ann Ash", "named in the reporting", "senate")
    row = _issue(db, title="Senators call for a reversal", texts=None, senators=[named])
    assert relink_issue_members(db) == 0
    db.refresh(row)
    assert [m["id"] for m in json.loads(row.related_senators)] == ["ann-ash"]
    assert row.member_link_stamp == member_link_stamp()


def test_a_bounded_batch_current_issues_first_then_newest(db):
    old = _issue(db, date="2026-10-01")
    new = _issue(db, date="2026-10-19")
    current = _issue(db, date="2026-09-01", is_current=True)
    relink_issue_members(db, limit=2)
    for row in (old, new, current):
        db.refresh(row)
    assert (current.member_link_stamp, new.member_link_stamp, old.member_link_stamp) == (
        member_link_stamp(), member_link_stamp(), None)


def test_the_stamp_follows_the_rule(monkeypatch):
    stamp = member_link_stamp()
    member_link_stamp.cache_clear()
    assert member_link_stamp() == stamp  # stable: no set-order or address in it
    # A constant the rule reads, two calls down (_proper_noun_mentions).
    monkeypatch.setattr(action_center, "TITLE_CASE_SHARE", 0.7)
    member_link_stamp.cache_clear()
    try:
        assert member_link_stamp() != stamp
    finally:
        monkeypatch.undo()
        member_link_stamp.cache_clear()


def test_a_matched_issue_takes_the_rule_and_reporting_it_was_matched_under():
    assert {"member_link_stamp", "member_link_texts", "related_senators", "related_officials"} <= set(_ISSUE_UPDATE_ATTRS)


def test_nothing_is_written_while_matching(db, monkeypatch):
    """A row changed mid-batch would be flushed by the next query and hold
    the write lock through the rest of it and the actor's embedding."""
    seen = []

    def actor(title, summary, officials):
        seen.append(bool(db.dirty))
        return "relabelled"

    monkeypatch.setattr(action_center, "_principal_actor", actor)
    wrong = [_entry("robin-bolder", "Robin Bolder", "referenced in coverage")]
    _issue(db, senators=wrong)
    _issue(db, senators=wrong, date="2026-10-19")
    assert relink_issue_members(db) == 2
    assert seen == [False, False]
