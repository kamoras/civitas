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

An alert about an ongoing condition names it (``condition``) and stays
open until the code that detects the condition sees it gone and calls
``resolve_ops_alert``: a watchdog on its next clean tick, a step when it
next succeeds. The dashboard shows open alerts as active and the rest as
history. An alert with no condition reports a one-off event and is
history from the start.
"""

import json
import logging
import threading
import uuid
from datetime import date, datetime, timedelta

import httpx

from app.config import settings
from app.database import SessionLocal
from app.models import (
    ApiCache,
    ElectionPipelineRun,
    HousePipelineRun,
    PipelineRun,
    PipelineStatus,
    StockTradesPipelineRun,
    SupplementaryPipelineRun,
)
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

_HISTORY_TIER = "_ops_alerts"
_HISTORY_KEEP = 50
# How far back the dashboard lists alerts that are no longer open. A week
# spans one cycle of the slowest regular jobs (the Sunday justice and
# committee refreshes), so every job's latest outcome stays in view.
HISTORY_SHOWN_FOR = timedelta(days=7)


def send_ops_alert(
    subject: str, body: str, *, dedupe_key: str | None = None, condition: str | None = None,
) -> bool:
    """Send an operator alert on every configured channel.

    ``dedupe_key``: if given, the alert fires at most once per key
    (tracked in the DB) — used e.g. so an overrunning pipeline alerts
    once, not every watchdog tick. ``condition``: the ongoing problem the
    alert reports, open until ``resolve_ops_alert(condition)``; a newer
    alert for it supersedes the older. Returns True if the alert fired.
    """
    try:
        # A read first, though _record's insert is what decides: a watchdog
        # re-raises a persisting condition every tick, and the read lets it
        # stop there without taking the database's write lock each time.
        if dedupe_key and (_sent_without_record(dedupe_key) or _already_sent(dedupe_key)):
            return False
        if not _record(subject, body, dedupe_key, condition):
            return False  # another process recorded (and sent) it first

        logger.error("OPS ALERT: %s — %s", subject, body)
        if settings.ALERT_NTFY_URL:
            _send_ntfy(subject, body)
        return True
    except Exception:
        logger.exception("Ops alert delivery failed (non-fatal)")
        return False


def recent_alerts(limit: int = 10) -> list[dict]:
    """Every open alert, then the newest ``limit`` others from the last
    HISTORY_SHOWN_FOR, each newest first — consumed by the admin API. An
    open alert is never pushed off, however old. Each is {subject, body, at,
    condition, resolvedAt, supersededAt, open}: open while its condition is
    unresolved and no newer alert has replaced it."""
    db = SessionLocal()
    try:
        rows = (
            db.query(ApiCache)
            .filter(ApiCache.tier == _HISTORY_TIER)
            .order_by(ApiCache.cached_at.desc())
            .all()
        )
        alerts = [
            {"condition": None, "resolvedAt": None, "supersededAt": None, **json.loads(r.data_json)}
            for r in rows
        ]
        for a in alerts:
            a["open"] = _is_open(a)
        since = (utcnow() - HISTORY_SHOWN_FOR).isoformat()
        history = [
            a for a in alerts
            if not a["open"] and (a["resolvedAt"] or a["supersededAt"] or a["at"]) >= since
        ]
        return [a for a in alerts if a["open"]] + history[:limit]
    except Exception:
        logger.exception("Failed to read ops alert history")
        return []
    finally:
        db.close()


def _already_sent(dedupe_key: str) -> bool:
    db = None
    try:
        db = SessionLocal()
        return (
            db.query(ApiCache.cache_key)
            .filter(
                ApiCache.tier == _HISTORY_TIER,
                ApiCache.cache_key == f"dedupe-{dedupe_key}",
            )
            .first()
            is not None
        )
    except Exception:
        # Only a shortcut (_record's insert decides): an unreadable database
        # must not stop an alert that may be about the database.
        logger.warning("Ops alert dedupe check failed — sending", exc_info=True)
        return False
    finally:
        if db is not None:
            db.close()


# Dedupe keys this process sent while the history couldn't be written, with
# the condition each reported: the database can't stop the next tick from
# sending again, so memory does — once per process rather than once per
# watchdog tick, until the condition is resolved. Kept until the process
# ends otherwise (some keys are per outage or permanent, so no age is
# right), capped so a long outage can't grow it without bound.
_sent_unrecorded: dict[str, str | None] = {}
_UNRECORDED_MAX = 1000
# Alerts are sent from many threads (the scheduler's, to_thread workers).
_unrecorded_lock = threading.Lock()


def _sent_without_record(dedupe_key: str) -> bool:
    with _unrecorded_lock:
        return dedupe_key in _sent_unrecorded


def _remember_unrecorded(dedupe_key: str, condition: str | None) -> None:
    with _unrecorded_lock:
        _sent_unrecorded[dedupe_key] = condition
        while len(_sent_unrecorded) > _UNRECORDED_MAX:
            del _sent_unrecorded[next(iter(_sent_unrecorded))]  # oldest first


def _forget_unrecorded(condition: str) -> None:
    with _unrecorded_lock:
        for key in [k for k, c in _sent_unrecorded.items() if c == condition]:
            del _sent_unrecorded[key]


def resolve_ops_alert(condition: str) -> int:
    """Close every open alert for ``condition``: the code that detects it
    found it gone. Frees their dedupe keys, so the condition alerts again
    if it comes back. Never raises. Returns how many were closed."""
    _forget_unrecorded(condition)
    db = None
    try:
        db = SessionLocal()
        closed = _close_open(db, condition, utcnow())
        db.commit()
        if closed:
            logger.info("Ops alert resolved: %s", condition)
        return closed
    except Exception:
        logger.exception("Failed to resolve ops alert %s", condition)
        return 0
    finally:
        if db is not None:
            db.close()


def _is_open(data: dict) -> bool:
    return bool(data.get("condition")) and not data.get("resolvedAt") and not data.get("supersededAt")


def _close_open(db, condition: str, now: datetime) -> int:
    """Resolve: the condition is gone. Closes the open alert and frees the
    dedupe key of every alert raised for it — the superseded ones' too,
    since a recurrence the same day may come back under any of them."""
    closed = 0
    for row in db.query(ApiCache).filter(ApiCache.tier == _HISTORY_TIER).all():
        data = json.loads(row.data_json)
        if data.get("condition") != condition:
            continue
        if _is_open(data):
            row.data_json = json.dumps({**data, "resolvedAt": now.isoformat()})
            closed += 1
        if row.cache_key.startswith("dedupe-"):
            row.cache_key = f"resolved-{row.cache_key[len('dedupe-'):]}-{now.isoformat()}"
    return closed


def _supersede_open(db, condition: str, now: datetime, keep: str | None = None) -> None:
    """A newer alert for a condition still open replaces the open one. It
    is not resolved — the problem never went away — and keeps its dedupe
    key: freeing it would re-send that alert whenever the condition's
    details swing back (a failing set of states A, then B, then A again),
    once per swing."""
    for row in db.query(ApiCache).filter(ApiCache.tier == _HISTORY_TIER).all():
        if row.cache_key == keep:
            continue  # the newer alert itself, already inserted
        data = json.loads(row.data_json)
        if data.get("condition") == condition and _is_open(data):
            row.data_json = json.dumps({**data, "supersededAt": now.isoformat()})


def _record(subject: str, body: str, dedupe_key: str | None, condition: str | None = None) -> bool:
    """Record the alert; False when its dedupe key was already recorded.
    The insert is the claim: with several processes (the API workers each
    run the liveness check) a read-then-write check let two both send.
    True as well when the history can't be written — an alert's delivery
    must not depend on the database it may be reporting on."""
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    now = utcnow()
    payload = json.dumps({
        "subject": subject,
        "body": body,
        "at": now.isoformat(),
        "condition": condition,
        "resolvedAt": None,
        "supersededAt": None,
    })
    db = None
    try:
        db = SessionLocal()
        # An event without a dedupe key is unique by its own id: a timestamp
        # alone collided when two alerts landed in the same microsecond.
        key = f"dedupe-{dedupe_key}" if dedupe_key else f"alert-{now.isoformat()}-{uuid.uuid4().hex[:12]}"
        inserted = db.execute(
            sqlite_insert(ApiCache)
            .values(tier=_HISTORY_TIER, cache_key=key, data_json=payload, cached_at=now)
            .on_conflict_do_nothing(index_elements=["tier", "cache_key"])
        ).rowcount
        if not inserted:
            db.rollback()
            return False  # its dedupe key: already recorded, and sent
        if condition:
            # Superseded by this one: the condition is still open, and one
            # alert for it says so.
            _supersede_open(db, condition, now, keep=key)
        # Prune old history so the table stays bounded — never an open
        # alert, which would silently drop a live problem off the panel.
        db.flush()  # the session doesn't autoflush; count this one in the prune
        cutoff_rows = (
            db.query(ApiCache)
            .filter(ApiCache.tier == _HISTORY_TIER)
            .order_by(ApiCache.cached_at.desc())
            .offset(_HISTORY_KEEP)
            .all()
        )
        for row in cutoff_rows:
            if not _is_open(json.loads(row.data_json)):
                db.delete(row)
        db.commit()
    except Exception:
        logger.exception("Failed to record ops alert")
        if dedupe_key:
            _remember_unrecorded(dedupe_key, condition)
    finally:
        if db is not None:
            db.close()
    return True


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
    if expected <= configured:
        resolve_ops_alert("stale-congress")
        return
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
        condition="stale-congress",
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
    if days_left > 30:
        resolve_ops_alert("feedback-token-expiring")  # rotated
    else:
        send_ops_alert(
            "FEEDBACK_TOKEN is expiring soon",
            f"The GitHub fine-grained PAT in FEEDBACK_TOKEN expires "
            f"{expiration} ({days_left} days from now). Once it expires, "
            f"site feedback submissions silently start failing (503) with "
            f"no other warning anywhere. Generate a new fine-grained PAT "
            f"scoped to Issues: write on {settings.GITHUB_FEEDBACK_REPO} "
            f"and update FEEDBACK_TOKEN before then.",
            dedupe_key=f"feedback-token-expiring-{expiration}",
            condition="feedback-token-expiring",
        )


def check_state_pvi_staleness() -> None:
    """Alert once a newer presidential election's data should be available
    for state_pvi.json's two-cycle window than what's currently baked in.

    Unlike CURRENT_CONGRESS or the committee-leadership/district-PVI data
    (app/pipeline/fetch/committee_leadership.py, district_pvi.py), this is
    NOT something a scheduled refetch can advance automatically:
    scripts/fetch_state_pvi.py deliberately pins its data sources to
    specific immutable GitHub-mirrored commits — "so a regeneration years
    from now fetches the exact same file" — so re-running it forever
    reproduces the identical 2020+2024 numbers. Advancing the window is a
    genuine one-time engineering task each presidential cycle (finding the
    new cycle's data source, verifying it passes the fidelity gates), the
    same class of unavoidable manual step as adding a new president to the
    historical term tables. This turns "nobody notices a new cycle is due"
    into a loud, deduped, dashboard-visible alert instead of silence —
    logged + DB-recorded regardless of whether ALERT_NTFY_URL is set.
    """
    import re

    from app.pipeline.analyze.score_calculator import _read_pvi_json

    window = _read_pvi_json("state_pvi.json").get("_window", "")
    years = [int(y) for y in re.findall(r"\d{4}", window)]
    if not years:
        return
    next_cycle = max(years) + 4
    # Presidential county-level canvass data is reliably compiled within a
    # few weeks of the election; mid-December of the election year is a
    # comfortable buffer before alerting.
    if date.today() < date(next_cycle, 12, 15):
        resolve_ops_alert("stale-state-pvi")  # regenerated for the new cycle
    else:
        send_ops_alert(
            "state_pvi.json window is stale",
            f"state_pvi.json is still windowed to {window}, but the "
            f"{next_cycle} presidential election has passed and its data "
            f"should be available now. Update scripts/fetch_state_pvi.py's "
            f"CYCLES/source URLs to the new cycle, verify the fidelity "
            f"gates pass, and regenerate the file. (district_pvi.json "
            f"needs no such update — it refreshes automatically from "
            f"Wikipedia's current Cook PVI figures.)",
            dedupe_key=f"stale-state-pvi-{next_cycle}",
            condition="stale-state-pvi",
        )


def stock_trades_overrun_budget() -> timedelta:
    """How long a stock-trades run may take before it counts as hung: 2h
    for the trade phases (normal runs finish in under 90 minutes), plus the
    time-boxed steps it also runs — the re-read of stored trades, and the
    annual-holdings phases, each capped at holdings_schedule.PHASE_CEILING —
    at the budgets they set themselves rather than restated here."""
    from app.holdings_schedule import HOLDINGS_STEPS, PHASE_CEILING, PTR_REREAD_BUDGET

    return timedelta(hours=2) + PTR_REREAD_BUDGET + len(HOLDINGS_STEPS) * PHASE_CEILING


def overrun_condition(label: str) -> str:
    """The open-alert condition for a pipeline running past its budget,
    shared with the action refresh's own overrun alerts (scheduler.py)."""
    return f"overrun-{label.lower().replace(' ', '-')}"


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
    tighter stock_trades_overrun_budget() — its weekly SCOTUS-refresh day includes an uncached
    Oyez crawl that took 5h+ in run 69).
    """
    from app.pipeline.run_tracker import live_run

    default_budget = timedelta(hours=settings.PIPELINE_OVERRUN_ALERT_HOURS)
    db = SessionLocal()
    try:
        # Runs that may be live (run_tracker.live_run), however old — a
        # budget can be set past the 12h age rule; a row its lease proves
        # dead is a dead run's, not an overrunning one.
        checks = [
            (label, live_run(db, model, timedelta.max), budget)
            for label, model, budget in (
                ("Senate", PipelineRun, default_budget),
                ("House", HousePipelineRun, default_budget),
                ("Supplementary", SupplementaryPipelineRun, default_budget),
                ("Stock trades", StockTradesPipelineRun, stock_trades_overrun_budget()),
            )
        ]
    finally:
        db.close()

    for label, run, budget in checks:
        condition = overrun_condition(label)
        age = utcnow() - run.started_at if run is not None else None
        if age is None or age <= budget:
            # Finished, or still inside its budget: an overrun alert from
            # this watchdog or the action refresh's (scheduler.py) is over.
            resolve_ops_alert(condition)
        else:
            hours = age.total_seconds() / 3600
            send_ops_alert(
                f"{label} pipeline overrunning",
                f"{label} pipeline run #{run.id} has been running for "
                f"{hours:.1f}h (budget {budget.total_seconds() / 3600:.0f}h). "
                f"Started {run.started_at.isoformat()}. Check the admin dashboard; "
                f"a run past 12h will be marked stale and the next attempt of "
                f"this pipeline may start concurrently.",
                dedupe_key=f"overrun-{label.lower().replace(' ', '-')}-{run.id}",
                condition=condition,
            )


# How long the scheduler's heartbeat (scheduler._record_next_run, every 5
# minutes) may go unwritten before the pipeline service is taken for down.
PIPELINE_SERVICE_SILENT_AFTER = timedelta(minutes=30)


# When this process first failed to read the heartbeat, in the current
# unbroken run of failures (None: the last read succeeded).
_heartbeat_unreadable_since: datetime | None = None


# When the API first found no heartbeat file at all: a record on the data
# volume, not this process's memory, so an API restarted by every deploy
# doesn't start the grace over and never page for a pipeline service that
# has not run once.
_HEARTBEAT_MISSING_RECORD = "pipeline_heartbeat_missing_since.json"


# This process's own first sighting, the fallback when the record can't be
# written or read: a volume that refuses the write must not also silence
# the alert for good.
_heartbeat_missing_noticed: datetime | None = None


def _heartbeat_missing_long_enough() -> bool:
    from app.shared_state import UNREADABLE, read_record, record_path, write_record

    global _heartbeat_missing_noticed
    now = utcnow()
    if _heartbeat_missing_noticed is None:
        _heartbeat_missing_noticed = now
    path = record_path(_HEARTBEAT_MISSING_RECORD)
    record = read_record(path)
    if isinstance(record, tuple):
        noticed = record[0]  # the volume's, shared and surviving restarts
    else:
        noticed = _heartbeat_missing_noticed
        if record is not UNREADABLE:  # absent: start the record (never reset one)
            try:
                write_record(path, {})
            except OSError:
                logger.warning("Couldn't record the missing pipeline heartbeat", exc_info=True)
    return now - noticed >= PIPELINE_SERVICE_SILENT_AFTER


def _forget_heartbeat_missing() -> None:
    import os

    from app.shared_state import record_path

    global _heartbeat_missing_noticed
    _heartbeat_missing_noticed = None
    try:
        os.unlink(record_path(_HEARTBEAT_MISSING_RECORD))
    except OSError:
        pass


def check_pipeline_service_alive() -> None:
    """Watchdog run by the read-only API process: alert when the pipeline
    service's scheduler has stopped writing its heartbeat.

    Every other pipeline watchdog here runs in the pipeline process's own
    scheduler, so none of them can report that process being gone — and
    since the two were split (settings.PROCESS_ROLE), the site stays up
    when it goes: a crash loop past Swarm's restart limit would stop every
    nightly run with no page and no alert. Once per outage (keyed by its last beat).
    """
    from app.scheduler import read_heartbeat
    from app.shared_state import UNREADABLE

    global _heartbeat_unreadable_since
    row = read_heartbeat()
    if row is UNREADABLE:
        # One unreadable round is a moment's I/O error, not evidence of
        # anything. A heartbeat file this process can't read for as long as
        # the pipeline is allowed to be silent is its own fault — and would
        # otherwise hide a dead pipeline service indefinitely.
        now = utcnow()
        if _heartbeat_unreadable_since is None:
            _heartbeat_unreadable_since = now
        logger.warning("Pipeline heartbeat unreadable (since %s)", f"{_heartbeat_unreadable_since:%H:%M} UTC")
        if now - _heartbeat_unreadable_since >= PIPELINE_SERVICE_SILENT_AFTER:
            send_ops_alert(
                "Pipeline heartbeat unreadable",
                f"The API process has not been able to read the pipeline service's heartbeat since "
                f"{_heartbeat_unreadable_since:%Y-%m-%d %H:%M} UTC, so it can't tell whether that "
                "service is running. Check the heartbeat file (scheduler_heartbeat.json on the data "
                "volume both services mount) and the backend's logs.",
                # Per day, not per spell: when a spell began is each
                # process's own observation (each API worker, each task
                # after a rollout), so a key built from it would page once
                # per process. The silent alert below can key per outage
                # because its key comes from the file itself.
                dedupe_key=f"pipeline-heartbeat-unreadable-{now:%Y-%m-%d}",
                condition="pipeline-heartbeat-unreadable",
            )
        return
    if _heartbeat_unreadable_since is not None:
        resolve_ops_alert("pipeline-heartbeat-unreadable")
    _heartbeat_unreadable_since = None
    last = row[0] if isinstance(row, tuple) else None
    if last is not None:
        _forget_heartbeat_missing()
        if last >= utcnow() - PIPELINE_SERVICE_SILENT_AFTER:
            resolve_ops_alert("pipeline-service-silent")
            return
    elif not _heartbeat_missing_long_enough():
        # No heartbeat file at all: the pipeline service may only be
        # starting (its first deploy, a fresh volume — pulling, migrating),
        # so it gets as long as a running one may be silent before paging.
        return
    since = f"since {last:%Y-%m-%d %H:%M} UTC" if last is not None else "ever"
    send_ops_alert(
        "Pipeline service is not running",
        f"The pipeline service's scheduler has not reported {since}. The site is still being "
        "served, but no scheduled job — the nightly chain, the hourly refreshes — will run until "
        "it is back. Check `docker service ps civitas_pipeline` and its logs.",
        # Once per outage — keyed by the last beat before it, which each
        # outage has its own of — not per day: a service that recovers and
        # stops again the same day alerts again.
        dedupe_key=f"pipeline-service-silent-{last:%Y-%m-%dT%H:%M:%S}" if last is not None
        else f"pipeline-service-silent-never-{utcnow():%Y-%m-%d}",
        condition="pipeline-service-silent",
    )


def check_pipeline_staleness() -> None:
    """Watchdog: alert when a nightly pipeline has not COMPLETED recently.

    This covers the gap every other alert in this module structurally
    cannot, and it is not hypothetical — it is why the 2026-09-01
    outage ran 19 nights before anyone noticed (see PR #562). In that
    incident the container was SIGKILLed by Swarm's healthcheck partway
    through Supplementary, and House, Stock trades and Election — which
    are chained behind it in scheduler.py — simply never started again:

    - ``_alert_if_skipped`` never fired: nothing was *skipped*, the
      chain just stopped existing partway down.
    - ``check_pipeline_overrun`` never fired: it reads rows whose status
      is RUNNING, and a pipeline that never started has no row at all
      (``if run is None: continue``).
    - the scheduler's ``except BaseException`` crash alert never fired:
      SIGKILL cannot be caught by a handler.

    Every one of those watches a run that EXISTS. This one watches for
    the absence of one, which is the only signal a silently-stopped
    chain actually emits. It also covers Election, which
    check_pipeline_overrun omits entirely.

    A pipeline with no runs at all is left alone: that is a fresh
    deployment, not a stall. One that has runs but has never completed
    successfully IS reported, since that is a real never-worked state.
    """
    budget = timedelta(days=settings.PIPELINE_STALE_ALERT_DAYS)
    models = [
        ("Senate", PipelineRun),
        ("House", HousePipelineRun),
        ("Supplementary", SupplementaryPipelineRun),
        ("Stock trades", StockTradesPipelineRun),
        ("Election", ElectionPipelineRun),
    ]

    db = SessionLocal()
    try:
        findings = []
        for label, model in models:
            last_done = (
                db.query(model.completed_at)
                .filter(model.status == PipelineStatus.COMPLETED,
                        model.completed_at.isnot(None))
                .order_by(model.completed_at.desc())
                .first()
            )
            if last_done is None:
                # Never completed. Only meaningful if it has ever tried —
                # an empty table is a fresh install, not a stalled one.
                if db.query(model.id).first() is not None:
                    findings.append((label, None))
                continue
            age = utcnow() - last_done[0]
            if age > budget:
                findings.append((label, age))
    finally:
        db.close()

    stale = {label for label, _ in findings}
    for label, _ in models:
        if label not in stale:
            resolve_ops_alert(f"stale-pipeline-{label.lower().replace(' ', '-')}")
    for label, age in findings:
        if age is None:
            detail = "has never completed successfully"
        else:
            detail = f"has not completed successfully in {age.total_seconds() / 86400:.1f} days"
        send_ops_alert(
            f"{label} pipeline is stale",
            f"The {label} pipeline {detail} (expected nightly, alert "
            f"threshold {budget.days}d). It is not overrunning — there is no "
            f"run to overrun — so this is the only signal it emits. Likely "
            f"causes: the nightly chain stopped partway (every pipeline after "
            f"the failure point silently never starts), or the container was "
            f"killed mid-run. Check the phase ABOVE this one in scheduler.py's "
            f"chain first: Senate -> Supplementary -> House -> Stock trades -> "
            f"Election.",
            # Per pipeline per day: a genuine multi-day stall should keep
            # reminding, but not once per watchdog tick.
            dedupe_key=f"stale-pipeline-{label.lower().replace(' ', '-')}-{utcnow():%Y-%m-%d}",
            condition=f"stale-pipeline-{label.lower().replace(' ', '-')}",
        )
