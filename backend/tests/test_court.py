"""/court and each justice's case list (services/court_service.py): every
figure counted from the stored votes and Martin-Quinn positions, none a
score or a rank. Justices here are made up: the Court's membership comes
from the data."""

import asyncio
import json
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import ExploreDocument, Justice, JusticeVote
from app.pipeline import justice_pipeline
from app.pipeline.fetch import justice_records
from app.services import court_service

# Five made-up justices, left to right by their latest position.
SEATS = [("left", -2.0), ("centre_left", -1.0), ("middle", 0.2), ("centre_right", 1.0), ("right", 2.5)]


def _seat(db, seats=SEATS, term=2024):
    for i, (jid, pos) in enumerate(seats):
        db.add(Justice(id=jid, name=jid.title(), last_name=jid, is_active=True, date_start=f"20{10 + i}-01-01",
                       thumbnail_url="x",
                       ideal_points=json.dumps([[term - 1, pos], [term, pos, pos - 0.5, pos + 0.5]])))
    db.commit()


def _case(db, case_id, term, decided, majority, minority=(), author=None, name="A V. B"):
    for jid in majority:
        db.add(JusticeVote(justice_id=jid, case_id=case_id, case_name=name, case_term=str(term),
                           decided_date=decided, vote="majority",
                           opinion_type="majority" if jid == author else "none",
                           majority_votes=len(majority), minority_votes=len(minority)))
    for jid in minority:
        db.add(JusticeVote(justice_id=jid, case_id=case_id, case_name=name, case_term=str(term),
                           decided_date=decided, vote="minority", opinion_type="dissent",
                           majority_votes=len(majority), minority_votes=len(minority)))
    db.commit()


ALL = [s for s, _ in SEATS]


def _get(db, path):
    app.dependency_overrides[get_db] = lambda: db
    try:
        return TestClient(app).get(path)
    finally:
        app.dependency_overrides.clear()


def test_the_term_begins_on_the_first_monday_in_october():
    # October 2026's first Monday is the 5th.
    assert court_service.current_term(date(2026, 10, 4)) == 2025
    assert court_service.current_term(date(2026, 10, 5)) == 2026
    assert court_service.current_term(date(2027, 1, 15)) == 2026


def test_the_line_orders_by_position_and_marks_the_median(db_session):
    _seat(db_session)
    c = court_service.court(db_session)
    assert [j["id"] for j in c["justices"]] == ALL
    assert c["mqTerm"] == 2024 and c["median"] == ["middle"]
    assert c["justices"][0]["mq"] == {"term": 2024, "position": -2.0, "low": -2.5, "high": -1.5}


def test_a_justice_without_a_position_for_the_term_is_listed_after_the_line(db_session):
    _seat(db_session)
    db_session.add(Justice(id="new", name="New", last_name="new", is_active=True, date_start="2026-10-01"))
    db_session.commit()
    c = court_service.court(db_session)
    assert c["justices"][-1]["id"] == "new" and c["justices"][-1]["mq"] is None
    # An even number placed has two middle justices; here five are placed.
    assert c["median"] == ["middle"]


def test_agreement_counts_the_cases_both_decided(db_session):
    _seat(db_session)
    _case(db_session, "scotus-2024-1", 2024, "2025-06-01", ALL)
    _case(db_session, "scotus-2024-2", 2024, "2025-06-02", ALL[2:], ALL[:2])
    # "right" sat out this one.
    _case(db_session, "scotus-2024-3", 2024, "2025-06-03", ALL[:3], ALL[3:4])
    pairs = {(p["a"], p["b"]): p for p in court_service.court(db_session)["agreement"]}
    assert pairs[("left", "centre_left")] == {"a": "left", "b": "centre_left", "agree": 3, "both": 3, "share": 100.0}
    assert pairs[("left", "right")] == {"a": "left", "b": "right", "agree": 1, "both": 2, "share": 50.0}
    assert pairs[("middle", "centre_right")]["agree"] == 2 and pairs[("middle", "centre_right")]["both"] == 3
    # Each pair once, left before right.
    assert len(pairs) == 10


def test_lineups_count_each_split_by_term_lopsided_first(db_session):
    _seat(db_session)
    _case(db_session, "scotus-2023-1", 2023, "2024-06-01", ALL)
    _case(db_session, "scotus-2024-1", 2024, "2025-06-01", ALL[2:], ALL[:2])
    _case(db_session, "scotus-2024-2", 2024, "2025-06-02", ALL[1:], ALL[:1])
    _case(db_session, "scotus-2024-3", 2024, "2025-06-03", ALL)
    c = court_service.court(db_session)
    assert c["lineups"]["splits"] == ["5-0", "4-1", "3-2"]
    assert c["lineups"]["terms"] == [
        {"term": 2023, "cases": 1, "counts": {"5-0": 1, "4-1": 0, "3-2": 0}},
        {"term": 2024, "cases": 3, "counts": {"5-0": 1, "4-1": 1, "3-2": 1}},
    ]
    assert c["record"] == {"cases": 4, "unanimous": 2, "oneVote": 1, "unanimousPct": 50, "oneVotePct": 25, "terms": [2023, 2024]}


def test_a_new_term_with_no_decisions_says_so(db_session, freeze_utcnow):
    freeze_utcnow(datetime(2026, 10, 16, 12))
    _seat(db_session)
    _case(db_session, "scotus-2025-1", 2025, "2026-06-01", ALL)
    c = court_service.court(db_session)
    assert c["currentTerm"] == {"term": 2026, "decided": 0}
    assert c["record"]["terms"] == [2025]


def test_nothing_stored_is_empty_never_a_made_up_figure(db_session):
    c = court_service.court(db_session)
    assert c["justices"] == [] and c["median"] == [] and c["mqTerm"] is None
    assert c["record"] == {"cases": 0, "unanimous": 0, "oneVote": 0, "unanimousPct": None, "oneVotePct": None, "terms": []}
    assert c["decisions"]["decisions"] == [] and c["agreement"] == []


def test_decisions_name_the_lineup_and_link_their_sources(db_session):
    _seat(db_session)
    _case(db_session, "scotus-2024-23-191", 2024, "2025-06-02", ALL[2:], ALL[:2], author="middle")
    _case(db_session, "scotus-2024-2024-007", 2024, "2025-06-01", ALL)  # the Database's own id
    db_session.add(ExploreDocument(doc_type="Supreme Court Opinion", source="supremecourt.gov", title="A v. B (No. 23-191)", date="2025-06-02",
                                   url="https://www.supremecourt.gov/opinions/24pdf/23-191_x.pdf",
                                   external_id="scotus-2024-23-191"))
    db_session.commit()
    body = _get(db_session, "/api/court/decisions?per_page=1").json()
    assert body["total"] == 2 and body["page"] == 1
    [d] = body["decisions"]
    assert d["name"] == "A v. B" and d["split"] == "3-2" and d["author"] == "middle"
    assert sorted(d["majority"]) == sorted(ALL[2:]) and sorted(d["minority"]) == sorted(ALL[:2])
    assert d["links"]["oyez"] == "https://www.oyez.org/cases/2024/23-191"
    assert d["links"]["opinion"].endswith("23-191_x.pdf") and d["links"]["explore"].startswith("/explore/") and not d["links"]["explore"].removeprefix("/explore/").isdigit()
    [older] = _get(db_session, "/api/court/decisions?per_page=1&page=2").json()["decisions"]
    assert older["links"] == {"oyez": None, "opinion": None, "explore": None}
    assert older["name"] == "A V. B" and older["unanimous"] is True


def test_a_docket_page_is_not_called_an_opinion(db_session):
    _seat(db_session)
    _case(db_session, "scotus-2025-24-38", 2025, "2026-06-01", ALL)
    db_session.add(ExploreDocument(doc_type="Supreme Court Opinion", source="supremecourt.gov", title="C v. D (No. 24-38)", date="2026-06-01",
                                   url="https://www.supremecourt.gov/docket/docketfiles/html/public/24-38.html",
                                   external_id="scotus-2025-24-38"))
    db_session.commit()
    [d] = court_service.decisions(db_session)["decisions"]
    assert d["links"]["opinion"] is None and d["links"]["explore"]


def test_a_justices_cases_are_paginated_newest_first(db_session):
    _seat(db_session)
    for n in range(5):
        _case(db_session, f"scotus-2024-{n}", 2024, f"2025-06-0{n + 1}", ALL[1:], ALL[:1], author="middle")
    _case(db_session, "scotus-2023-9", 2023, "2024-06-01", ALL)
    body = _get(db_session, "/api/justices/left/cases?per_page=2").json()
    assert body["total"] == 6 and body["terms"] == [2023, 2024]
    assert [c["caseId"] for c in body["cases"]] == ["scotus-2024-4", "scotus-2024-3"]
    assert body["cases"][0]["vote"] == "minority" and body["cases"][0]["opinion"] == "dissent"
    assert body["cases"][0]["split"] == "4-1"
    last = _get(db_session, "/api/justices/left/cases?per_page=2&page=3").json()["cases"]
    assert [c["caseId"] for c in last] == ["scotus-2024-0", "scotus-2023-9"]
    assert _get(db_session, "/api/justices/middle/cases").json()["cases"][0]["opinion"] == "majority"
    assert _get(db_session, "/api/justices/nobody/cases").status_code == 404
    assert _get(db_session, "/api/justices/left/cases?per_page=500").status_code == 422


def test_the_record_says_which_terms_it_covers(db_session):
    _seat(db_session)
    _case(db_session, "scotus-2023-1", 2023, "2024-06-01", ALL)
    _case(db_session, "scotus-2025-1", 2025, "2026-06-01", ALL)
    body = _get(db_session, "/api/justices/left").json()
    assert body["recordTerms"] == [2023, 2025]
    # The stored interval stays off this field: [term, position] only.
    assert body["idealPoints"] == [[2023, -2.0], [2024, -2.0]]


def test_martin_quinn_rows_carry_the_credible_interval(db_session):

    csv_text = ('"term","justice","justiceName","post_mn","post_sd","post_med","post_025","post_975"\n'
                '2024,1,"AJustice",0.5151,0.3,0.51,-0.0712,1.1\n'
                '2023,1,"AJustice",0.6,0.3,0.6,,\n')
    with patch.object(justice_records, "_get", AsyncMock(return_value=SimpleNamespace(text=csv_text))):
        out = asyncio.run(justice_records.fetch_martin_quinn(None, db_session))
    assert out == {"AJustice": [[2024, 0.515, -0.071, 1.1], [2023, 0.6]]}


def test_an_unread_martin_quinn_file_is_reported(db_session):

    measured = {"loyalty": {}, "term": 2024, "current": [], "cases": [], "ideal": None}
    listed = [{"identifier": "j1", "name": "J One", "last_name": "One",
               "roles": [{"date_start": 1, "date_end": 0, "role_title": "Associate Justice"}]}]
    with patch.object(justice_pipeline, "fetch_justice_list", AsyncMock(return_value=listed)), \
         patch.object(justice_pipeline, "fetch_case_votes", AsyncMock(return_value=[])), \
         patch.object(justice_pipeline, "_measure_loyalty", AsyncMock(return_value=(measured, None))):
        result = asyncio.run(justice_pipeline.run_justice_pipeline(db_session))
    assert result["positions_unread"] is True
