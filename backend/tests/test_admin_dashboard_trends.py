"""Tests for the admin dashboard's time-series endpoints.

Covers page-load timing collection (api/visits.py's track-timing →
PageLoadTiming's bucketed histogram), the percentiles read back from it,
the zero-filled visitor/page-view series, and the date-windowed pipeline
run trend. Each exists to be drawn as a line over calendar days, so the
tests pin the two properties a line chart depends on: every day in the
window is present, and "nothing measured" (null) stays distinct from zero.
"""

from datetime import UTC, datetime, timedelta

from app.api.admin import (
    _histogram_percentile, admin_load_times, admin_pipeline_trend, admin_visitor_stats,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import database
from app.api import visits
from app.api.visits import (
    CONFIRMED_VISITS_SINCE, LOAD_TIMING_BUCKETS_MS, _bucket_for, track_timing,
)
from app.models import (
    HousePipelineRun, PageLoadTiming, PageView, PipelineRun, PipelineStatus, SiteVisit, VisitsMigration,
)
from app.time_utils import utcnow
from tests.visits_helpers import _drain_queue_and_write


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _days_ago(n: int) -> str:
    return (datetime.now(UTC).date() - timedelta(days=n)).isoformat()


class TestBucketFor:
    def test_value_on_a_bound_lands_in_that_bucket(self):
        assert _bucket_for(100) == 100

    def test_value_between_bounds_rounds_up_to_the_next_bound(self):
        assert _bucket_for(101) == 150
        assert _bucket_for(0) == LOAD_TIMING_BUCKETS_MS[0]

    def test_out_of_range_values_are_dropped_not_clamped(self):
        assert _bucket_for(-1) is None
        assert _bucket_for(float("nan")) is None
        assert _bucket_for(LOAD_TIMING_BUCKETS_MS[-1] + 1) is None


class TestTrackTiming:
    async def test_records_each_reported_metric_in_its_bucket(self, db_session):
        await track_timing(path="/politicians/jane-doe", ttfb=80, fcp=420, load=900, device=None)
        assert _drain_queue_and_write(db_session) == 1

        rows = {
            (r.path, r.metric, r.bucket_ms): r.count
            for r in db_session.query(PageLoadTiming).all()
        }
        # Route normalized exactly as track-visit does — never a per-id row.
        assert rows == {
            ("/politicians/[id]", "ttfb", 100): 1,
            ("/politicians/[id]", "fcp", 500): 1,
            ("/politicians/[id]", "load", 1000): 1,
        }

    async def test_repeat_loads_accumulate_in_the_same_cell(self, db_session):
        for _ in range(3):
            await track_timing(path="/leaderboard", ttfb=None, fcp=None, load=700, device=None)
        _drain_queue_and_write(db_session)

        row = db_session.query(PageLoadTiming).one()
        assert (row.metric, row.bucket_ms, row.count) == ("load", 750, 3)

    async def test_nothing_in_range_queues_nothing(self, db_session):
        await track_timing(path="/leaderboard", ttfb=-5, fcp=None, load=10 ** 7, device=None)
        assert _drain_queue_and_write(db_session) == 0


class TestTimingDeviceClass:
    async def test_device_class_is_its_own_cell(self, db_session):
        await track_timing(path="/", ttfb=None, fcp=420, load=None, device="mobile")
        await track_timing(path="/", ttfb=None, fcp=420, load=None, device="desktop")
        await track_timing(path="/", ttfb=None, fcp=420, load=None, device="mobile")
        _drain_queue_and_write(db_session)
        rows = {(r.device, r.count) for r in db_session.query(PageLoadTiming).all()}
        assert rows == {("mobile", 2), ("desktop", 1)}

    async def test_a_report_without_one_is_unrecorded(self, db_session):
        # A page loaded before this release sends no device.
        await track_timing(path="/", ttfb=None, fcp=420, load=None, device=None)
        _drain_queue_and_write(db_session)
        assert db_session.query(PageLoadTiming.device).scalar() == ""

    def _client(self):
        app = FastAPI()
        app.include_router(visits.router, prefix="/api")
        return TestClient(app)

    def test_only_the_three_classes_are_accepted(self, db_session):
        # The counter's device column has exactly three values: anything
        # else (a User-Agent pasted in, a made-up word) is refused whole.
        with self._client() as client:
            for bad in ("phone", "Mozilla/5.0 (iPhone)", "DESKTOP", ""):
                r = client.post("/api/track-timing", params={"path": "/", "fcp": 400, "device": bad})
                assert r.status_code == 422, bad
            assert client.post(
                "/api/track-timing", params={"path": "/", "fcp": 400, "device": "tablet"},
            ).status_code == 204
        _drain_queue_and_write(db_session)
        assert [(r.device, r.count) for r in db_session.query(PageLoadTiming).all()] == [("tablet", 1)]

    def test_the_caller_is_not_read(self, db_session):
        # Loads from different User-Agents and addresses land in one cell:
        # the endpoint learns the class the page sent, nothing about who sent it.
        with self._client() as client:
            for ua, ip in (("Mozilla/5.0 (iPhone) Mobile", "198.51.100.1"), ("curl/8", "203.0.113.9")):
                client.post(
                    "/api/track-timing", params={"path": "/", "fcp": 400, "device": "desktop"},
                    headers={"User-Agent": ua, "X-Real-IP": ip},
                )
        _drain_queue_and_write(db_session)
        assert [(r.device, r.count) for r in db_session.query(PageLoadTiming).all()] == [("desktop", 2)]


class TestCopyToDeviceSplit:
    def test_old_histogram_is_copied_once_as_unrecorded(self, db_session, monkeypatch):
        monkeypatch.setattr(database, "visits_engine", db_session.get_bind())
        db_session.execute(text(
            "CREATE TABLE page_load_timings (date VARCHAR(10), path VARCHAR(100), metric VARCHAR(8), "
            "bucket_ms INTEGER, count INTEGER, PRIMARY KEY (date, path, metric, bucket_ms))"
        ))
        db_session.execute(text(
            "INSERT INTO page_load_timings VALUES ('2026-10-01', '/', 'fcp', 500, 7), "
            "('2026-10-01', '/', 'load', 1000, 7)"
        ))
        db_session.commit()

        database._copy_page_load_timings()
        database._copy_page_load_timings()  # second start: no-op, no doubled counts
        db_session.expire_all()

        rows = {(r.metric, r.bucket_ms, r.device, r.count) for r in db_session.query(PageLoadTiming).all()}
        assert rows == {("fcp", 500, "", 7), ("load", 1000, "", 7)}
        assert db_session.query(VisitsMigration).count() == 1

    def test_fresh_install_has_nothing_to_copy(self, db_session, monkeypatch):
        monkeypatch.setattr(database, "visits_engine", db_session.get_bind())
        database._copy_page_load_timings()
        assert db_session.query(PageLoadTiming).count() == 0


class TestHistogramPercentile:
    def test_empty_histogram_is_none_not_zero(self):
        assert _histogram_percentile([], 0.5) is None

    def test_interpolates_inside_the_bucket_from_the_ladder_edge(self):
        # 10 samples, all in the 750-1000 bucket: the median is halfway up it.
        assert _histogram_percentile([(1000, 10)], 0.5) == 875.0

    def test_sparse_histogram_does_not_stretch_across_empty_buckets(self):
        # Lower edge of the 2000 bucket is 1500 (the ladder), not 100 (the
        # previous populated bucket).
        value = _histogram_percentile([(100, 1), (2000, 1)], 0.95)
        assert 1500 < value <= 2000


class TestVisitorStats:
    def test_every_day_is_present_and_zero_filled(self, db_session):
        db_session.add_all([
            SiteVisit(date=_today(), visitor_hash="a"),
            SiteVisit(date=_today(), visitor_hash="b"),
            SiteVisit(date=_days_ago(2), visitor_hash="c"),
            PageView(date=_today(), path="/", count=7),
            PageView(date=_today(), path="/leaderboard", count=3),
        ])
        db_session.commit()

        result = admin_visitor_stats(days=4, db=db_session)

        assert [r["date"] for r in result] == [_days_ago(3), _days_ago(2), _days_ago(1), _today()]
        assert [r["uniqueVisitors"] for r in result] == [0, 1, 0, 2]
        assert [r["pageViews"] for r in result] == [0, 0, 0, 10]

    def test_confirmed_visitors_start_with_confirmation(self, db_session, monkeypatch):
        # Before visits counted only browsers that ran the page, the rows
        # counted mostly automation: no confirmed figure for those days.
        monkeypatch.setattr("app.api.admin.CONFIRMED_VISITS_SINCE", _days_ago(1))
        db_session.add_all([
            SiteVisit(date=_days_ago(2), visitor_hash="crawler"),
            SiteVisit(date=_today(), visitor_hash="reader"),
        ])
        db_session.commit()

        result = admin_visitor_stats(days=3, db=db_session)
        assert [r["confirmedVisitors"] for r in result] == [None, 0, 1]
        assert [r["uniqueVisitors"] for r in result] == [1, 0, 1]

    def test_first_paint_loads_count_fcp_cells_on_every_device_and_route(self, db_session):
        db_session.add_all([
            PageLoadTiming(date=_today(), path="/", metric="fcp", bucket_ms=500, device="mobile", count=3),
            PageLoadTiming(date=_today(), path="/explore", metric="fcp", bucket_ms=750, device="", count=2),
            # One load reports several metrics: only its paint is counted.
            PageLoadTiming(date=_today(), path="/", metric="load", bucket_ms=1000, device="mobile", count=5),
            PageLoadTiming(date=_today(), path="/", metric="ttfb", bucket_ms=100, device="mobile", count=5),
        ])
        db_session.commit()

        assert admin_visitor_stats(days=1, db=db_session)[0]["firstPaintLoads"] == 5

    def test_confirmation_starts_the_first_whole_day_of_the_rebuilt_counting(self):
        assert CONFIRMED_VISITS_SINCE == "2026-10-11"

    def test_rows_outside_the_window_are_excluded(self, db_session):
        db_session.add(SiteVisit(date=_days_ago(10), visitor_hash="old"))
        db_session.commit()

        result = admin_visitor_stats(days=3, db=db_session)
        assert sum(r["uniqueVisitors"] for r in result) == 0


class TestLoadTimes:
    def test_days_without_samples_have_null_percentiles(self, db_session):
        db_session.add(PageLoadTiming(date=_today(), path="/", metric="load", bucket_ms=1000, count=4))
        db_session.commit()

        result = admin_load_times(days=2, db=db_session)

        yesterday, today = result["days"]
        assert yesterday["load"] == {"samples": 0, "p50": None, "p75": None, "p95": None}
        assert today["load"]["samples"] == 4
        assert 750 < today["load"]["p50"] <= 1000
        assert today["ttfb"]["samples"] == 0

    def test_by_path_ranks_slowest_p95_first(self, db_session):
        db_session.add_all([
            PageLoadTiming(date=_today(), path="/", metric="load", bucket_ms=500, count=5),
            PageLoadTiming(date=_today(), path="/explore", metric="load", bucket_ms=5000, count=5),
            # Other metrics don't enter the per-route ranking.
            PageLoadTiming(date=_today(), path="/about", metric="ttfb", bucket_ms=20000, count=5),
        ])
        db_session.commit()

        by_path = admin_load_times(days=7, db=db_session)["byPath"]
        assert [e["path"] for e in by_path] == ["/explore", "/"]


    def test_first_paint_loads_split_by_device(self, db_session):
        db_session.add_all([
            PageLoadTiming(date=_today(), path="/", metric="fcp", bucket_ms=500, device="mobile", count=3),
            PageLoadTiming(date=_today(), path="/", metric="fcp", bucket_ms=500, device="tablet", count=1),
            PageLoadTiming(date=_days_ago(1), path="/", metric="fcp", bucket_ms=500, device="", count=4),
            PageLoadTiming(date=_today(), path="/", metric="load", bucket_ms=500, device="desktop", count=9),
        ])
        db_session.commit()

        result = admin_load_times(days=2, db=db_session)
        yesterday, today = result["days"]
        assert today["devices"] == {"desktop": 0, "tablet": 1, "mobile": 3, "unrecorded": 0}
        assert yesterday["devices"] == {"desktop": 0, "tablet": 0, "mobile": 0, "unrecorded": 4}
        assert result["deviceTotals"] == {"desktop": 0, "tablet": 1, "mobile": 3, "unrecorded": 4}
        # Percentiles still pool every device.
        assert today["load"]["samples"] == 9


class TestPipelineTrend:
    async def test_returns_runs_of_every_type_in_window_oldest_first(self, db_session):
        now = utcnow()
        db_session.add_all([
            PipelineRun(started_at=now - timedelta(days=1), status=PipelineStatus.COMPLETED,
                        elapsed_seconds=3600),
            HousePipelineRun(started_at=now - timedelta(days=2), status=PipelineStatus.FAILED,
                             elapsed_seconds=120),
            PipelineRun(started_at=now - timedelta(days=40), status=PipelineStatus.COMPLETED),
        ])
        db_session.commit()

        result = await admin_pipeline_trend(days=30, db=db_session)

        assert [(r["pipelineType"], r["status"]) for r in result["runs"]] == [
            ("house", "failed"),
            ("senate", "completed"),
        ]
        assert result["runs"][1]["elapsedSeconds"] == 3600

    async def test_window_starts_at_midnight_utc_of_the_first_calendar_day(self, db_session):
        # days=2 covers yesterday and today by UTC date: a run late on the
        # day before yesterday is outside it even if it is < 48h old.
        today = datetime.now(UTC).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
        db_session.add_all([
            PipelineRun(started_at=today - timedelta(days=1), status=PipelineStatus.COMPLETED),
            PipelineRun(started_at=today - timedelta(days=1, minutes=1), status=PipelineStatus.FAILED),
        ])
        db_session.commit()

        runs = (await admin_pipeline_trend(days=2, db=db_session))["runs"]
        assert [r["status"] for r in runs] == ["completed"]


class TestTrackTimingPath:
    async def test_unknown_path_buckets_to_other_not_a_new_row_per_string(self, db_session):
        await track_timing(path="/<script>alert(1)</script>", ttfb=None, fcp=None, load=500, device=None)
        await track_timing(path="/wp-admin/x.php", ttfb=None, fcp=None, load=500, device=None)
        _drain_queue_and_write(db_session)
        rows = db_session.query(PageLoadTiming).all()
        assert [(r.path, r.count) for r in rows] == [("/other", 2)]


class TestProcStatCpu:
    def test_busy_excludes_idle_and_iowait_and_total_excludes_guest(self):
        from app.api.admin import _parse_proc_stat_cpu

        text = "cpu  100 5 50 800 40 3 2 0 7 0\ncpu0 1 2 3 4 5 6 7 8 0 0\n"
        busy, total = _parse_proc_stat_cpu(text)
        # user+nice+system+irq+softirq+steal = 160; idle+iowait = 840.
        assert (busy, total) == (160, 1000)

    def test_short_or_missing_line_is_handled(self):
        from app.api.admin import _parse_proc_stat_cpu

        assert _parse_proc_stat_cpu("cpu  10 0 5 85\n") == (15, 100)
        assert _parse_proc_stat_cpu("intr 12345\n") is None
        assert _parse_proc_stat_cpu("cpu  x y z w\n") is None

    def test_system_stats_reports_ticks(self):
        from app.api.admin import _read_system_stats

        stats = _read_system_stats()
        assert stats["cpuTotalTicks"] is None or stats["cpuTotalTicks"] >= stats["cpuBusyTicks"] >= 0
