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
from app.time_utils import utcnow

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

    now = utcnow()
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


def check_current_congress_staleness() -> None:
    """Alert if CURRENT_CONGRESS has fallen behind the calendar.

    CURRENT_CONGRESS defaults to a value computed from the wall clock
    (see app.config._default_current_congress), so under normal operation
    this should never fire — the round-4 audit's original "silent time
    bomb" finding was that the config default was a hardcoded literal
    nobody would remember to bump after a new Congress convened (Jan 3 of
    each odd year). This check remains as a defensive backstop for the one
    case that can still go stale: an operator explicitly pinning
    CURRENT_CONGRESS via env (for an archived-DB re-run's reproducibility)
    and then leaving that pin in place past the next Congress.
    """
    from app.pipeline.fetch.congress import expected_current_congress

    configured = settings.CURRENT_CONGRESS
    expected = expected_current_congress()
    if expected > configured:
        send_ops_alert(
            "CURRENT_CONGRESS is stale",
            f"CURRENT_CONGRESS is set to {configured}, but the {expected}th "
            f"Congress is now in session. The Senate pipeline pins its "
            f"roll-call window to CURRENT_CONGRESS while the House derives "
            f"its window from the calendar year, so they are now scoring "
            f"different Congresses and the Senate is scoring a dead one. "
            f"Bump CURRENT_CONGRESS to {expected} (env or config) and re-run "
            f"the pipeline.",
            dedupe_key=f"stale-congress-{expected}",
        )


def check_feedback_token_expiration() -> None:
    """Alert when FEEDBACK_TOKEN is within 30 days of expiring.

    FEEDBACK_TOKEN must be a fine-grained GitHub PAT (see config.py's own
    comment on why — scoped narrowly to Issues: write, not a broad classic
    token), and fine-grained PATs always expire (commonly capped at 1 year
    by GitHub) — there is no way to issue one that doesn't. When it
    expires, /api/feedback starts returning a 503 to every user with
    nothing telling an absent operator to renew it. GitHub returns the
    exact expiration in a response header on every authenticated request
    (github-authentication-token-expiration), so this makes an otherwise
    silent failure into a loud, dashboard-visible alert with a month's
    lead time to act. Runs at most weekly (Sunday) — the value only
    changes when the token is rotated, so nightly checks would just be an
    unnecessary GitHub API call.
    """
    if not settings.FEEDBACK_TOKEN or utcnow().weekday() != 6:
        return
    try:
        resp = httpx.get(
            "https://api.github.com/rate_limit",  # free — doesn't count against the rate limit
            headers={
                "Authorization": f"Bearer {settings.FEEDBACK_TOKEN}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10.0,
        )
    except Exception:
        logger.warning("FEEDBACK_TOKEN expiration check failed to reach GitHub", exc_info=True)
        return
    expiration = resp.headers.get("github-authentication-token-expiration")
    if not expiration:
        return  # classic PAT (no expiration) or header not present
    try:
        expires_at = datetime.strptime(expiration.split(" UTC")[0], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return
    days_left = (expires_at - utcnow()).days
    if days_left <= 30:
        send_ops_alert(
            "FEEDBACK_TOKEN is expiring soon",
            f"The GitHub fine-grained PAT in FEEDBACK_TOKEN expires "
            f"{expiration} ({days_left} days from now). Once it expires, "
            f"site feedback submissions silently start failing (503) with "
            f"no other warning anywhere. Generate a new fine-grained PAT "
            f"scoped to Issues: write on {settings.GITHUB_FEEDBACK_REPO} "
            f"and update FEEDBACK_TOKEN before then.",
            dedupe_key=f"feedback-token-expiring-{expiration}",
        )


def check_pipeline_overrun() -> None:
    """Watchdog: alert once per run when a pipeline exceeds the budget.

    Called periodically by the scheduler. Covers all four nightly
    pipelines (Senate, House, Supplementary, Stock trades) — until
    2026-07-23 this only covered Senate and House, so a wedged
    Supplementary or Stock run generated zero automatic alert, unlike
    the other two. Confirmed live: this contributed to stock-trades data
    going stale for 4+ days and supplementary data for 1+ day with
    nothing telling an operator to look. Per-pipeline budgets mirror the
    ones scheduler.py's _hourly_action_refresh already uses for the same
    four checks (Supplementary gets Senate/House's 8h, not Stock's
    tighter 2h — its weekly SCOTUS-refresh day includes an uncached
    Oyez crawl that took 5h+ in run 69).
    """
    from app.database import SessionLocal
    from app.models import (
        HousePipelineRun, PipelineRun, PipelineStatus,
        StockTradesPipelineRun, SupplementaryPipelineRun,
    )

    default_budget = timedelta(hours=settings.PIPELINE_OVERRUN_ALERT_HOURS)
    db = SessionLocal()
    try:
        checks = [
            ("Senate", db.query(PipelineRun).filter(PipelineRun.status == PipelineStatus.RUNNING).first(), default_budget),
            ("House", db.query(HousePipelineRun).filter(HousePipelineRun.status == PipelineStatus.RUNNING).first(), default_budget),
            ("Supplementary", db.query(SupplementaryPipelineRun).filter(SupplementaryPipelineRun.status == PipelineStatus.RUNNING).first(), default_budget),
            ("Stock trades", db.query(StockTradesPipelineRun).filter(StockTradesPipelineRun.status == PipelineStatus.RUNNING).first(), timedelta(hours=2)),
        ]
    finally:
        db.close()

    for label, run, budget in checks:
        if run is None:
            continue
        age = utcnow() - run.started_at
        if age > budget:
            hours = age.total_seconds() / 3600
            send_ops_alert(
                f"{label} pipeline overrunning",
                f"{label} pipeline run #{run.id} has been running for "
                f"{hours:.1f}h (budget {budget.total_seconds() / 3600:.0f}h). "
                f"Started {run.started_at.isoformat()}. Check the admin dashboard; "
                f"a run past 12h will be marked stale and the next attempt of "
                f"this pipeline may start concurrently.",
                dedupe_key=f"overrun-{label.lower().replace(' ', '-')}-{run.id}",
            )
