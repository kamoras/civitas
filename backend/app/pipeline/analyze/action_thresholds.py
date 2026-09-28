"""Where four Action Center similarity thresholds come from.

The same shape as explore_ranking.py: a value in use is the stored
calibration if one exists, else the bundled file, and the bundled file
says where its numbers came from. What is new here is where calibration
gets its evidence. None of these four had a logged distribution, so the
refresh now records one, and every point carries a label that is NOT the
similarity being calibrated — a threshold fitted to its own decisions
would only ever confirm itself:

  cluster_title    two articles' day-centered title similarity, labelled
                   by whether their named entities and numbers overlap
                   (_issue_signature / _signatures_match). Title
                   similarity alone cannot separate same-event from
                   same-theme (see _cluster_articles), which is why the
                   label has to come from somewhere else.
  near_identical   a new issue against an existing one, labelled by the
                   same signature test. The threshold is where title
                   similarity is conclusive on its own, so it is fitted
                   for precision: the lowest value above which no pair
                   was a different story.
  monitor_issue    issue vs monitor, labelled by the LLM gate that
                   already judges the borderline band.
  monitor_merge    monitor vs monitor, labelled the same way by the
                   merge gate.

A gate only ever sees pairs above its own threshold, so on its own it
could never show that the threshold is too high. Each run therefore also
asks it about the single closest pair BELOW (probe_below), one extra call
per stage per run, which is what lets the fitted value move down.

Counters live in action-metrics as f"thr_{name}_{yes|no}_{percent}" —
the same per-run rows, the same 60-day retention.
"""

from __future__ import annotations

import json
import logging
import pathlib
import threading
import time

from app.database import SessionLocal
from app.models import ApiCache
from app.pipeline.analyze import action_metrics
from app.pipeline.cache import api_cache_get, api_cache_set
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

NAMES = ("cluster_title", "near_identical", "monitor_issue", "monitor_merge")
_PRECISION_FITTED = frozenset({"near_identical"})

_BUNDLED = pathlib.Path(__file__).resolve().parents[2] / "data" / "action_thresholds.json"
_CACHE_TIER = "action"
_CACHE_KEY = "threshold_calibration"
# The stored calibration outlives any single day it failed to refresh, up
# to api_cache's own 60-day prune: a stale calibration beats none.
_STORED_MAX_AGE_HOURS = 24 * 60
_RECALIBRATE_AFTER_HOURS = 24
# Not a tuning value: how long a process reuses a loaded calibration
# before consulting the database, same bound explore_ranking uses.
_RELOAD_AFTER_SECONDS = 3600
# Fewer labelled pairs than this on either side and a fit is noise, so the
# previous value stays. Thirty is the usual floor for a proportion to be
# estimated at all; it is a sample-size rule, not a fitted value.
MIN_PER_CLASS = 30

_lock = threading.Lock()
_loaded: dict | None = None
_loaded_at = 0.0


def _bundled() -> dict:
    return json.loads(_BUNDLED.read_text())


def _stored(db) -> dict | None:
    raw = api_cache_get(db, _CACHE_TIER, _CACHE_KEY, max_age_hours=_STORED_MAX_AGE_HOURS)
    return raw if isinstance(raw, dict) else None


def get(name: str) -> float:
    """The threshold in use: stored calibration, else the bundled value."""
    global _loaded, _loaded_at
    with _lock:
        if _loaded is None or time.monotonic() - _loaded_at > _RELOAD_AFTER_SECONDS:
            values = dict(_bundled()["values"])
            try:
                db = SessionLocal()
                try:
                    values.update((_stored(db) or {}).get("values", {}))
                finally:
                    db.close()
            except Exception:
                logger.debug("No stored action-threshold calibration; using bundled", exc_info=True)
            _loaded, _loaded_at = values, time.monotonic()
        return float(_loaded[name])


def record(name: str, sim: float, same: bool) -> None:
    """One labelled pair for `name`'s distribution."""
    percent = min(100, max(0, round(sim * 100)))
    action_metrics.increment(f"thr_{name}_{'yes' if same else 'no'}_{percent}")


def fit(yes: dict[int, int], no: dict[int, int], precision: bool) -> float | None:
    """A threshold from labelled counts per similarity percent, or None
    when either class has fewer than MIN_PER_CLASS pairs.

    precision: the lowest value with no different-story pair at or above
    it (and enough same-story pairs there to trust). Otherwise the value
    that misclassifies fewest pairs — same-story below it plus
    different-story at or above it — the median of the tied values.
    """
    if sum(yes.values()) < MIN_PER_CLASS or sum(no.values()) < MIN_PER_CLASS:
        return None
    if precision:
        highest_no = max(no)
        if sum(n for p, n in yes.items() if p > highest_no) < MIN_PER_CLASS:
            return None
        return (highest_no + 1) / 100
    errors = [
        sum(n for p, n in yes.items() if p < t) + sum(n for p, n in no.items() if p >= t)
        for t in range(101)
    ]
    best = min(errors)
    tied = [t for t, e in enumerate(errors) if e == best]
    return tied[len(tied) // 2] / 100


def fit_all(db, previous: dict[str, float]) -> dict:
    """Every threshold refitted from all logged runs; a name without
    enough labelled pairs keeps its value from `previous`."""
    counts: dict[str, dict[str, dict[int, int]]] = {n: {"yes": {}, "no": {}} for n in NAMES}
    runs = 0
    for (data_json,) in db.query(ApiCache.data_json).filter(ApiCache.tier == "action-metrics"):
        runs += 1
        for key, n in (json.loads(data_json).get("counts") or {}).items():
            if not key.startswith("thr_"):
                continue
            name, label, percent = key[4:].rsplit("_", 2)
            if name in counts and label in ("yes", "no"):
                bucket = counts[name][label]
                bucket[int(percent)] = bucket.get(int(percent), 0) + n

    values = dict(previous)
    support = {}
    fitted_names = []
    for name in NAMES:
        yes, no = counts[name]["yes"], counts[name]["no"]
        support[name] = {"same": sum(yes.values()), "different": sum(no.values())}
        fitted = fit(yes, no, precision=name in _PRECISION_FITTED)
        if fitted is not None:
            values[name] = fitted
            fitted_names.append(name)
    return {"values": values, "support": support, "fitted": fitted_names, "runs_read": runs}


def calibrate(db) -> dict | None:
    """Refit and store, at most once per _RECALIBRATE_AFTER_HOURS.
    Returns what was stored, or None when it was too soon."""
    previous = _stored(db) or {}
    last = previous.get("calibrated_at")
    if last and (utcnow().timestamp() - float(last)) < _RECALIBRATE_AFTER_HOURS * 3600:
        return None
    result = fit_all(db, previous.get("values") or _bundled()["values"])
    result["calibrated_at"] = utcnow().timestamp()
    api_cache_set(db, _CACHE_TIER, _CACHE_KEY, result)
    global _loaded
    with _lock:
        _loaded = None
    logger.info(
        "Action thresholds calibrated from %d runs: %s (support %s)",
        result["runs_read"], result["values"], result["support"],
    )
    return result
