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
from typing import Any

from app.ops_alerts import resolve_ops_alert, send_ops_alert

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


# How much of a member-failure alert is spelled out: an outage can fail a
# whole chamber, and the alert has to stay readable on a phone.
_FAILURE_IDS_SHOWN = 10
_FAILURE_KINDS_SHOWN = 8
_FAILURE_MESSAGE_CHARS = 200


def member_failure_condition(chamber: str) -> str:
    return f"member-failed-{chamber}"


def alert_member_failures(
    chamber: str, failures: list[tuple[str, BaseException]], *, close_when_clean: bool = True,
) -> None:
    """One ops alert per ``chamber`` run naming every member whose prepare
    or score step raised, or the chamber's open one closed when none did.

    A failed member keeps the scorecard of the last run it passed, and
    until 2026-10-09 the count on the run record was the only trace: one
    senator failed every night from v6.31 on, and nobody saw it for a day.
    One alert per run, not per member, so an outage that fails the whole
    chamber is one alert; identical exceptions are grouped. Deduped on the
    set of (member, exception type): the same set the next night says
    nothing new, a changed set alerts again and supersedes the old one.
    ``close_when_clean=False`` for a run that scored only some members (a
    single-senator trigger): its having no failures says nothing about the
    others'. Never raises."""
    try:
        condition = member_failure_condition(chamber)
        if not failures:
            if close_when_clean:
                resolve_ops_alert(condition)
            return
        groups: dict[str, list[str]] = {}
        for member_id, exc in failures:
            detail = f"{type(exc).__name__}: {exc}"
            if len(detail) > _FAILURE_MESSAGE_CHARS:
                detail = detail[: _FAILURE_MESSAGE_CHARS - 1] + "…"
            groups.setdefault(detail, []).append(member_id)
        kinds = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        lines = []
        for detail, ids in kinds[:_FAILURE_KINDS_SHOWN]:
            shown = ", ".join(ids[:_FAILURE_IDS_SHOWN])
            more = f", and {len(ids) - _FAILURE_IDS_SHOWN} more" if len(ids) > _FAILURE_IDS_SHOWN else ""
            lines.append(f"- {detail} ×{len(ids)}: {shown}{more}")
        if len(kinds) > _FAILURE_KINDS_SHOWN:
            rest = kinds[_FAILURE_KINDS_SHOWN:]
            lines.append(f"- and {len(rest)} other errors ({sum(len(ids) for _, ids in rest)} members)")
        identity = sorted({(member_id, type(exc).__name__) for member_id, exc in failures})
        digest = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:16]
        n = len({member_id for member_id, _ in failures})
        send_ops_alert(
            f"{chamber.capitalize()} pipeline: {n} member{'s' if n != 1 else ''} failed",
            "These members keep the scorecard of the last run they passed:\n" + "\n".join(lines),
            dedupe_key=f"{condition}-{digest}",
            condition=condition,
        )
    except Exception:
        logger.exception("Member-failure alert failed (non-fatal)")
