"""Persisted per-step progress tracking for a pipeline run.

Generalized from senate_pipeline.py's original ProgressTracker (2026-07),
which was the only pipeline with step-by-step admin-dashboard visibility —
House, Supplementary, and Stock Trades pipeline runs had no equivalent, so
a failure or a slow phase in any of them was invisible until the whole run
finished or timed out. Each pipeline defines its own STEPS list (key,
phase, label) tuples and passes its own run row — any model with
progress_detail: str | None and elapsed_seconds: float | None columns.

Every terminal step transition also writes a durable PipelinePhaseTiming
row. `progress_detail` is live-run state — it is overwritten on the next
run and tells you nothing about the run before it. The timing rows are
the history, and they are what makes "which step got slower" answerable.
"""

import json
import logging
import time
from datetime import datetime

from sqlalchemy.orm import Session

from app.pipeline import rate_limiter
from app.time_utils import utcnow

logger = logging.getLogger(__name__)


class ProgressTracker:
    """Track sub-step progress within a pipeline run and persist to the DB."""

    def __init__(self, run, steps: list[tuple[str, str, str]], db: Session, start_time: float):
        self._run = run
        self._steps_def = steps
        self._db = db
        self._start_time = start_time
        self._steps: dict[str, dict] = {}
        # Rate-limiter counter snapshots taken at each step's begin(), so a
        # terminal transition can persist the delta over that step's window.
        self._rl_baselines: dict[str, dict] = {}
        for key, phase, label in steps:
            self._steps[key] = {
                "key": key,
                "phase": phase,
                "label": label,
                "status": "pending",
            }
        self._flush()

    def begin(self, key: str, *, total: int | None = None) -> None:
        step = self._steps.get(key)
        if not step:
            return
        step["status"] = "active"
        step["startedAt"] = _now_iso()
        if total is not None:
            step["total"] = total
            step["done"] = 0
        # Baseline for this step's rate-limiter accounting. Taken here
        # rather than at terminal time so the delta covers exactly the
        # step's own window.
        self._rl_baselines[key] = rate_limiter.snapshot()
        self._flush()

    def update(self, key: str, *, done: int | None = None, detail: str | None = None) -> None:
        step = self._steps.get(key)
        if not step:
            return
        if done is not None:
            step["done"] = done
        if detail is not None:
            step["detail"] = detail
        self._flush()

    def complete(self, key: str, *, detail: str | None = None) -> None:
        step = self._steps.get(key)
        if not step:
            return
        step["status"] = "done"
        step["completedAt"] = _now_iso()
        if detail is not None:
            step["detail"] = detail
        if "total" in step and "done" not in step:
            step["done"] = step["total"]
        self._flush()
        self._record_timing(step)

    def skip(self, key: str, *, detail: str | None = None) -> None:
        step = self._steps.get(key)
        if not step:
            return
        step["status"] = "skipped"
        # Set even for a step skipped before begin() (no startedAt to pair
        # it with, so _record_timing still reports duration_seconds=None) —
        # a step skipped *after* begin() otherwise loses its elapsed time
        # the same way complete()/fail() don't.
        step["completedAt"] = _now_iso()
        if detail:
            step["detail"] = detail
        self._flush()
        self._record_timing(step)

    def fail(self, key: str, *, detail: str | None = None) -> None:
        step = self._steps.get(key)
        if not step:
            return
        step["status"] = "failed"
        step["completedAt"] = _now_iso()
        if detail:
            step["detail"] = detail
        self._flush()
        self._record_timing(step)

    def _flush(self) -> None:
        ordered = [self._steps[k] for k, _, _ in self._steps_def]
        self._run.progress_detail = json.dumps(ordered)
        self._run.elapsed_seconds = round(time.time() - self._start_time, 1)
        try:
            self._db.commit()
        except Exception:
            logger.debug("Progress commit failed, rolling back", exc_info=True)
            self._db.rollback()

    def _record_timing(self, step: dict) -> None:
        """Persist one durable timing row for a step that just reached a
        terminal state.

        Runs in its own transaction *after* `_flush` has committed, and
        swallows every failure. Timing data is diagnostic; progress_detail
        is what the admin dashboard and the stuck-run detection read. If
        the two shared a transaction, a bad timing write would roll back
        the progress update with it — so the operational write always
        commits first and independently.

        Idempotent per (run_kind, run_id, step_key): a step driven to a
        terminal state twice (a retry, a resumed run) updates its existing
        row rather than colliding with the unique constraint.
        """
        from app.models import PipelinePhaseTiming

        run_id = getattr(self._run, "id", None)
        run_kind = getattr(self._run, "__tablename__", None)
        if run_id is None or run_kind is None:
            # Run row was never flushed — nothing stable to key timings on.
            return

        started = _parse_iso(step.get("startedAt"))
        completed = _parse_iso(step.get("completedAt"))
        duration = None
        if started and completed:
            duration = round((completed - started).total_seconds(), 3)

        try:
            row = (
                self._db.query(PipelinePhaseTiming)
                .filter_by(run_kind=run_kind, run_id=run_id, step_key=step["key"])
                .one_or_none()
            )
            if row is None:
                row = PipelinePhaseTiming(
                    run_kind=run_kind, run_id=run_id, step_key=step["key"],
                )
                self._db.add(row)
            row.phase = step.get("phase", "")
            row.label = step.get("label", "")
            row.status = step.get("status", "done")
            row.started_at = started
            row.completed_at = completed
            row.duration_seconds = duration
            self._db.commit()
        except Exception:
            logger.debug(
                "Phase timing write failed for %s/%s", run_kind, step.get("key"),
                exc_info=True,
            )
            try:
                self._db.rollback()
            except Exception:
                logger.debug("Phase timing rollback failed", exc_info=True)

        self._record_rate_limit_stats(step, run_kind, run_id)

    def _record_rate_limit_stats(self, step: dict, run_kind: str, run_id: int) -> None:
        """Persist how much of this step was spent blocked on each source's
        rate limiter.

        Only sources with activity in the step's window get a row, so a
        run's row count tracks what it actually touched rather than how
        many fetch modules exist. A step that was never begin()'d has no
        baseline and is skipped — without one there is no window to
        attribute, and diffing against zero would charge the step with
        every request the process has made since it started.

        Same failure posture as the timing write: separate commit, all
        exceptions swallowed. Diagnostics never take down a run.
        """
        from app.models import PipelineRateLimitStat

        baseline = self._rl_baselines.pop(step["key"], None)
        if baseline is None:
            return

        try:
            deltas = rate_limiter.diff_snapshots(baseline, rate_limiter.snapshot())
            if not deltas:
                return
            for source, (requests, blocked) in deltas.items():
                row = (
                    self._db.query(PipelineRateLimitStat)
                    .filter_by(
                        run_kind=run_kind, run_id=run_id,
                        step_key=step["key"], source=source,
                    )
                    .one_or_none()
                )
                if row is None:
                    row = PipelineRateLimitStat(
                        run_kind=run_kind, run_id=run_id,
                        step_key=step["key"], source=source,
                    )
                    self._db.add(row)
                row.requests = requests
                row.blocked_seconds = blocked
            self._db.commit()
        except Exception:
            logger.debug(
                "Rate limit stat write failed for %s/%s", run_kind, step.get("key"),
                exc_info=True,
            )
            try:
                self._db.rollback()
            except Exception:
                logger.debug("Rate limit stat rollback failed", exc_info=True)


def _parse_iso(value) -> "datetime | None":
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    return utcnow().isoformat()
