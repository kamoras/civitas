"""When each disclosure source was last read, and the note the trades and
holdings surfaces show while its reads fail (services/disclosure_sources.py):
an outage is never shown as a record with nothing new in it."""

import json
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import presidents, representatives, senators
from app.api.politicians import get_politician
from app.database import get_db
from app.error_utils import SourceWalled, failure_kind
from app.models import President, Senator, StockTrade, StockTradesPipelineRun
from app.pipeline.progress_tracker import ProgressTracker
from app.services import member_boxes as mb
from app.services.disclosure_sources import source_status

WALL_NOTE = ("Senate disclosures last read Oct 22, 2026. The Senate's disclosure site has refused "
             "automated reads since Oct 23; new filings appear once it allows them again.")


class _Walled(SourceWalled):
    """What a fetcher raises when its source's bot wall refuses it."""


def _run(db, started: datetime, **steps: tuple[str, datetime | None, str | None]) -> None:
    """A stock-trades run whose steps ended as given: key=(status, at, kind)."""
    detail = [{"key": key, "status": status, **({"completedAt": at.isoformat()} if at else {}),
               **({"failureKind": kind} if kind else {})}
              for key, (status, at, kind) in steps.items()]
    db.add(StockTradesPipelineRun(started_at=started, completed_at=started, status="completed",
                                  progress_detail=json.dumps(detail)))
    db.commit()


def _walled_since_oct_23(db) -> None:
    # Reads at 10:00 UTC (6 AM Eastern) each night.
    _run(db, datetime(2026, 10, 21, 9), senate_ptr=("done", datetime(2026, 10, 21, 10), None))
    _run(db, datetime(2026, 10, 22, 9), senate_ptr=("done", datetime(2026, 10, 22, 10), None),
         senate_holdings=("done", datetime(2026, 10, 22, 11), None), house_ptr=("done", datetime(2026, 10, 22, 10), None))
    _run(db, datetime(2026, 10, 23, 9), senate_ptr=("failed", datetime(2026, 10, 23, 10), "wall"),
         senate_holdings=("failed", datetime(2026, 10, 23, 11), "wall"), house_ptr=("done", datetime(2026, 10, 23, 10), None))
    _run(db, datetime(2026, 10, 24, 9), senate_ptr=("failed", datetime(2026, 10, 24, 10), "wall"),
         senate_holdings=("failed", datetime(2026, 10, 24, 11), "wall"), house_ptr=("done", datetime(2026, 10, 24, 10), None),
         # Killed mid-step by a deploy: says nothing about the source.
         president_ptr=("active", None, None))


def test_failure_kind_follows_the_chain():
    assert failure_kind(_Walled("403")) == "wall"
    try:
        try:
            raise _Walled("403")
        except _Walled as e:
            raise RuntimeError("phase failed") from e
    except RuntimeError as wrapped:
        assert failure_kind(wrapped) == "wall"
    assert failure_kind(RuntimeError("timeout")) == "outage"


def test_senate_efds_akamai_refusal_is_recorded_as_a_wall():
    from app.pipeline.fetch.senate_ptr import EfdRefused

    assert failure_kind(EfdRefused("403 Access Denied")) == "wall"


def test_a_failed_step_records_its_kind(db_session, freeze_utcnow):
    freeze_utcnow(datetime(2026, 10, 23, 10))
    run = StockTradesPipelineRun(status="running")
    db_session.add(run)
    db_session.commit()
    tracker = ProgressTracker(run, [("senate_ptr", "fetch", "Senate"), ("house_ptr", "fetch", "House")],
                              db_session, start_time=0.0)
    for key, error in (("senate_ptr", _Walled("403")), ("house_ptr", RuntimeError("timeout"))):
        tracker.begin(key)
        try:
            raise error
        except Exception:
            tracker.fail(key)
    steps = {s["key"]: s for s in json.loads(run.progress_detail)}
    assert steps["senate_ptr"]["failureKind"] == "wall"
    assert steps["house_ptr"]["failureKind"] == "outage"


def test_while_the_wall_holds_the_note_says_when_it_was_last_read(db_session, freeze_utcnow):
    freeze_utcnow(datetime(2026, 10, 24, 15))
    _walled_since_oct_23(db_session)
    status = source_status(db_session, "senate", "trades")
    assert status.last_read == "2026-10-22T10:00:00"
    assert status.failing_since == "2026-10-23T10:00:00"
    assert status.kind == "wall"
    assert status.note == WALL_NOTE
    assert source_status(db_session, "senate", "holdings").note.startswith("Senate disclosures last read Oct 22, 2026.")
    house = source_status(db_session, "house", "trades")
    assert house.last_read == "2026-10-24T10:00:00" and house.note is None and house.kind is None
    # Never read, and never failed either (the only try was killed).
    assert source_status(db_session, "president", "trades") is None
    assert "—" not in status.note


def test_the_first_good_read_clears_the_note(db_session, freeze_utcnow):
    freeze_utcnow(datetime(2026, 10, 25, 15))
    _walled_since_oct_23(db_session)
    _run(db_session, datetime(2026, 10, 25, 9), senate_ptr=("done", datetime(2026, 10, 25, 10), None))
    status = source_status(db_session, "senate", "trades")
    assert status.last_read == "2026-10-25T10:00:00"
    assert status.failing_since is None and status.note is None


def test_an_outage_is_worded_as_a_failed_read_not_a_refusal(db_session, freeze_utcnow):
    freeze_utcnow(datetime(2027, 1, 4, 15))
    _run(db_session, datetime(2026, 12, 30, 9), house_holdings=("done", datetime(2026, 12, 30, 10), None))
    # A row from before failureKind was recorded counts as an outage.
    _run(db_session, datetime(2027, 1, 2, 9), house_holdings=("failed", datetime(2027, 1, 2, 10), None))
    assert source_status(db_session, "house", "holdings").note == (
        "House disclosures last read Dec 30, 2026. Reads of the House Clerk's disclosure site have failed "
        "since Jan 2, 2027; new filings appear after the next successful read.")


def test_the_trades_box_never_reads_a_failed_read_as_no_trades():
    box = mb.trades_box(0, 0, "senate", WALL_NOTE)
    assert box["headline"] == "No trades on file" and box["dek"] == WALL_NOTE
    assert mb.trades_box(3, 1, "senate", WALL_NOTE)["dek"] == WALL_NOTE
    assert mb.trades_box(0, 0, "senate")["headline"] == "No trades disclosed"


@pytest.fixture
def client(db_session):
    app = FastAPI()
    for module in (senators, representatives, presidents):
        app.include_router(module.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


@pytest.fixture
def senator(db_session):
    db_session.add(Senator(id="ron-doe", bioguide_id="D000001", name="Ron Doe", state="OR", party="D"))
    db_session.add(StockTrade(senator_id="ron-doe", ticker=None, asset_name="Acme", owner="self",
                              transaction_type="purchase", transaction_date="2026-10-01",
                              disclosure_date="2026-10-21", days_to_disclose=20, amount_low=1001.0,
                              amount_high=15000.0, industry="UNCLASSIFIED", source_url="https://example.com",
                              filing_id="f1", parse_confidence="text"))
    db_session.commit()


def test_the_senator_endpoints_and_profile_serve_it_while_failing(client, senator, db_session, freeze_utcnow):
    freeze_utcnow(datetime(2026, 10, 24, 15))
    _walled_since_oct_23(db_session)
    trades = client.get("/api/senators/ron-doe/stock-trades").json()
    assert trades["sourceStatus"]["note"] == WALL_NOTE
    assert trades["sourceStatus"]["kind"] == "wall"
    assert trades["sourceStatus"]["lastRead"].startswith("2026-10-22T10:00")
    assert trades["sourceStatus"]["failingSince"].startswith("2026-10-23T10:00")
    holdings = client.get("/api/senators/ron-doe/holdings").json()
    assert holdings["available"] is False and holdings["sourceStatus"]["kind"] == "wall"
    boxes = json.loads(get_politician("ron-doe", db_session).body)["boxes"]
    assert boxes["trades"]["dek"] == WALL_NOTE
    assert boxes["trades"]["headline"] == "1 trade disclosed, 0 late"
    assert boxes["holdings"]["headline"] == "No report on file"


def test_and_say_nothing_once_reads_succeed(client, senator, db_session, freeze_utcnow):
    freeze_utcnow(datetime(2026, 10, 25, 15))
    _walled_since_oct_23(db_session)
    _run(db_session, datetime(2026, 10, 25, 9), senate_ptr=("done", datetime(2026, 10, 25, 10), None),
         senate_holdings=("done", datetime(2026, 10, 25, 11), None))
    trades = client.get("/api/senators/ron-doe/stock-trades").json()
    assert trades["sourceStatus"] == {"lastRead": "2026-10-25T10:00:00", "failingSince": None, "kind": None,
                                      "note": None}
    boxes = json.loads(get_politician("ron-doe", db_session).body)["boxes"]
    assert boxes["trades"]["dek"] == "Periodic transaction reports filed with the Senate."
    assert boxes["holdings"] is None


def test_only_the_sitting_presidents_page_speaks_for_the_reads(client, db_session, freeze_utcnow):
    freeze_utcnow(datetime(2026, 10, 24, 15))
    db_session.add_all([
        President(id="sitting", name="A Sitting", party="R", number=48, term_start="2025-01-20", is_current=True),
        President(id="former", name="A Former", party="D", number=47, term_start="2021-01-20", is_current=False),
    ])
    db_session.commit()
    _run(db_session, datetime(2026, 10, 24, 9), president_ptr=("failed", datetime(2026, 10, 24, 10), "outage"),
         president_holdings=("failed", datetime(2026, 10, 24, 11), "outage"))
    sitting = client.get("/api/presidents/sitting/stock-trades").json()["sourceStatus"]
    assert sitting["note"].startswith("Reads of the Office of Government Ethics' site have failed since Oct 24, 2026")
    assert client.get("/api/presidents/sitting/holdings").json()["sourceStatus"]["kind"] == "outage"
    assert client.get("/api/presidents/former/stock-trades").json()["sourceStatus"] is None
    assert client.get("/api/presidents/former/holdings").json()["sourceStatus"] is None


def test_a_representatives_endpoints_carry_the_house_source(client, db_session, freeze_utcnow):
    from app.models import Representative

    freeze_utcnow(datetime(2026, 10, 24, 15))
    db_session.add(Representative(id="r1", name="Jo Roe", state="TX", district=1, party="R", is_current=True))
    db_session.commit()
    _walled_since_oct_23(db_session)
    assert client.get("/api/representatives/r1/stock-trades").json()["sourceStatus"]["note"] is None
    assert client.get("/api/representatives/r1/holdings").json()["sourceStatus"] is None  # never tried
