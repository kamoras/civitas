"""action_thresholds: fitting rules and the stored-else-bundled lookup."""

import json
from types import SimpleNamespace

import numpy as np
import pytest

from app.pipeline.analyze import action_center as ac
from app.pipeline.analyze import action_metrics, action_thresholds as t
from app.pipeline.cache import api_cache_set


@pytest.fixture(autouse=True)
def _fresh_cache():
    """get() caches process-wide; a value one test loads must not reach
    another test's thresholds."""
    t._loaded = None
    yield
    t._loaded = None


def _counts(pairs):
    out: dict[int, int] = {}
    for percent, n in pairs:
        out[percent] = out.get(percent, 0) + n
    return out


class TestFit:
    def test_too_few_pairs_on_either_side_fits_nothing(self):
        assert t.fit(_counts([(80, 29)]), _counts([(20, 100)]), precision=False) is None
        assert t.fit(_counts([(80, 100)]), _counts([(20, 29)]), precision=False) is None

    def test_separable_classes_split_in_the_middle_of_the_gap(self):
        # Every t in 0.31..0.70 errs on nothing; the median of those.
        assert t.fit(_counts([(70, 40)]), _counts([(30, 40)]), precision=False) == 0.51

    def test_overlap_takes_the_fewest_errors(self):
        # Same-story at 60 (40) and 45 (5); different at 40 (40) and 55 (5).
        yes, no = _counts([(60, 40), (45, 5)]), _counts([(40, 40), (55, 5)])
        # t in 41..45 errs on 5 (the no at 55); 46..55 on 10; 56..60 on 5.
        # The median of the ten tied values is 0.56.
        assert t.fit(yes, no, precision=False) == 0.56

    def test_precision_is_just_above_the_highest_different_story_pair(self):
        """near_identical is where title cosine alone is conclusive: no
        different-story pair may sit above it."""
        yes, no = _counts([(95, 40), (80, 10)]), _counts([(30, 30), (88, 1)])
        assert t.fit(yes, no, precision=True) == 0.89

    def test_precision_needs_enough_same_story_pairs_above(self):
        yes, no = _counts([(95, 5), (80, 40)]), _counts([(30, 30), (88, 1)])
        assert t.fit(yes, no, precision=True) is None


class TestCalibrate:
    def _run(self, db, key, counts):
        api_cache_set(db, "action-metrics", key, {"counts": counts})

    def test_fits_from_logged_runs_and_get_serves_it(self, db_session, monkeypatch):
        monkeypatch.setattr(t, "SessionLocal", lambda: db_session)
        monkeypatch.setattr(db_session, "close", lambda: None)
        self._run(db_session, "run-1", {"thr_monitor_issue_yes_75": 40, "thr_monitor_issue_no_55": 40})
        result = t.calibrate(db_session)
        assert result["values"]["monitor_issue"] == 0.66
        assert result["fitted"] == ["monitor_issue"]
        # Unfitted names keep the bundled value.
        assert result["values"]["near_identical"] == 0.92
        assert t.get("monitor_issue") == 0.66

    def test_runs_at_most_once_a_day(self, db_session):
        self._run(db_session, "run-1", {"thr_monitor_issue_yes_75": 40, "thr_monitor_issue_no_55": 40})
        assert t.calibrate(db_session) is not None
        assert t.calibrate(db_session) is None

    def test_record_writes_labelled_percent_counters(self):
        action_metrics.reset()
        t.record("cluster_title", 0.404, True)
        t.record("cluster_title", 0.404, False)
        assert action_metrics.snapshot() == {"thr_cluster_title_yes_40": 1, "thr_cluster_title_no_40": 1}


def test_bundled_file_names_every_threshold_and_its_source():
    bundled = json.loads(t._BUNDLED.read_text())
    assert set(bundled["values"]) == set(t.NAMES)
    assert bundled["_source"]


class TestMergeProbe:
    """A gate only sees pairs above its floor, so each merge pass asks it
    once about the closest pair below — recorded, never acted on."""

    def test_the_closest_pair_below_the_floor_is_judged_once_and_not_merged(self, monkeypatch):
        monitors = [SimpleNamespace(id=i, title=f"M{i}", description="", updates=[]) for i in range(3)]
        # Pairwise similarities 0.30 (0,1), 0.20 (0,2), 0.10 (1,2): all below 0.42.
        vecs = {"M0": [1.0, 0.0, 0.0], "M1": [0.30, 0.954, 0.0], "M2": [0.20, -0.0, 0.980]}

        class Model:
            def encode(self, texts, normalize_embeddings=True):
                return np.array([vecs[x.split()[0]] for x in texts])

        asked = []
        monkeypatch.setattr(ac, "_should_merge_monitors_llm", lambda a, b, db: asked.append((a.id, b.id)) or True)
        monkeypatch.setattr(ac, "_merge_monitors", lambda *a: (_ for _ in ()).throw(AssertionError("merged")))
        action_metrics.reset()
        assert ac._merge_similar_monitors(monitors, Model(), db=None) is False
        assert asked == [(0, 1)]
        assert action_metrics.snapshot() == {"thr_monitor_merge_yes_30": 1}
