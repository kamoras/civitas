"""Persisted per-step progress tracking for a pipeline run.

Generalized from senate_pipeline.py's original ProgressTracker (2026-07),
which was the only pipeline with step-by-step admin-dashboard visibility —
House, Supplementary, and Stock Trades pipeline runs had no equivalent, so
a failure or a slow phase in any of them was invisible until the whole run
finished or timed out. Each pipeline defines its own STEPS list (key,
phase, label) tuples and passes its own run row — any model with
progress_detail: str | None and elapsed_seconds: float | None columns.
"""

import json
import logging
import time

from sqlalchemy.orm import Session
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

    def skip(self, key: str, *, detail: str | None = None) -> None:
        step = self._steps.get(key)
        if not step:
            return
        step["status"] = "skipped"
        if detail:
            step["detail"] = detail
        self._flush()

    def fail(self, key: str, *, detail: str | None = None) -> None:
        step = self._steps.get(key)
        if not step:
            return
        step["status"] = "failed"
        step["completedAt"] = _now_iso()
        if detail:
            step["detail"] = detail
        self._flush()

    def _flush(self) -> None:
        ordered = [self._steps[k] for k, _, _ in self._steps_def]
        self._run.progress_detail = json.dumps(ordered)
        self._run.elapsed_seconds = round(time.time() - self._start_time, 1)
        try:
            self._db.commit()
        except Exception:
            logger.debug("Progress commit failed, rolling back", exc_info=True)
            self._db.rollback()


def _now_iso() -> str:
    return utcnow().isoformat()
