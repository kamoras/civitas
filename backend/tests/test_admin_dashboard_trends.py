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
from app.api.visits import (
    LOAD_TIMING_BUCKETS_MS, _bucket_for, _visit_queue, _write_visit_batch, track_timing,
)
from app.models import (
    HousePipelineRun, PageLoadTiming, PageView, PipelineRun, PipelineStatus, SiteVisit,
)
from app.time_utils import utcnow


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _days_ago(n: int) -> str:
    return (datetime.now(UTC).date() - timedelta(days=n)).isoformat()


def _drain(db) -> int:
    batch = []
    while not _visit_queue.empty():
        batch.append(_visit_queue.get_nowait())
    if batch:
        _write_visit_batch(batch, db)
    return len(batch)


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
        await track_timing(path="/politicians/jane-doe", ttfb=80, fcp=420, load=900)
        assert _drain(db_session) == 1

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
            await track_timing(path="/leaderboard", ttfb=None, fcp=None, load=700)
        _drain(db_session)

        row = db_session.query(PageLoadTiming).one()
        assert (row.metric, row.bucket_ms, row.count) == ("load", 750, 3)

    async def test_nothing_in_range_queues_nothing(self, db_session):
        await track_timing(path="/leaderboard", ttfb=-5, fcp=None, load=10 ** 7)
        assert _drain(db_session) == 0


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
        await track_timing(path="/<script>alert(1)</script>", ttfb=None, fcp=None, load=500)
        await track_timing(path="/wp-admin/x.php", ttfb=None, fcp=None, load=500)
        _drain(db_session)
        rows = db_session.query(PageLoadTiming).all()
        assert [(r.path, r.count) for r in rows] == [("/other", 2)]
