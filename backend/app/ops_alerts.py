"""Operator alerts for pipeline health.

Run 69 (2026-07) took 24 hours, caused the next nightly to be skipped,
and failed a ground-truth score gate — and nothing notified anyone.
This module is the single place pipeline code reports operational
problems. Delivery is best-effort across every configured channel:

- always logged at ERROR (visible in ``docker logs``)
- always recorded in ApiCache (tier ``_ops_alerts``) so the admin
  dashboard can show recent alerts
- pushed via ntfy if ``ALERT_NTFY_URL`` is set

Alerts never raise: a broken alert channel must not take down the
pipeline it is reporting on.
"""

import json
import logging
from datetime import datetime, timedelta

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_HISTORY_TIER = "_ops_alerts"
_HISTORY_KEEP = 50


def send_ops_alert(subject: str, body: str, *, dedupe_key: str | None = None) -> bool:
    """Send an operator alert on every configured channel.

    ``dedupe_key``: if given, the alert fires at most once per key
    (tracked in the DB) — used e.g. so an overrunning pipeline alerts
    once, not every watchdog tick. Returns True if the alert fired.
    """
    try:
        if dedupe_key and _already_sent(dedupe_key):
            return False

        logger.error("OPS ALERT: %s — %s", subject, body)
        _record(subject, body, dedupe_key)

        if settings.ALERT_NTFY_URL:
            _send_ntfy(subject, body)
        return True
    except Exception:
        logger.exception("Ops alert delivery failed (non-fatal)")
        return False


def recent_alerts(limit: int = 10) -> list[dict]:
    """Most recent alerts, newest first — consumed by the admin API."""
    from app.database import SessionLocal
    from app.models import ApiCache

    db = SessionLocal()
    try:
        rows = (
            db.query(ApiCache)
            .filter(ApiCache.tier == _HISTORY_TIER)
            .order_by(ApiCache.cached_at.desc())
            .limit(limit)
            .all()
        )
        return [json.loads(r.data_json) for r in rows]
    except Exception:
        logger.exception("Failed to read ops alert history")
        return []
    finally:
        db.close()


def _already_sent(dedupe_key: str) -> bool:
    from app.database import SessionLocal
    from app.models import ApiCache

    db = SessionLocal()
    try:
        return (
            db.query(ApiCache.cache_key)
            .filter(
                ApiCache.tier == _HISTORY_TIER,
                ApiCache.cache_key == f"dedupe-{dedupe_key}",
            )
            .first()
            is not None
        )
    finally:
        db.close()


def _record(subject: str, body: str, dedupe_key: str | None) -> None:
    from app.database import SessionLocal
    from app.models import ApiCache

    now = datetime.utcnow()
    payload = json.dumps({
        "subject": subject,
        "body": body,
        "at": now.isoformat(),
    })
    db = SessionLocal()
    try:
        key = f"dedupe-{dedupe_key}" if dedupe_key else f"alert-{now.isoformat()}"
        db.add(ApiCache(tier=_HISTORY_TIER, cache_key=key, data_json=payload, cached_at=now))
        # Prune old history so the table stays bounded.
        cutoff_rows = (
            db.query(ApiCache)
            .filter(ApiCache.tier == _HISTORY_TIER)
            .order_by(ApiCache.cached_at.desc())
            .offset(_HISTORY_KEEP)
            .all()
        )
        for row in cutoff_rows:
            db.delete(row)
        db.commit()
    except Exception:
        logger.exception("Failed to record ops alert")
    finally:
        db.close()


def _send_ntfy(subject: str, body: str) -> None:
    try:
        httpx.post(
            settings.ALERT_NTFY_URL,
            content=body.encode(),
            headers={"Title": f"Civitas: {subject}", "Priority": "high"},
            timeout=10.0,
        )
    except Exception:
        logger.exception("ntfy alert failed")


def check_pipeline_overrun() -> None:
    """Watchdog: alert once per run when a pipeline exceeds the budget.

    Called periodically by the scheduler. Covers both the Senate and
    House pipelines.
    """
    from app.database import SessionLocal
    from app.models import HousePipelineRun, PipelineRun

    budget = timedelta(hours=settings.PIPELINE_OVERRUN_ALERT_HOURS)
    db = SessionLocal()
    try:
        checks = [
            ("Senate", db.query(PipelineRun).filter(PipelineRun.status == "running").first()),
            ("House", db.query(HousePipelineRun).filter(HousePipelineRun.status == "running").first()),
        ]
    finally:
        db.close()

    for label, run in checks:
        if run is None:
            continue
        age = datetime.utcnow() - run.started_at
        if age > budget:
            hours = age.total_seconds() / 3600
            send_ops_alert(
                f"{label} pipeline overrunning",
                f"{label} pipeline run #{run.id} has been running for "
                f"{hours:.1f}h (budget {settings.PIPELINE_OVERRUN_ALERT_HOURS:.0f}h). "
                f"Started {run.started_at.isoformat()}. Check the admin dashboard; "
                f"a run past 12h will be marked stale and the next nightly may "
                f"start concurrently.",
                dedupe_key=f"overrun-{label.lower()}-{run.id}",
            )
