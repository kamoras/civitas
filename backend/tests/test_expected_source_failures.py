"""A source that refused Civitas or couldn't be read is recorded as its one
line and counted, never as a traceback; a crash keeps its traceback
(error_utils.ExpectedSourceFailure, SourceFailureCount)."""

import importlib
import pkgutil

import httpx
import pytest

import app
from app.error_utils import (
    ExpectedSourceFailure,
    SourceUnavailable,
    count_source_failure,
    exception_detail,
    exception_summary,
    failure_host,
    record_exception,
)
from app.models import StockTradesPipelineRun, SourceFailureCount
from app.pipeline.fetch.senate_ptr import EfdRefused
from app.source_failures import by_host


def _raised(exc: BaseException) -> BaseException:
    try:
        raise exc
    except BaseException as caught:
        return caught


def _all_subclasses(cls) -> set[type]:
    out = set()
    for sub in cls.__subclasses__():
        out |= {sub} | _all_subclasses(sub)
    return out


@pytest.fixture(scope="module")
def expected_classes() -> set[type]:
    # Every module under app/, so a class marked anywhere is checked.
    for info in pkgutil.walk_packages(app.__path__, "app."):
        try:
            importlib.import_module(info.name)
        except Exception:  # a module that needs its process (a child's entry point)
            pass
    return _all_subclasses(ExpectedSourceFailure)


def test_the_source_refusal_classes_are_marked(expected_classes):
    names = {cls.__name__ for cls in expected_classes}
    for name in (
        "EfdRefused", "HostRefusing", "SourceBlocked", "CongressUnavailable", "FecUnavailable",
        "GovinfoUnavailable", "FeedUnreadable", "UntrustedCount", "SecUnavailable", "SessionLapsed",
        "ScanSourceUnavailable", "SourceUnavailable",
    ):
        assert name in names, f"{name} is not an ExpectedSourceFailure"


def test_no_expected_class_gets_a_traceback(expected_classes):
    for cls in expected_classes:
        exc = cls.__new__(cls)
        BaseException.__init__(exc, "the source said no")
        exc = _raised(exc)
        detail = exception_detail(exc, "a_step")
        assert "Traceback" not in detail and "\n" not in detail, cls
        assert detail == exception_summary(exc, "a_step")
        assert ".py:" not in detail, cls  # no raising frame either
        assert exc.failure_kind in ("walled", "refused", "unavailable", "untrusted"), cls


def test_an_unexpected_error_keeps_its_traceback():
    detail = exception_detail(_raised(KeyError("member_id")), "score")
    assert "Traceback (most recent call last)" in detail
    assert "test_expected_source_failures.py:" in detail.split("\n", 1)[0]


def test_an_expected_failure_is_counted_per_day_step_host_and_kind(db_session):
    run = StockTradesPipelineRun(status="running")
    db_session.add(run)
    db_session.commit()
    for _ in range(2):
        line = record_exception(run, _raised(EfdRefused("Senate eFD's Akamai edge refused")), "senate_ptr")
    record_exception(run, _raised(ValueError("a bug")), "senate_ptr")
    db_session.commit()
    assert line == "senate_ptr: EfdRefused: Senate eFD's Akamai edge refused"
    assert "Traceback" in run.error_detail  # the bug's, and only the bug's
    assert run.error_detail.count("Traceback") == 1
    rows = db_session.query(SourceFailureCount).all()
    assert [(r.run_kind, r.step, r.host, r.kind, r.count) for r in rows] == [
        ("stock_trades_pipeline_runs", "senate_ptr", "efdsearch.senate.gov", "walled", 2),
    ]
    summary = by_host(db_session, 30)
    assert summary["total"] == 2
    host = summary["hosts"][0]
    assert host["host"] == "efdsearch.senate.gov" and host["byKind"]["walled"] == 2
    assert host["steps"] == ["stock trades · senate_ptr"]
    assert host["mark"]["shown"] == "2 failed steps"


def test_a_member_failed_by_its_source_is_counted_and_a_bug_is_not(db_session):
    from app.models import Senator
    from app.pipeline.fetch.fec import FecUnavailable
    from app.pipeline.run_checks import record_member_holds

    db_session.add(Senator(id="pat-doe", name="Pat Doe", state="WI", party="D", is_current=True))
    db_session.commit()
    record_member_holds(db_session, Senator, "senator", [
        ("pat-doe", _raised(FecUnavailable("FEC answered 503"))),
        ("lee-roe", _raised(FecUnavailable("FEC answered 503"))),
        ("kim-poe", _raised(ValueError("a bug"))),
    ])
    rows = db_session.query(SourceFailureCount).all()
    assert [(r.run_kind, r.step, r.host, r.kind, r.count) for r in rows] == [
        ("pipeline_runs", "member", "api.open.fec.gov", "unavailable", 2),
    ]


def test_a_step_failed_by_its_source_alerts_one_line_without_a_traceback_note(db_session, monkeypatch):
    import time

    from app.pipeline import progress_tracker

    sent = []
    monkeypatch.setattr(progress_tracker, "send_ops_alert", lambda subject, body, **kw: sent.append(body))
    run = StockTradesPipelineRun(status="running")
    db_session.add(run)
    db_session.commit()
    tracker = progress_tracker.ProgressTracker(run, [("senate_ptr", "fetch", "Senate")], db_session, time.time())
    tracker.begin("senate_ptr")
    try:
        raise EfdRefused("Senate eFD's Akamai edge refused the terms agreement.")
    except EfdRefused:
        tracker.fail("senate_ptr")
    assert sent[0].endswith("Cause: senate_ptr: EfdRefused: Senate eFD's Akamai edge refused the terms agreement.")
    assert "traceback" not in sent[0].lower() and ".." not in sent[0]


def test_a_count_that_cannot_be_written_never_breaks_the_caller(db_session):
    class Broken:
        def begin_nested(self):
            raise RuntimeError("no table")

    count_source_failure(Broken(), "pipeline_runs", "fetch", SourceUnavailable("down", host="x.gov"))


def test_the_host_comes_from_the_exception_or_its_request():
    assert failure_host(SourceUnavailable("down", host="api.example.gov")) == "api.example.gov"
    request = httpx.Request("GET", "https://www.example.gov/x")
    try:
        try:
            raise httpx.ConnectError("refused", request=request)
        except httpx.ConnectError as e:
            raise SourceUnavailable("could not read") from e
    except SourceUnavailable as e:
        assert failure_host(e) == "www.example.gov"
    assert failure_host(SourceUnavailable("down")) == "unknown"
    assert failure_host(EfdRefused("walled")) == "efdsearch.senate.gov"
