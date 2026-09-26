"""Tests for GET /api/sitemap (app/api/sitemap.py) — the id list the
frontend sitemap is built from."""

import json
from datetime import datetime

import pytest

from app.api.sitemap import _iso_date, sitemap_entries
from app.config import settings
from app.issue_ids import to_public_id
from app.models import ActionIssue, ActionIssueStatus, Justice, President, Representative, Senator, SponsoredBill
from app.services.bill_service import clear_bill_collection_cache


@pytest.fixture(autouse=True)
def _reset_bill_collection_cache():
    # _collect_bills caches at module scope; each test gets a fresh DB.
    clear_bill_collection_cache()
    yield
    clear_bill_collection_cache()


def _body(db):
    return json.loads(sitemap_entries(db).body)


def test_lists_every_profile_branch_including_former_presidents(db_session):
    db_session.add_all([
        Senator(id="s1", name="Sen A", state="CA", party="D", is_current=True),
        Representative(id="r1", name="Rep B", state="TX", party="R", is_current=False),
        President(id="lincoln-16", name="Abraham Lincoln", party="R", number=16,
                  term_start="1861-03-04", is_current=False),
        Justice(id="j1", name="Justice C", last_name="C"),
    ])
    db_session.commit()

    ids = {p["id"] for p in _body(db_session)["politicians"]}

    assert ids == {"s1", "r1", "lincoln-16", "j1"}


def test_issues_use_public_ids_not_row_ids(db_session):
    issue = ActionIssue(date="2026-09-20", rank=1, title="T", is_current=False)
    db_session.add(issue)
    db_session.commit()

    assert _body(db_session)["issues"] == [
        {"id": to_public_id(issue.id), "lastmod": "2026-09-20"}
    ]


def test_unconfirmed_developing_issues_are_left_out(db_session):
    confirmed = ActionIssue(date="2026-09-20", rank=1, title="Confirmed", is_current=True)
    developing = ActionIssue(date="2026-09-21", rank=2, title="Developing", is_current=True,
                             status=ActionIssueStatus.DEVELOPING)
    expired = ActionIssue(date="2026-09-19", rank=3, title="Expired unconfirmed", is_current=False,
                          status=ActionIssueStatus.DEVELOPING)
    db_session.add_all([confirmed, developing, expired])
    db_session.commit()

    assert [i["id"] for i in _body(db_session)["issues"]] == [to_public_id(confirmed.id)]


def test_bills_match_the_bills_feed_and_are_deduped(db_session):
    db_session.add(Senator(id="s1", name="Sen A", state="CA", party="D", is_current=True))
    db_session.add(Senator(id="s2", name="Sen B", state="NY", party="R", is_current=False))
    db_session.flush()
    for sid, bill_id in (("s1", "S.1"), ("s1", "S.1"), ("s2", "S.2")):
        db_session.add(SponsoredBill(
            senator_id=sid, bill_id=bill_id, title="A bill", stage="introduced",
            congress=settings.CURRENT_CONGRESS, latest_action_date="2026-03-04",
        ))
    db_session.commit()

    # S.2's sponsor has left office, so /bills doesn't list it either.
    assert _body(db_session)["bills"] == [{"id": "S.1", "lastmod": "2026-03-04"}]


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("", None),
        ("not a date", None),
        ("2026-07-04", "2026-07-04"),
        ("2026-07-04T12:00:00", "2026-07-04"),
        (datetime(2026, 7, 4, 12, 0), "2026-07-04"),
    ],
)
def test_iso_date_never_emits_an_invalid_lastmod(value, expected):
    assert _iso_date(value) == expected
