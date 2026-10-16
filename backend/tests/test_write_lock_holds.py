"""SQLite has one writer, and the Action Center refresh runs beside the
nightly chain. On 2026-10-15 the refresh's monitor stage opened a write
transaction with its orphan sweep and held it through an LLM call; the
Senate run's learning-store upsert (05:26) and the House sponsorship step
(07:23) each waited out the 30 s busy timeout inside that window and died
on "database is locked".

These run the refresh's stages on a file-backed database, as production
does, and at every slow call (model, embedding, network) make a pipeline
write from a second connection with a short busy timeout: it must get the
write lock. The pipeline's own learning-store writes are held to the same
rule (2026-10-16: an upsert left open through the classification after it
held the lock ~20 s). Then the retry that keeps one refused write from
failing a run.
"""

import json
import logging
from datetime import datetime

import numpy as np
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError

from app import database
from app.models import (
    ActionIssue,
    ActionIssueStatus,
    ApiCache,
    LearnedClassification,
    MonitorStatus,
    MonitorUpdate,
    NationalMonitor,
)
from app.pipeline import cache
from app.pipeline.analyze import action_center as ac
from app.pipeline.analyze import bill_analyzer, donor_classifier_ai, early_signal
from app.pipeline.transform import industry_classifier

TODAY = "2026-10-15"


@pytest.fixture()
def pipeline_write(file_sessionmaker):
    """A pipeline's learning-store upsert (the statement the Senate run
    died on) on a connection of its own, waiting 200 ms rather than 30 s
    for the lock. Called at each slow point; records whether it got in."""
    results: list[tuple[str, bool]] = []

    def write(where: str) -> None:
        session = file_sessionmaker()
        try:
            session.execute(text("PRAGMA busy_timeout = 200"))
            session.execute(sqlite_insert(LearnedClassification).values(
                entity_name=f"DONOR {len(results)}", entity_type="donor_type", value="INDIVIDUAL",
                confidence=1.0, source="fec",
            ).on_conflict_do_nothing())
            session.commit()
            results.append((where, True))
        except OperationalError as error:
            session.rollback()
            assert database.is_locked(error)
            results.append((where, False))
        finally:
            session.execute(text(f"PRAGMA busy_timeout = {database.SQLITE_BUSY_TIMEOUT_S * 1000}"))
            session.close()

    write.results = results
    return write


def _orthogonal():
    ids: dict[str, int] = {}

    def embed(texts):
        return np.array([np.eye(64)[ids.setdefault(t, len(ids))] for t in texts], dtype=np.float32)

    return embed


def test_the_monitor_stage_never_holds_the_write_lock_through_the_model(file_sessionmaker, pipeline_write, monkeypatch):
    """A monitor still named after a headline is put to the model on every
    refresh (the production case: its answer was refused as ungrounded,
    so nothing was renamed and nothing committed). The orphan sweep before
    it had already opened the write transaction."""
    db = file_sessionmaker()
    headline = "Senate panel advances diesel bill"
    monitor = NationalMonitor(slug="senate-panel-advances-diesel-bill", title=headline,
                              description=headline, status=MonitorStatus.ACTIVE, last_article_date=TODAY)
    db.add(monitor)
    db.flush()
    db.add(MonitorUpdate(monitor_id=monitor.id, date=TODAY, summary=headline,
                         source_url="https://example.org/1", source_name="NPR", article_title=headline))
    db.add(ActionIssue(date=TODAY, rank=1, title="Court blocks tariff order", summary="A court blocked it.",
                       source_names=json.dumps(["AP"]), source_urls=json.dumps(["https://example.org/2"]),
                       status=ActionIssueStatus.CONFIRMED))
    db.commit()

    embed = _orthogonal()

    class Model:
        def encode(self, texts, **_):
            pipeline_write("embedding")
            return embed(texts)

    def metadata(articles, session):
        pipeline_write("monitor name from the model")
        return None

    monkeypatch.setattr(ac, "get_embedding_model", lambda: Model())
    monkeypatch.setattr(ac, "_embed_texts_sim", lambda texts: (pipeline_write("title embedding"), embed(texts))[1])
    monkeypatch.setattr(ac, "_generate_monitor_metadata", metadata)

    ac._update_national_monitors(TODAY, db)
    db.close()

    assert ("monitor name from the model", True) in pipeline_write.results
    assert all(ok for _, ok in pipeline_write.results), pipeline_write.results


def test_the_signal_stages_commit_before_the_next_fetch(file_sessionmaker, pipeline_write, monkeypatch, freeze_utcnow):
    """A roll-call draft is committed before the Federal Register request
    and the news fetch after it, not held open through them."""
    # The vote is dated: on the clock's day after it, so it is still recent
    # enough to draft whatever date the suite runs on.
    freeze_utcnow(datetime(2026, 10, 15, 12))
    vote = {
        "chamber": "Senate", "congress": 119, "session": 1, "rollNumber": 301, "year": 2026,
        "question": "On Passage of the Bill", "voteTitle": "A bill to fund the parks", "result": "Bill Passed",
        "documentName": "", "voteDate": "October 14, 2026, 05:00 PM",
        "members": [{"voteCast": "Yea"}] * 60 + [{"voteCast": "Nay"}] * 40,
    }

    def rules(session):
        pipeline_write("Federal Register request")
        return []

    def news():
        pipeline_write("news fetch")
        return []

    monkeypatch.setattr(early_signal, "_fetch_recent_votes", lambda session: [vote])
    monkeypatch.setattr(early_signal, "classify_policy_area", lambda text: ("PUBLIC_LANDS", 0.9))
    monkeypatch.setattr(early_signal, "_fetch_recent_rules", rules)
    monkeypatch.setattr(ac, "fetch_news_articles", news)
    monkeypatch.setattr(ac, "_run_periodic_bluesky_posts", lambda session: None)

    db = file_sessionmaker()
    try:
        assert ac._run_refresh(db) == 0
    finally:
        db.close()

    assert [where for where, _ in pipeline_write.results] == ["Federal Register request", "news fetch"]
    assert all(ok for _, ok in pipeline_write.results), pipeline_write.results
    check = file_sessionmaker()
    assert check.query(ActionIssue).filter(ActionIssue.status == ActionIssueStatus.DEVELOPING).count() == 1
    check.close()


def _learned(file_sessionmaker, **filters) -> set[tuple[str, str, str]]:
    check = file_sessionmaker()
    rows = {(r.entity_name, r.entity_type, r.value)
            for r in check.query(LearnedClassification).filter_by(**filters)}
    check.close()
    return rows


async def test_bill_classification_commits_each_learning_write_before_the_next_bill(
    file_sessionmaker, pipeline_write, monkeypatch,
):
    """2026-10-16: record_classification's upsert was left open while the
    next bills were classified, holding the write lock ~20 s at a time.
    The same writer serves the Senate's key bills and recent votes and
    the House's bills."""
    def classify(text, bill_id=None, db_session=None):
        pipeline_write(f"classifying {bill_id}")
        return [{"area": "PROCEDURAL", "confidence": 0.95}]

    monkeypatch.setattr(bill_analyzer, "classify_policy_areas_multi", classify)
    bills = [{"billId": f"S.{n}", "billName": f"Bill {n}", "congress": 119, "summary": "", "actions": []}
             for n in (1, 2, 3)]
    db = file_sessionmaker()
    try:
        assert len(await bill_analyzer.classify_all_bills(bills, db_session=db)) == 3
    finally:
        db.close()

    assert [where for where, _ in pipeline_write.results] == ["classifying S.1", "classifying S.2", "classifying S.3"]
    assert all(ok for _, ok in pipeline_write.results), pipeline_write.results
    assert _learned(file_sessionmaker, entity_type="bill_policy") == {
        (f"S.{n}", "bill_policy", "PROCEDURAL") for n in (1, 2, 3)
    }


def test_donor_classification_commits_each_learning_write_before_classifying_on(
    file_sessionmaker, pipeline_write, monkeypatch,
):
    """2026-10-16: the main loop's last upsert stayed open through the kNN
    pass (20 s). Every slow step, in the loop and in the kNN pass, must
    find the lock free; the rows written are the same."""
    donors = [{"name": f"Acme {n} Inc", "amount": 100, "fec_receipt": {"entity_type": "ORG"}} for n in (1, 2)]
    donors.append({"name": "Mystery Fund", "amount": 5})

    def skip_check(name_upper):
        pipeline_write(f"skip check {name_upper}")
        return False

    def semantic(name, candidate_name=None):
        pipeline_write(f"semantic {name}")
        return None

    def knn(names, session, entity_type, **_):
        pipeline_write(f"kNN {entity_type}")
        return {n: "TECH" if entity_type == "industry" else "PAC" for n in names}

    monkeypatch.setattr(donor_classifier_ai, "classify_industries_batch_scored",
                        lambda names: {n: ("TECH", 0.9) for n in names if n.startswith("Acme")})
    monkeypatch.setattr(donor_classifier_ai, "is_skip_entity", skip_check)
    monkeypatch.setattr(donor_classifier_ai, "classify_donor_type_semantic", semantic)
    monkeypatch.setattr(donor_classifier_ai, "normalize_learning_store",
                        lambda session: pipeline_write("normalizing the learning store") or 0)
    monkeypatch.setattr(donor_classifier_ai, "cross_validate_donor_types",
                        lambda session: pipeline_write("cross-validating PAC labels") or 0)
    monkeypatch.setattr(donor_classifier_ai, "classify_batch_nn", knn)

    db = file_sessionmaker()
    try:
        result = donor_classifier_ai._classify_donors_hybrid_sync(donors, db)
    finally:
        db.close()

    assert result["MYSTERY FUND"] == {"type": "PAC", "industry": "TECH", "skip": False}
    assert ("kNN industry", True) in pipeline_write.results
    assert all(ok for _, ok in pipeline_write.results), pipeline_write.results
    assert {(n, t, v) for n, t, v in _learned(file_sessionmaker) if not n.startswith("DONOR ")} == {
        ("ACME 1 INC", "donor_type", "Org/Employees"), ("ACME 1 INC", "industry", "TECH"),
        ("ACME 2 INC", "donor_type", "Org/Employees"), ("ACME 2 INC", "industry", "TECH"),
        ("MYSTERY FUND", "donor_type", "PAC"), ("MYSTERY FUND", "industry", "TECH"),
    }


def test_industry_classification_commits_each_learning_write_before_the_next_name(
    file_sessionmaker, pipeline_write, monkeypatch,
):
    def classify(name):
        pipeline_write(f"embedding {name}")
        return "TECH", {}

    monkeypatch.setattr(industry_classifier, "classify_industry_with_provenance", classify)
    db = file_sessionmaker()
    try:
        for name in ("Acme Software", "Beta Software"):
            assert industry_classifier.classify_with_learning(name, db) == ("TECH", "embedding")
    finally:
        db.close()

    assert all(ok for _, ok in pipeline_write.results), pipeline_write.results
    assert _learned(file_sessionmaker, entity_type="industry") == {
        ("ACME SOFTWARE", "industry", "TECH"), ("BETA SOFTWARE", "industry", "TECH"),
    }


class _Locked:
    """A session whose first `failures` executes are refused as locked."""

    def __init__(self, failures: int, message: str = "database is locked"):
        self.failures, self.message, self.calls = failures, message, 0

    def execute(self, statement):
        self.calls += 1
        if self.calls <= self.failures:
            raise OperationalError("INSERT", {}, Exception(self.message))
        return "done"


def test_a_locked_write_is_retried_then_succeeds(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(database.time, "sleep", sleeps.append)
    db = _Locked(failures=database.LOCK_RETRY_ATTEMPTS - 1)

    assert database.execute_retrying_lock(db, "stmt") == "done"
    assert db.calls == database.LOCK_RETRY_ATTEMPTS
    assert sleeps == [database.LOCK_RETRY_BACKOFF_S * 2 ** i for i in range(database.LOCK_RETRY_ATTEMPTS - 1)]


def test_retries_are_bounded_and_other_errors_are_not_retried(monkeypatch):
    monkeypatch.setattr(database.time, "sleep", lambda s: None)
    always = _Locked(failures=99)
    with pytest.raises(OperationalError):
        database.execute_retrying_lock(always, "stmt")
    assert always.calls == database.LOCK_RETRY_ATTEMPTS

    missing = _Locked(failures=1, message="no such table: learned_classifications")
    with pytest.raises(OperationalError):
        database.execute_retrying_lock(missing, "stmt")
    assert missing.calls == 1


def test_a_cache_write_waits_out_a_held_lock_on_a_real_database(file_sessionmaker, monkeypatch):
    """The House sponsorship step's shape: its cosponsor cache write is
    refused while another connection holds the lock, and lands on retry
    once the lock is released, in the same session, so the step goes on."""
    holder = file_sessionmaker()
    holder.execute(sqlite_insert(LearnedClassification).values(
        entity_name="HELD", entity_type="donor_type", value="PAC", confidence=1.0, source="fec",
    ))  # holds the write lock, uncommitted

    def release_then_sleep(seconds):
        holder.commit()

    monkeypatch.setattr(database.time, "sleep", release_then_sleep)
    step = file_sessionmaker()
    step.execute(text("PRAGMA busy_timeout = 100"))
    cache.api_cache_set(step, "congress", "bill-cosponsors-119-hr-1", [{"bioguideId": "X000001"}])

    assert step.get(ApiCache, ("congress", "bill-cosponsors-119-hr-1")) is not None
    step.execute(text(f"PRAGMA busy_timeout = {database.SQLITE_BUSY_TIMEOUT_S * 1000}"))
    step.close()
    holder.close()


def test_a_long_write_hold_is_logged_with_where_it_began(tmp_path, monkeypatch, caplog):
    engine = create_engine(f"sqlite:///{tmp_path / 'hold.db'}")
    database.watch_write_holds(engine)
    monkeypatch.setattr(database, "WRITE_HOLD_WARN_S", -1.0)
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE t (x INTEGER)"))
        conn.commit()
        conn.execute(text("SELECT 1"))
        conn.commit()  # a read opens no write: nothing logged
        assert "write lock held" not in caplog.text
        with caplog.at_level(logging.WARNING, logger="app.database"):
            conn.execute(text("INSERT INTO t VALUES (1)"))
            conn.commit()
    engine.dispose()
    assert "SQLite write lock held" in caplog.text
