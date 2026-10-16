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
from datetime import date, datetime, timedelta, timezone

import httpx

from app.config import settings
from app.database import SessionLocal
from app.election_calendar import federal_states
from app.election_phase import active_election
from app.models import (
    ApiCache,
    ElectionPipelineRun,
    HousePipelineRun,
    PipelineRun,
    PipelineStatus,
    President,
    StockTradesPipelineRun,
    SupplementaryPipelineRun,
)
from app.pipeline.fetch import ballot_pdf_sources
from app.time_utils import utcnow
from app.voter_calendar import calendar_election_date

logger = logging.getLogger(__name__)

_HISTORY_TIER = "_ops_alerts"
_HISTORY_KEEP = 50
# Superseded alerts kept past the history window per condition still open:
# their dedupe keys are what stop a swing back re-sending them.
_SUPERSEDED_KEPT = 10
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
    if it comes back. Never raises. Returns how many were closed, or -1
    when the history couldn't be written (a caller that resolves only on a
    transition tries again)."""
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
        return -1
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
        # alert, which would silently drop a live problem off the panel,
        # nor one superseded under a condition still open: its dedupe key
        # is what keeps it from being sent again (_supersede_open).
        db.flush()  # the session doesn't autoflush; count this one in the prune
        history = (
            db.query(ApiCache)
            .filter(ApiCache.tier == _HISTORY_TIER)
            .order_by(ApiCache.cached_at.desc())
            .all()
        )
        decoded = [(row, json.loads(row.data_json)) for row in history]
        still_open = {data.get("condition") for _, data in decoded if data.get("condition") and _is_open(data)}
        # Kept past the window: at most _SUPERSEDED_KEPT per open condition,
        # newest first — a condition open for weeks under a dated key would
        # otherwise keep a row a day for good, and a shared allowance would
        # let one noisy condition crowd out another's keys; the newest are
        # the ones a swing back is likeliest to reuse.
        kept: dict[str, int] = {}
        for row, data in decoded[_HISTORY_KEEP:]:
            if _is_open(data):
                continue
            condition = data.get("condition")
            if (row.cache_key.startswith("dedupe-") and condition in still_open
                    and kept.get(condition, 0) < _SUPERSEDED_KEPT):
                kept[condition] = kept.get(condition, 0) + 1
                continue
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
    """Alert if CURRENT_CONGRESS has fallen behind the Congress in office.

    Unpinned, it can't: the pipeline process advances it to the Congress in
    office (noon ET on Jan 3 of an odd year, the 20th Amendment's
    hand-over) at the start of every job (app.config.scoring_congress), and
    this check advances it first too, so a process that was already running
    when a new Congress convened scores the new one from its next job, with
    no restart. What can go stale is an operator's pin: CURRENT_CONGRESS
    set in the environment (an archived-DB re-run's reproducibility) and
    left in place past the next Congress — or copied from an old
    .env.example that set it. Only editing the environment fixes that. The
    pin holds both chambers' scored windows and House members' district
    lines (fetch/district_pvi reads the same value), and the alert says so.

    The round-4 audit's original "silent time bomb" finding was that the
    default was a hardcoded literal nobody would remember to bump.
    """
    from app.config import advance_current_congress
    from app.ordinals import ordinal
    from app.pipeline.fetch.congress import expected_current_congress

    from app.config import settings as current

    configured = advance_current_congress()
    expected = expected_current_congress()
    # Unpinned, advance_current_congress just brought it up to the clock;
    # it can only read as behind across the instant of the hand-over
    # between the two reads, and the next call advances it.
    if expected <= configured or not current.current_congress_pinned:
        resolve_ops_alert("stale-congress")
        return
    send_ops_alert(
        "CURRENT_CONGRESS is stale",
        f"CURRENT_CONGRESS is pinned in the environment (.env or the container's environment) "
        f"to {configured}, but the {ordinal(expected)} Congress is now in session. Both chambers' "
        f"scored windows (roll-call sessions, bills, Voteview ideal points) and House members' "
        f"district lines stay on the {ordinal(configured)} Congress while the pin is in place "
        f"(fetch/district_pvi reads the same setting), so the {ordinal(expected)} Congress's "
        f"members are scored on a dead Congress's record and on districts they were not elected "
        f"in. Remove the pin so it follows the clock — production should never set it; a pin is "
        f"only for re-running an archived database — and restart the backend.",
        dedupe_key=f"stale-congress-{expected}",
        condition="stale-congress",
    )


# How far ahead of an election each piece of cycle-specific data has to be
# in place. The maps must be settled by the year's filing deadlines; voter
# deadlines start with UOCAVA ballots 45 days out and registration about a
# month out, and a calendar must be on the page well before either; poll
# closing times only matter on the night, a town's sample ballot once it is
# printed (about two months out).
READINESS_LEAD = {
    "maps": None,  # from January 1 of the election year
    "calendar": timedelta(days=150),
    "poll_close": timedelta(days=45),
    "town_ballots": timedelta(days=60),
}


def election_readiness_gaps(election_day: date) -> dict[str, str]:
    """{condition: what is missing} for the cycle-specific data the coming
    election needs and doesn't have yet, each only once its lead time has
    begun (READINESS_LEAD). Nothing here is typed per cycle: each check
    compares a data file with the election the calendar says is next."""
    # Imported here, not at the top: district_lean (through
    # live_results.sync), district_pvi and poll_close (through the data
    # refresh it reads) import this module.
    from app.district_lean import lines_table
    from app.live_results.sync import _maps
    from app.pipeline.fetch import poll_close
    from app.pipeline.fetch.district_pvi import congress_for_election

    today = utcnow().date()
    year = election_day.year
    gaps: dict[str, str] = {}
    if today >= date(year, 1, 1):
        if str(year) not in _maps():
            gaps["election-ready-maps"] = (
                f"app/data/redrawn_congressional_maps.json has no entry for {year}. Each cycle's maps are "
                f"confirmed there (an empty entry when no state redrew; a state whose new map is in force, "
                f"or a court-held one, with its source). Until then the {year} House pages carry no "
                f"computed lean, and a state that redrew would be read on its old lines."
            )
        else:
            congress = congress_for_election(year)
            missing = sorted(st for st in federal_states() if not lines_table(st, congress))
            if missing:
                gaps["election-ready-district-lean"] = (
                    f"No computed district lean for the {congress}th Congress's lines in {', '.join(missing)} "
                    f"(they redrew, or app/data/district_lean.json has no table to carry forward). Rebuild it "
                    f"with backend/scripts/build_district_lean.py for those maps."
                )
    if today >= election_day - READINESS_LEAD["calendar"] and calendar_election_date() != election_day.isoformat():
        gaps["election-ready-calendar"] = (
            f"app/data/election_calendar.json is for {calendar_election_date() or 'no election'}, not "
            f"{election_day}: every state page and the homepage strip show no voter deadlines until it is "
            f"rebuilt for {election_day} (backend/scripts/build_election_calendar.py)."
        )
    if today >= election_day - READINESS_LEAD["poll_close"] and poll_close.file_year() != year:
        gaps["election-ready-poll-close"] = (
            f"app/data/poll_close_times.json is for {poll_close.file_year() or 'no stated year'}, not {year}: "
            f"election night would hold each state's count to the last cycle's closing times "
            f"(backend/scripts/fetch_poll_close_times.py {year})."
        )
    if today >= election_day - READINESS_LEAD["town_ballots"]:
        stale = sorted(
            town for town, entry in ballot_pdf_sources.all_towns().items()
            if (entry.get("election_date") or "") < (election_day - timedelta(days=366)).isoformat()
        )
        if stale:
            gaps["election-ready-town-ballots"] = (
                f"The sample ballots on file for {', '.join(stale)} are from an earlier cycle; those towns show "
                f"as not covered until app/data/ballot_pdf_sources.json names the {year} ballot."
            )
    return gaps


READINESS_CONDITIONS = (
    "election-ready-maps", "election-ready-district-lean", "election-ready-calendar",
    "election-ready-poll-close", "election-ready-town-ballots",
)


def check_election_readiness() -> None:
    """One alert per piece of cycle-specific data the coming election is
    missing (election_readiness_gaps), each resolved once it is in place,
    so a new cycle's needs are flagged ahead of time rather than found on
    the page. Only in the campaign phase: once the election is held the
    data that mattered was used, and the next cycle's checks begin when the
    site moves on to it."""
    election = active_election()
    gaps = election_readiness_gaps(election.election_day) if election.phase == "campaign" else {}
    for condition in READINESS_CONDITIONS:
        if condition in gaps:
            send_ops_alert(
                "Coming election's data not ready",
                gaps[condition],
                dedupe_key=f"{condition}-{election.election_day}",
                condition=condition,
            )
        else:
            resolve_ops_alert(condition)


# The Federal Register's presidential documents (proclamations, executive
# orders, memoranda) each name the president who signed them: the official
# record of who holds the office, published within days of a signing.
PRESIDENTIAL_DOCUMENTS_URL = "https://www.federalregister.gov/api/v1/documents.json"


def _person_key(name: str) -> tuple[str, str] | None:
    """(first, last) of a name with initials, suffixes and punctuation
    dropped, so the Federal Register's "First Last" and the roster's "First
    M. Last, Jr." compare equal; None for an empty name."""
    words = [w for w in "".join(c if c.isalpha() or c == " " else "" for c in name.lower()).split()
             if len(w) > 1 and w not in {"jr", "sr", "ii", "iii", "iv"}]
    return (words[0], words[-1]) if words else None


def newest_presidential_signer(timeout: float = 15.0) -> tuple[str, str] | None:
    """(president's name, signing date) of the newest presidential document
    in the Federal Register, or None when it can't be read."""
    try:
        resp = httpx.get(PRESIDENTIAL_DOCUMENTS_URL, params={
            "conditions[type][]": "PRESDOCU", "order": "newest", "per_page": 20,
            "fields[]": ["president", "signing_date"],
        }, timeout=timeout)
        resp.raise_for_status()
        rows = [r for r in resp.json().get("results") or []
                if (r.get("president") or {}).get("name") and r.get("signing_date")]
    except Exception:
        logger.warning("Federal Register presidential documents could not be read", exc_info=True)
        return None
    if not rows:
        return None
    newest = max(rows, key=lambda r: r["signing_date"])
    return newest["president"]["name"], newest["signing_date"]


def check_sitting_president() -> None:
    """Alert when the presidents table's sitting president is not the one
    signing presidential documents.

    The sitting president comes from UCSB's roster each night
    (presidential_roster.py), never a typed name. A roster that is slow to
    add a new president after an inauguration would keep the outgoing one
    "sitting" with nothing to say so. The Federal Register is the official
    cross-check: its newest presidential document, signed after the stored
    president's term began, by someone else, means the roster has fallen
    behind. A register still showing the outgoing president's last
    documents (it publishes days after signing) is not a disagreement."""
    signer = newest_presidential_signer()
    if signer is None:
        return
    name, signed = signer
    db = SessionLocal()
    try:
        sitting = db.query(President).filter(President.is_current.is_(True)).all()
    finally:
        db.close()
    if len(sitting) == 1 and _person_key(sitting[0].name) == _person_key(name):
        resolve_ops_alert("sitting-president-mismatch")
        return
    if len(sitting) == 1 and signed < (sitting[0].term_start or ""):
        return  # the register is behind the roster, not the other way round
    stored = ", ".join(p.name for p in sitting) or "nobody"
    send_ops_alert(
        "Sitting president differs from the Federal Register",
        f"The Federal Register's newest presidential document, signed {signed}, is {name}'s, but the "
        f"presidents table marks {stored} as sitting. The roster (UCSB's presidents list, read nightly by "
        f"presidential_roster.py) has not caught up: the site names the wrong sitting president until it "
        f"does. Check https://www.presidency.ucsb.edu/presidents; nothing needs editing in code.",
        dedupe_key=f"sitting-president-mismatch-{signed}-{_person_key(name)}",
        condition="sitting-president-mismatch",
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

    Unlike CURRENT_CONGRESS or the committee-leadership data
    (app/pipeline/fetch/committee_leadership.py), this is NOT something a
    scheduled refetch can advance automatically (district PVI is pinned
    too, per Congress, in app/data/district_pvi_sources.json — see
    fetch/district_pvi.py):
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
            f"is separate: its sources are pinned per Congress in "
            f"app/data/district_pvi_sources.json.)",
            dedupe_key=f"stale-state-pvi-{next_cycle}",
            condition="stale-state-pvi",
        )


def stock_trades_overrun_budget() -> timedelta:
    """How long a stock-trades run may take before it counts as hung: 2h
    for the trade phases (normal runs finish in under 90 minutes), plus the
    time-boxed steps it also runs — the re-read of stored trades, the
    annual-holdings phases, each capped at holdings_schedule.PHASE_CEILING,
    and the member-photo choice — at the budgets they set themselves rather
    than restated here."""
    from app.holdings_schedule import HOLDINGS_STEPS, MEMBER_PHOTOS_BUDGET, PHASE_CEILING, PTR_REREAD_BUDGET

    return timedelta(hours=2) + PTR_REREAD_BUDGET + len(HOLDINGS_STEPS) * PHASE_CEILING + MEMBER_PHOTOS_BUDGET


def overrun_condition(label: str) -> str:
    """The open-alert condition for a pipeline running past its budget."""
    return f"overrun-{label.lower().replace(' ', '-')}"


def check_pipeline_overrun() -> None:
    """Watchdog: alert once per run when a pipeline exceeds the budget.

    Called periodically by the scheduler. Covers all four nightly
    pipelines (Senate, House, Supplementary, Stock trades) — until
    2026-07-23 this only covered Senate and House, so a wedged
    Supplementary or Stock run generated zero automatic alert, unlike
    the other two. Confirmed live: this contributed to stock-trades data
    going stale for 4+ days and supplementary data for 1+ day with
    nothing telling an operator to look. Supplementary gets Senate/House's
    budget, not Stock's tighter stock_trades_overrun_budget() — its weekly
    SCOTUS-refresh day includes an uncached Oyez crawl that took 5h+ in
    run 69. The only overrun alert: the hourly Action Center refresh,
    which raised its own until it stopped standing down for the chain
    (2026-10).
    """
    from app.pipeline.run_tracker import QUIET_PIPELINE_TIMEOUT, STALE_PIPELINE_TIMEOUT, live_run

    default_budget = timedelta(hours=settings.PIPELINE_OVERRUN_ALERT_HOURS)
    db = SessionLocal()
    try:
        # Runs that may be live (run_tracker.live_run), however old or
        # quiet — a budget can be set past the age ceiling, and a run that
        # has gone quiet is the one this alert most needs to name; a row
        # its lease proves dead is a dead run's, not an overrunning one.
        checks = [
            (label, live_run(db, model, timedelta.max, timedelta.max), budget)
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
            # this watchdog is over.
            resolve_ops_alert(condition)
        else:
            hours = age.total_seconds() / 3600
            send_ops_alert(
                f"{label} pipeline overrunning",
                f"{label} pipeline run #{run.id} has been running for "
                f"{hours:.1f}h (budget {budget.total_seconds() / 3600:.0f}h). "
                f"Started {run.started_at.isoformat()}. {_active_step_line(run)}"
                f"A run with no progress for {QUIET_PIPELINE_TIMEOUT.total_seconds() / 3600:.0f}h, or past "
                f"{STALE_PIPELINE_TIMEOUT.total_seconds() / 3600:.0f}h, is marked stale and the next attempt "
                f"of this pipeline may start beside it.",
                dedupe_key=f"overrun-{label.lower().replace(' ', '-')}-{run.id}",
                condition=condition,
            )


def _active_step_line(run) -> str:
    """Where an overrunning run is, from its progress row, so the alert
    tells a slow step that is working (a cache warm-up after a change to
    what is cached: tens of thousands of rate-limited reads, counted in the
    step's detail) from one that has stopped. Its elapsed time advances
    with each progress write, so the time since the last one is how long
    the step has gone quiet."""
    try:
        steps = json.loads(run.progress_detail or "[]")
    except (TypeError, ValueError):
        steps = []
    step = next((s for s in steps if isinstance(s, dict) and s.get("status") == "active"), None)
    if step is None:
        return "No step is recorded as active; check the admin dashboard. "
    where = f"In step '{step.get('key')}' ({step.get('label', '')})"
    if step.get("total") is not None:
        where += f", {step.get('done', 0)} of {step['total']}"
    if step.get("detail"):
        where += f": {step['detail']}"
    if run.elapsed_seconds is not None:
        quiet_min = ((utcnow() - run.started_at).total_seconds() - run.elapsed_seconds) / 60
        where += f". Last progress written {quiet_min:.0f} min ago"
        if quiet_min >= 30:
            where += " (no progress since: check the pipeline service log for a hang)"
    return where + ". "


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


# Whether a "pipeline service silent" / "heartbeat unreadable" alert may be
# open: true at start (one may be from before this process), then after this
# process sends one, until a resolve of it writes.
_silent_alert_may_be_open = True
_unreadable_alert_may_be_open = True


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

    global _heartbeat_unreadable_since, _silent_alert_may_be_open, _unreadable_alert_may_be_open
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
            _unreadable_alert_may_be_open = True
        return
    _heartbeat_unreadable_since = None
    if _unreadable_alert_may_be_open:
        # As for the silent alert below: once on the way back (and once per
        # process, for one opened before it), kept owed until it writes.
        _unreadable_alert_may_be_open = resolve_ops_alert("pipeline-heartbeat-unreadable") < 0
    last = row[0] if isinstance(row, tuple) else None
    if last is not None:
        _forget_heartbeat_missing()
        if last >= utcnow() - PIPELINE_SERVICE_SILENT_AFTER:
            if _silent_alert_may_be_open:
                # Only on the way back: resolving reads the whole alert
                # history, every tick, in every worker, otherwise. Kept
                # owed when the resolve couldn't write: the next tick tries.
                _silent_alert_may_be_open = resolve_ops_alert("pipeline-service-silent") < 0
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
    _silent_alert_may_be_open = True


def check_pipeline_staleness() -> None:
    """Watchdog: alert when a nightly pipeline has not COMPLETED recently.

    This covers the gap every other alert in this module structurally
    cannot, and it is not hypothetical — it is why the 2026-09-01
    outage ran 19 nights before anyone noticed (see PR #562). In that
    incident the container was SIGKILLed by Swarm's healthcheck partway
    through Supplementary, and House, Stock trades and Election, which
    ran after it in the nightly chain, never started that night:

    - the per-link skip and crash alerts (scheduler._nightly_pipeline)
      never fired: nothing was *skipped* or raised, the process was gone.
    - ``check_pipeline_overrun`` never fired: it reads rows whose status
      is RUNNING, and a pipeline that never started has no row at all
      (``if run is None: continue``).

    Since 2026-09 a link's own skip, failure or crash no longer stops the
    ones after it (app.pipeline_chain), but a killed process still ends
    that night's chain, and a pipeline can fail every night. Every other
    alert watches a run that EXISTS; this one watches for the absence of
    a good one. It also covers Election, which check_pipeline_overrun
    omits entirely.

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
            f"causes: its own nightly run keeps being skipped, failing or "
            f"crashing (see its nightly-skipped / nightly-crashed alerts), the "
            f"nightly job itself isn't running, or the container was killed "
            f"mid-run.",
            # Per pipeline per day: a genuine multi-day stall should keep
            # reminding, but not once per watchdog tick.
            dedupe_key=f"stale-pipeline-{label.lower().replace(' ', '-')}-{utcnow():%Y-%m-%d}",
            condition=f"stale-pipeline-{label.lower().replace(' ', '-')}",
        )


# --- Source freshness -------------------------------------------------------
#
# A source that keeps answering with nothing new is the quiet version of a
# dead one: the feeds' fetch loop logged "Fetched 0 articles from AP News" at
# INFO for a month (2026-09-09 to 2026-10-10) while its mirror served the same
# stale items, and nothing else noticed. Each source's state is one ApiCache
# row: the publication times it has shown (pruned to SOURCE_FRESHNESS_HISTORY),
# when it was first checked, and whether a staleness alert is open.
SOURCE_FRESHNESS_TIER = "_source_freshness"
SOURCE_FRESHNESS_HISTORY = timedelta(days=60)
# Holds the history window for a feed publishing every couple of hours (the
# state outlets' median gap, measured 2026-10-10) and keeps each row to a few
# tens of kilobytes.
_SOURCE_FRESHNESS_KEEP = 500
# A source is stale once its silence runs a day past the longest gap it has
# shown between items in the history window — its own measured rhythm,
# weekends and recesses included — and never before the caller's `floor`,
# the point past which the silence costs something.
#
# A day, not a multiple: measured 2026-10-13 on 130 days of Roll Call's own
# publication record (its WordPress API), its ordinary weekend is 64-72 hours
# and a holiday weekend 82-88 (July 4, Labor Day). Twice the longest gap set a
# 142-177 hour bar — a dead feed unnoticed for over a week — where the longest
# gap plus a day sets 95-112 hours; neither raised a false alert on that
# record, nor on VTDigger's, Civil Beat's or The Hill's.
SOURCE_FRESHNESS_SLACK = timedelta(hours=24)
# How much history a source's own longest gap needs before it is trusted:
# a week always holds a weekend. Until then the bar comes from the other
# sources of the same kind that have that much history (their longest gap,
# plus the slack) — a feed first checked on a Thursday has never shown a
# weekend, and its weekday rhythm alone alerted on Roll Call's first quiet
# weekend (2026-10-11, a 48-hour bar against a 72-hour weekend).
SOURCE_FRESHNESS_MIN_SPAN = timedelta(days=7)


def _as_utc_naive(t: datetime) -> datetime:
    return t if t.tzinfo is None else t.astimezone(timezone.utc).replace(tzinfo=None)


def longest_gap(times: list[datetime]) -> timedelta:
    """The longest stretch between consecutive times (sorted input)."""
    return max((b - a for a, b in zip(times, times[1:])), default=timedelta(0))


def _matured(history: list[datetime]) -> bool:
    return bool(history) and history[-1] - history[0] >= SOURCE_FRESHNESS_MIN_SPAN


def check_source_freshness(
    observed: dict[str, tuple[str, list[datetime] | None]], *, floor: timedelta, kind: str,
) -> None:
    """Alert on any source that has gone quiet for longer than its own rhythm.

    ``observed`` maps a stable source key to (label, the publication times
    this check saw, or None when the fetch failed). A failed fetch adds
    nothing, so a source that keeps failing goes stale the same way as one
    that keeps answering with old items, and a source that has never shown a
    dated item is measured from when it was first checked. One alert per
    stale stretch (``condition`` stays open until the source shows something
    new). Never raises.

    The bar is the source's longest gap in the history window plus
    SOURCE_FRESHNESS_SLACK, once that history spans
    SOURCE_FRESHNESS_MIN_SPAN; before then, the longest gap among the
    sources of the same ``kind`` that have that much history stands in.
    """
    now = utcnow()
    alerts: list[tuple[str, str, str, str]] = []
    resolved: list[str] = []
    db = None
    try:
        db = SessionLocal()
        # Every row of the tier: a source's peers are the other sources of
        # its kind, whichever call checks them (the national and state feeds
        # are fetched separately).
        rows = {r.cache_key: r for r in db.query(ApiCache).filter(ApiCache.tier == SOURCE_FRESHNESS_TIER)}
        states = {key: json.loads(row.data_json) for key, row in rows.items()}

        def window(times) -> list[datetime]:
            return sorted(t for t in times if t >= now - SOURCE_FRESHNESS_HISTORY)[-_SOURCE_FRESHNESS_KEEP:]

        peer_gaps = [
            longest_gap(h) for key, state in states.items()
            if key not in observed and state.get("kind") == kind
            and _matured(h := window(datetime.fromisoformat(t) for t in state.get("seen", [])))
        ]
        histories = {}
        for key, (_label, times) in observed.items():
            seen = {datetime.fromisoformat(t) for t in states.get(key, {}).get("seen", [])}
            histories[key] = window(seen | {_as_utc_naive(t) for t in times or []})
        peer_gaps += [longest_gap(h) for h in histories.values() if _matured(h)]
        peers = max(peer_gaps, default=None)

        for key, (label, times) in observed.items():
            row = rows.get(key)
            state = states.get(key, {})
            first_checked = datetime.fromisoformat(state.get("first_checked") or now.isoformat())
            history = histories[key]
            since = history[-1] if history else first_checked
            gap = longest_gap(history)
            measured_by = f"its longest gap in the last {SOURCE_FRESHNESS_HISTORY.days} days"
            if not _matured(history) and peers is not None and peers > gap:
                gap = peers
                measured_by = (f"it has under {SOURCE_FRESHNESS_MIN_SPAN.days} days of history, so the "
                               f"longest gap among other {kind}s that have that much")
            limit = max(floor, gap + SOURCE_FRESHNESS_SLACK)
            alerted = bool(state.get("alerted"))
            condition = f"source-stale:{key}"
            if now - since > limit:
                if not alerted:
                    hours = (now - since).total_seconds() / 3600
                    newest = (
                        f"its newest item is from {since:%Y-%m-%d %H:%M} UTC" if history
                        else f"it has shown no dated item since first checked ({since:%Y-%m-%d %H:%M} UTC)"
                    )
                    latest = ("The latest fetch failed." if times is None else
                              "The latest fetch succeeded: the source is answering with old items.")
                    body = (
                        f"The {kind} '{label}' ({key}) has had nothing new for {hours:.0f} hours: "
                        f"{newest}. The bar is {measured_by} ({gap.total_seconds() / 3600:.1f} hours) "
                        f"plus {SOURCE_FRESHNESS_SLACK.total_seconds() / 3600:.0f}, never under "
                        f"{floor.total_seconds() / 3600:.0f}: {limit.total_seconds() / 3600:.0f} hours. {latest}"
                    )
                    alerts.append((f"Stale {kind}: {label}", body, f"{condition}:{since.isoformat()}", condition))
                    alerted = True
            elif alerted:
                resolved.append(condition)
                alerted = False
                # The stale stretch is not the source's rhythm: kept, it would
                # set the bar for the next two months. Only when the source
                # came back — a bar that rose past the silence (more history,
                # or peers) leaves nothing to forget.
                stale_since = datetime.fromisoformat(state.get("since") or since.isoformat())
                if history and history[-1] > stale_since:
                    history = [t for t in history if t > stale_since]
            data = json.dumps({
                "label": label,
                "kind": kind,
                "first_checked": first_checked.isoformat(),
                "since": since.isoformat(),
                "alerted": alerted,
                "seen": [t.isoformat() for t in history],
            })
            if row is not None:
                row.data_json, row.cached_at = data, now
            else:
                db.add(ApiCache(tier=SOURCE_FRESHNESS_TIER, cache_key=key, data_json=data, cached_at=now))
        db.commit()
    except Exception:
        logger.exception("Source freshness check failed (non-fatal)")
        return
    finally:
        if db is not None:
            db.close()
    for subject, body, dedupe_key, condition in alerts:
        send_ops_alert(subject, body, dedupe_key=dedupe_key, condition=condition)
    for condition in resolved:
        resolve_ops_alert(condition)
