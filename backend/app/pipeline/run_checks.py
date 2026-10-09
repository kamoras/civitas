"""Post-run quality gates shared by the Senate and House pipelines.

Both orchestrators end with the same two non-fatal checks: a score-calibration
drift report, and a ground-truth / score-distribution gate that persists any
failures on the run record and fires an ops alert. These were copy-pasted
(differing only in the "senator"/"representative" label and the alert text).
This is the shared implementation.

Both chambers run the same derived consistency gate (``check_ground_truth``
+ ``check_score_distribution`` — expectations computed from each chamber's
own raw data, no named reference members); each pipeline gathers its
``gt_failures`` and calls ``persist_ground_truth_failures`` here.
"""

import hashlib
import json
import logging
from collections.abc import Iterable
from typing import Any

from app.ops_alerts import recent_alerts, resolve_ops_alert, send_ops_alert

logger = logging.getLogger(__name__)


def run_calibration_check(entity_type: str) -> None:
    """Log any score-calibration drift for ``entity_type`` ("senator" /
    "representative"). Non-fatal — a failure here never aborts the pipeline."""
    try:
        from app.pipeline.analyze.score_calibration import generate_calibration_report
        report = generate_calibration_report(entity_type)
        if report and report["drift_events"]:
            for evt in report["drift_events"]:
                logger.warning(
                    "SCORE DRIFT [%s] %s: %s",
                    evt["severity"], evt["dimension"], evt["message"],
                )
        else:
            logger.info("Score calibration: no drift detected")
    except Exception:
        logger.exception("Score calibration check failed (non-fatal)")


def persist_ground_truth_failures(
    db: Any,
    run: Any,
    gt_failures: list,
    *,
    alert_title: str,
    alert_body: str,
    dedupe_key: str,
    condition: str,
) -> None:
    """Persist ``gt_failures`` on ``run.ground_truth_failures`` (committed) so
    they surface in the admin dashboard, and fire an ops alert if non-empty;
    a clean gate resolves the chamber's open one.

    The caller computes ``gt_failures`` (the two pipelines gather them
    differently) and supplies the fully-formatted alert text.
    """
    run.ground_truth_failures = json.dumps(gt_failures)
    db.commit()
    if gt_failures:
        send_ops_alert(alert_title, alert_body, dedupe_key=dedupe_key, condition=condition)
    else:
        resolve_ops_alert(condition)


def member_failure_condition(chamber: str, member_id: str) -> str:
    return f"member-failed-{chamber}-{member_id}"


def alert_member_failures(
    chamber: str, failures: list[tuple[str, BaseException]], succeeded: Iterable[str],
) -> None:
    """One ops alert per distinct member failure in a ``chamber`` run, and
    the open ones resolved for members this run scored.

    A member whose prepare or score step raises keeps the scorecard of the
    last run it passed, and until 2026-10-09 the count on the run record
    was the only trace: one senator failed every night from v6.31 on, and
    nobody saw it for a day. Each failure is its own condition, keyed by
    the member, deduped by the exception's type and message, so the same
    failure night after night alerts once, a different one alerts again,
    and a member who scores again closes theirs (a later recurrence then
    alerts anew). Never raises."""
    try:
        for member_id, exc in failures:
            detail = f"{type(exc).__name__}: {exc}"
            digest = hashlib.sha256(detail.encode()).hexdigest()[:16]
            send_ops_alert(
                f"{chamber.capitalize()} member failed: {member_id}",
                f"{member_id} failed in the {chamber} pipeline and keeps the scorecard "
                f"of the last run it passed.\n{detail}",
                dedupe_key=f"{member_failure_condition(chamber, member_id)}-{digest}",
                condition=member_failure_condition(chamber, member_id),
            )
        prefix = member_failure_condition(chamber, "")
        open_conditions = {
            a["condition"] for a in recent_alerts(limit=0)
            if a.get("open") and (a.get("condition") or "").startswith(prefix)
        }
        for member_id in succeeded:
            if (condition := member_failure_condition(chamber, member_id)) in open_conditions:
                resolve_ops_alert(condition)
    except Exception:
        logger.exception("Member-failure alert failed (non-fatal)")
