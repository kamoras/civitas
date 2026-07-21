"""Post-run quality gates shared by the Senate and House pipelines.

Both orchestrators end with the same two non-fatal checks: a score-calibration
drift report, and a ground-truth / score-distribution gate that persists any
failures on the run record and fires an ops alert. These were copy-pasted
(differing only in the "senator"/"representative" label and the alert text).
This is the shared implementation.

The Senate additionally runs named-reference ground-truth cases
(``check_ground_truth``) that the House has no reference set for; that step
stays in ``run_senate_pipeline``, which gathers its ``gt_failures`` and then
calls ``persist_ground_truth_failures`` here just like the House does.
"""

import json
import logging
from typing import Any

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
) -> None:
    """Persist ``gt_failures`` on ``run.ground_truth_failures`` (committed) so
    they surface in the admin dashboard, and fire an ops alert if non-empty.

    The caller computes ``gt_failures`` (the two pipelines gather them
    differently) and supplies the fully-formatted alert text.
    """
    run.ground_truth_failures = json.dumps(gt_failures)
    db.commit()
    if gt_failures:
        from app.ops_alerts import send_ops_alert
        send_ops_alert(alert_title, alert_body, dedupe_key=dedupe_key)
