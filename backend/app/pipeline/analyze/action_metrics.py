"""Per-run validator counters for the Action Center pipeline.

2026-07 audit: every validator in the action pipeline (dropped facts,
grounding rejections, fail-closed issue skips, suppressed duplicate posts)
reported only via log lines — and the container restarts on every deploy,
so validator hit-rates were unmeasurable in practice ("how often does the
fact validator fire?" had no answer). Counters here are reset at the start
of each refresh, incremented at validator call sites, and persisted as a
row in api_cache (tier "action-metrics", keyed by run timestamp) at the
end of the run — queryable history with zero schema changes, pruned by the
same 60-day api_cache cleanup every other tier gets.

Kept in its own module (not action_center.py) because bluesky_poster.py
also increments counters, and it is imported BY action_center — counters
living in either file would be a circular import.

Same module-level-state-with-reset pattern as fec.py's run circuit
breaker (reset_run_state): the pipeline is single-flight per process, so
a plain dict with no locking is sufficient.
"""

import logging
from collections import Counter

logger = logging.getLogger(__name__)

_counters: Counter = Counter()


def reset() -> None:
    """Clear counters. Call at the start of each action-center refresh."""
    _counters.clear()


def increment(key: str, n: int = 1) -> None:
    _counters[key] += n


def snapshot() -> dict[str, int]:
    return dict(_counters)


def persist(db, run_key: str) -> None:
    """Write this run's counters to api_cache (tier "action-metrics")."""
    from app.pipeline.cache import api_cache_set

    try:
        api_cache_set(db, "action-metrics", run_key, {"counts": snapshot()})
    except Exception:
        logger.exception("Failed to persist action-metrics for run %s", run_key)
