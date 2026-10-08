"""action_thresholds: fitting rules and the stored-else-bundled lookup."""

import json

import pytest

from app.pipeline.analyze import action_metrics, action_thresholds as t
from app.pipeline.cache import api_cache_set


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    """get() caches process-wide; a value one test loads must not reach
    another test's thresholds (monkeypatch puts back what was there)."""
    monkeypatch.setattr(t, "_loaded", None)


def _counts(pairs):
    out: dict[int, int] = {}
    for percent, n in pairs:
        out[percent] = out.get(percent, 0) + n
    return out


class TestFit:
    def test_too_few_pairs_on_either_side_fits_nothing(self):
        assert t.fit(_counts([(80, 29)]), _counts([(20, 100)])) is None
        assert t.fit(_counts([(80, 100)]), _counts([(20, 29)])) is None

    def test_separable_classes_split_in_the_middle_of_the_gap(self):
        # Every t in 0.31..0.70 errs on nothing; the median of those.
        assert t.fit(_counts([(70, 40)]), _counts([(30, 40)])) == 0.51

    def test_overlap_takes_the_fewest_errors(self):
        # Same-story at 60 (40) and 45 (5); different at 40 (40) and 55 (5).
        yes, no = _counts([(60, 40), (45, 5)]), _counts([(40, 40), (55, 5)])
        # t in 41..45 errs on 5 (the no at 55); 46..55 on 10; 56..60 on 5.
        # The median of the ten tied values is 0.56.
        assert t.fit(yes, no) == 0.56


class TestCalibrate:
    def _run(self, db, key, counts):
        api_cache_set(db, "action-metrics", key, {"counts": counts})

    def test_fits_from_logged_runs_and_get_serves_it(self, db_session, monkeypatch):
        monkeypatch.setattr(t, "SessionLocal", lambda: db_session)
        monkeypatch.setattr(db_session, "close", lambda: None)
        self._run(db_session, "run-1", {"thr_cluster_title_yes_75": 40, "thr_cluster_title_no_55": 40})
        result = t.calibrate(db_session)
        assert result["values"]["cluster_title"] == 0.66
        assert result["fitted"] == ["cluster_title"]
        # Unfitted names keep the bundled value.
        assert result["values"]["near_identical"] == 0.92
        assert t.get("cluster_title") == 0.66

    def test_runs_at_most_once_a_day(self, db_session):
        self._run(db_session, "run-1", {"thr_cluster_title_yes_75": 40, "thr_cluster_title_no_55": 40})
        assert t.calibrate(db_session) is not None
        assert t.calibrate(db_session) is None

    def test_record_writes_labelled_percent_counters(self):
        action_metrics.reset()
        t.record("cluster_title", 0.404, True)
        t.record("cluster_title", 0.404, False)
        assert action_metrics.snapshot() == {"thr_cluster_title_yes_40": 1, "thr_cluster_title_no_40": 1}


def test_bundled_file_names_every_threshold_and_its_source():
    bundled = json.loads(t._BUNDLED.read_text())
    # near_identical is bundled but never fitted (its only label was the
    # signature test; see the module docstring).
    assert set(bundled["values"]) == set(t.NAMES) | {"near_identical"}
    assert bundled["_source"]
