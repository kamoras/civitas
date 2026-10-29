"""The source map: what each host answered (app/source_reads.py) and the
state each source is in now (app/source_health.py)."""

import json
import time
from datetime import datetime

import httpx
import pytest

from app import crawl_policy, crawler, source_health, source_reads
from app.models import SourceFailureCount, SourceReadStat, StockTradesPipelineRun
from app.pipeline.fetch.senate_ptr import EfdRefused
from app.pipeline.progress_tracker import ProgressTracker


@pytest.fixture(autouse=True)
def _fresh_reads():
    source_reads.reset_for_tests()
    yield
    source_reads.reset_for_tests()


class TestReads:
    def test_an_answer_clears_a_failure_and_a_failure_dates_from_the_first(self):
        source_reads.answered("a.gov", 200, False)
        source_reads.answered("a.gov", 503, False)
        first = source_reads.reads()["a.gov"]["failingSince"]
        source_reads.unanswered("a.gov", httpx.ConnectTimeout("slow"))
        rec = source_reads.reads()["a.gov"]
        assert rec["failingSince"] == first and rec["kind"] == "unavailable"
        assert rec["reason"] == "no answer in time"
        source_reads.answered("a.gov", 404, False)  # an answer about one address
        assert "failingSince" not in source_reads.reads()["a.gov"]

    def test_kinds(self):
        assert source_reads.failure_of(403, True)[0] == "walled"
        assert source_reads.failure_of(429, False)[0] == "refused"
        assert source_reads.failure_of(502, False)[0] == "unavailable"
        assert source_reads.failure_of(403, False) is None

    def test_two_processes_merge_newest_first(self):
        old = {"lastOk": "2026-10-20T00:00:00+00:00", "lastFailure": "2026-10-23T00:00:00+00:00",
               "failingSince": "2026-10-23T00:00:00+00:00", "kind": "walled", "reason": "wall"}
        new = {"lastFailure": "2026-10-25T00:00:00+00:00", "failingSince": "2026-10-25T00:00:00+00:00",
               "kind": "unavailable", "reason": "down"}
        merged = source_reads.merge(old, new)
        assert merged["failingSince"] == "2026-10-23T00:00:00+00:00"
        assert (merged["kind"], merged["reason"]) == ("unavailable", "down")
        assert merged["lastOk"] == "2026-10-20T00:00:00+00:00"

    def test_flush_writes_the_file_another_process_reads(self):
        source_reads.answered("b.gov", 200, False)
        source_reads.flush()
        source_reads.reset_for_tests()
        assert source_reads.reads()["b.gov"]["lastOk"]

    def test_counts_diff_per_step(self):
        before = source_reads.counts()
        source_reads.answered("c.gov", 200, False)
        source_reads.answered("c.gov", 500, False)
        assert source_reads.diff(before, source_reads.counts()) == {"c.gov": (1, 1)}


def _hosts(*names):
    # Read by senate_ptr, which only the stock-trades run imports.
    return [{"host": n, "readers": [{"module": "app.pipeline.fetch.senate_ptr", "rps": None, "registry": None}]}
            for n in names]


def _stock_run(db, run_id: int, when: str, status: str, step: str = "senate_ptr"):
    db.add(StockTradesPipelineRun(
        id=run_id, status="completed", started_at=datetime.fromisoformat(when),
        progress_detail=json.dumps([{"key": step, "status": status, "completedAt": when}]),
    ))


class TestMap:
    def test_each_state_by_rule(self, db_session, monkeypatch):
        monkeypatch.setattr(crawler, "hosts", lambda: _hosts(
            "walled.gov", "down.gov", "fine.gov", "never.gov", "skipped.gov"))
        monkeypatch.setattr(crawl_policy, "refusals", lambda: {
            "walled.gov": {"reason": "a bot wall's challenge (HTTP 403)", "since": "2026-10-23T04:00:00+00:00"},
        })
        source_reads.answered("down.gov", 200, False)
        source_reads.answered("down.gov", 503, False)
        source_reads.answered("fine.gov", 200, False)
        source_reads.answered("skipped.gov", 200, False)
        # skipped.gov was read in stock-trades run 1, and run 2 read only fine.gov.
        db_session.add_all([
            SourceReadStat(run_kind="stock_trades_pipeline_runs", run_id=1, step_key="senate_ptr",
                           host="skipped.gov", answered=3, failed=0),
            SourceReadStat(run_kind="stock_trades_pipeline_runs", run_id=2, step_key="house_ptr",
                           host="fine.gov", answered=2, failed=1),
            SourceFailureCount(date=source_health.utcnow().date().isoformat(), run_kind="pipeline_runs",
                               step="fetch", host="down.gov", kind="unavailable", count=2),
        ])
        db_session.commit()
        body = source_health.build(db_session)
        by = {e["host"]: e for e in body["sources"]}
        assert by["walled.gov"]["state"] == "walled"
        assert by["walled.gov"]["line"].startswith("Not readable since Oct 23, 2026: the site blocks automated reads")
        assert by["walled.gov"]["reason"] == "a bot wall's challenge (HTTP 403)"  # the admin's detail
        assert by["down.gov"]["state"] == "unavailable" and by["down.gov"]["failedSteps30d"] == 2
        assert by["fine.gov"]["state"] == "ok" and by["fine.gov"]["runs"] == ["stock trades · house_ptr"]
        # Not asked is not failed.
        assert by["never.gov"]["state"] == "not_read" and by["never.gov"]["line"] == "No read on record yet."
        assert by["skipped.gov"]["state"] == "not_read" and by["skipped.gov"]["lastGood"]
        assert [e["state"] for e in body["sources"]][:2] == ["walled", "unavailable"]
        assert by["fine.gov"]["reads"]  # what its reader reads, from the module's docstring
        public = source_health.public(body)
        assert set(public["sources"][0]) == {"host", "publisher", "takes", "state", "stateLabel", "since",
                                             "lastGood", "line"}

    def test_a_registry_wall_holds_until_the_host_answers(self, db_session, monkeypatch):
        monkeypatch.setattr(crawler, "hosts", lambda: _hosts("www.ohiosos.gov"))
        monkeypatch.setattr(crawl_policy, "refusals", lambda: {})
        entry = source_health.build(db_session)["sources"][0]
        assert entry["state"] == "walled" and entry["since"]
        source_reads.answered("www.ohiosos.gov", 200, False)
        assert source_health.build(db_session)["sources"][0]["state"] == "ok"

    def test_a_good_read_is_a_step_that_got_what_it_came_for(self, db_session, monkeypatch):
        # eFD: the last stock run that read it whole was Oct 22; from Oct 23
        # its senate_ptr step failed on it, though its home page still
        # answered 200 on the way to the refused agreement. The runs before
        # the per-step rows existed are read from their own progress records.
        monkeypatch.setattr(crawler, "hosts", lambda: _hosts("efd.example.gov"))
        monkeypatch.setattr(crawl_policy, "refusals", lambda: {})
        _stock_run(db_session, 1, "2026-10-22T03:40:00", "done")
        _stock_run(db_session, 2, "2026-10-23T03:41:00", "failed")
        _stock_run(db_session, 3, "2026-10-24T03:39:00", "failed")
        db_session.add(SourceReadStat(
            run_kind="stock_trades_pipeline_runs", run_id=3, step_key="senate_ptr", host="efd.example.gov",
            answered=1, failed=1, failed_on_host=True, recorded_at=datetime(2026, 10, 24, 3, 39),
        ))
        db_session.commit()
        source_reads.answered("efd.example.gov", 200, False)  # the home page, today
        source_reads.answered("efd.example.gov", 403, True)
        e = source_health.build(db_session)["sources"][0]
        assert e["lastGood"] == "2026-10-22" and e["since"] == "2026-10-23"
        assert e["state"] == "walled"
        assert e["line"] == ("Not readable since Oct 23, 2026: the site blocks automated reads. "
                             "Last good read Oct 22, 2026.")
        assert "Stock trades" in e["readBy"]

    def test_a_step_that_fails_on_a_host_it_never_reached_is_recorded(self, db_session):
        # eFD's refusal can be known before asking (its backoff): the step
        # still failed on eFD, with no request in its window.
        run = StockTradesPipelineRun(status="running")
        db_session.add(run)
        db_session.commit()
        tracker = ProgressTracker(run, [("senate_ptr", "fetch", "Senate")], db_session, time.time())
        tracker.begin("senate_ptr")
        try:
            raise EfdRefused("refused")
        except EfdRefused:
            tracker.fail("senate_ptr")
        row = db_session.query(SourceReadStat).one()
        assert (row.host, row.step_key, row.answered, row.failed_on_host) == (
            "efdsearch.senate.gov", "senate_ptr", 0, True)

    def test_read_by_comes_from_the_code_and_a_registry_wall_dates_an_ask(self, db_session, monkeypatch):
        # Ohio before any run has recorded a read: the Election run imports
        # its reader, and the registry's wall was measured on a request.
        monkeypatch.setattr(crawl_policy, "refusals", lambda: {})
        by = {e["host"]: e for e in source_health.build(db_session)["sources"]}
        assert "Election" in by["www.ohiosos.gov"]["readBy"]
        assert by["www.ohiosos.gov"]["lastAttempt"] == "2026-10-22"
        assert "Stock trades" in by["efdsearch.senate.gov"]["readBy"]
        # Nothing at all on record: never asked.
        assert by["thehill.com"]["lastAttempt"] is None

    def test_read_by_includes_the_scheduler_jobs_named_with_when_they_run(self, db_session, monkeypatch):
        # The national feeds are read by the hourly Action Center refresh,
        # found from scheduler.py's add_job calls, not by a nightly run.
        monkeypatch.setattr(crawl_policy, "refusals", lambda: {})
        by = {e["host"]: e for e in source_health.build(db_session)["sources"]}
        assert "Action Center, every hour at :15" in by["thehill.com"]["readBy"]
        assert "Agency sync, every hour at :30" in by["api.usaspending.gov"]["readBy"]
        assert any(label.startswith("Data file refresh, ") for label in by["dataverse.harvard.edu"]["readBy"])
        # Read only when a page asks: the API imports its reader.
        assert by["api.regulations.gov"]["readBy"] == ["Site pages, when a reader asks"]
        # Every source on the map is read by something the code names.
        assert [h for h, e in by.items() if not e["readBy"]] == []

    def test_hosts_the_site_only_sends_to_are_not_sources(self, db_session, monkeypatch):
        # The senders are found from the code: the module every alert goes
        # through, the one every post goes through, and the feedback route
        # that files a reader's message as an issue.
        assert source_health.outbound_modules() == {
            "app.ops_alerts", "app.broadcast", "app.api.feedback",
            "app.social.graph", "app.social.instagram", "app.social.threads",
        }
        monkeypatch.setattr(crawl_policy, "refusals", lambda: {})
        body = source_health.build(db_session)
        by = {e["host"]: e for e in body["sources"]}
        # GitHub is a source only because the data refresh reads election
        # returns there: read by that job alone, never by every job that
        # can raise an alert.
        assert [label.split(",")[0] for label in by["api.github.com"]["readBy"]] == ["Data file refresh"]
        assert set(by["api.github.com"]["alsoSendsTo"]) == {"app.ops_alerts", "app.api.feedback"}
        # Bluesky stays a source for its trends, but not for the site's own posts.
        assert by["public.api.bsky.app"]["alsoSendsTo"] == ["app.broadcast"]
        # A host only senders name is listed apart and kept off the public map.
        hosts = crawler.hosts()
        monkeypatch.setattr(crawler, "hosts", lambda: hosts + [
            {"host": "alerts.example", "readers": [{"module": "app.ops_alerts", "rps": None, "registry": None}]},
        ])
        body = source_health.build(db_session)
        assert {"host": "alerts.example", "modules": ["app.ops_alerts"]} in body["outbound"]
        assert "alerts.example" not in {e["host"] for e in source_health.public(body)["sources"]}

    def test_every_scheduler_job_is_found(self):
        jobs = crawler.job_readers()
        assert set(crawler.SCHEDULE) <= set(jobs)
        assert crawler.NIGHTLY_JOB not in jobs  # the nightly chain's runs are run_readers'
        assert jobs["action_refresh"]["label"] == "Action Center, every hour at :15"
        assert "app.pipeline.fetch.news_feeds" in jobs["action_refresh"]["modules"]

    def test_every_source_on_the_map_is_named_as_site_copy(self, db_session):
        # Who publishes it and what is taken, from the news feeds, the state
        # registries or the source ledger; a new host fails here until named.
        body = source_health.public(source_health.build(db_session))
        unnamed = [e["host"] for e in body["sources"] if not (e["publisher"] and e["takes"])]
        assert unnamed == [], f"name these in app/data/source_ledger.json: {unnamed}"
        for e in body["sources"]:
            for text in (e["publisher"], e["takes"], e["line"], body["summary"]):
                assert "—" not in text and "`" not in text and ".py" not in text, text
            assert e["takes"][0].isupper() or e["takes"][0].isdigit(), e["takes"]
        names = {e["host"]: e["publisher"] for e in body["sources"]}
        assert names["efdsearch.senate.gov"] == "Senate Office of Public Records (eFD)"
        assert names["thehill.com"] == "The Hill"
        assert names["www.eurekacountynv.gov"] == "Eureka County Clerk-Recorder"  # a republishing office
        assert names["www.ohiosos.gov"] == "Ohio Secretary of State"  # a per-state reader

    def test_every_host_the_code_names_is_on_the_map(self, db_session):
        # All but the hosts only the frontend's photo route reads, which the
        # backend can't see and the summary names.
        photo_only = {h["host"] for h in crawler.hosts()
                      if {r["module"] for r in h["readers"]} == {crawler.PHOTO_ROUTE}}
        hosts = {h["host"] for h in crawler.hosts()} - photo_only
        body = source_health.build(db_session)
        assert {e["host"] for e in body["sources"]} == hosts
        assert photo_only and "photo hosts" in body["summary"]
