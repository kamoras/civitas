"""
Hourly incremental bill-status refresh.

The nightly pipeline rebuilds every member's sponsored-bill rows from
scratch (per-member fetches + LLM classification — hours of work), which
means bill status on the /bills view could lag Congress.gov by up to a
day. This module closes that gap cheaply enough to run hourly on the Pi:
ask Congress.gov which bills changed since the last cycle (one paginated
listing call — GET /bill?fromDateTime=...&sort=updateDate+desc), and for
the subset we already track whose latestAction actually moved, update
latest_action / latest_action_date / is_law in place and re-derive
`stage` from a fresh actions fetch (classify_bill_stage_from_actions —
deterministic table lookup, no LLM).

Cost is bounded and mostly network: the listing is a handful of requests,
and per-bill actions are fetched only for tracked bills whose latest
action text/date actually changed — typically dozens per hour when
Congress is in session, zero when it isn't. updateDate churns for reasons
that don't move a bill (summaries posted, cosponsors added); those are
matched but skipped without any extra fetch. Freshly fetched actions
overwrite the ApiCache entry the nightly pipeline reads, so the next full
run benefits from them too.

Scheduling and the skip-while-nightly-runs guard live in scheduler.py.
"""
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.config_definitions import BillStage
from app.http_client import make_async_client
from app.models import RepSponsoredBill, SponsoredBill
from app.pipeline.analyze.bill_stage import (
    LAW_ACTION_PHRASES,
    became_law_action,
    classify_bill_stage_from_actions,
    is_enacted,
)
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.congress import CONGRESS_API_BASE, _fetch_with_retry
from app.pipeline.run_tracker import PipelineRunTracker
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

# Last-successful-run marker, persisted through the ApiCache table (same
# store the fetchers already use) so the update window survives restarts.
LAST_RUN_CACHE_KEY = "bill-status-refresh-last-run"
_LAST_RUN_TIER = "congress"

_DEFAULT_LOOKBACK = timedelta(hours=24)
_MAX_LOOKBACK = timedelta(days=7)
# Congress.gov's updateDate clock and ours won't agree to the second, so
# each cycle re-scans a little history past the last run rather than risk
# missing an update at the boundary. Updates are idempotent (same values
# written again), so the overlap costs nothing.
_WINDOW_OVERLAP = timedelta(minutes=30)

_PAGE_SIZE = 250
_MAX_LIST_PAGES = 8  # up to 2,000 updated bills per cycle, oldest update first
# Backstop against a pathological cycle (first run after a long outage,
# say) fanning out thousands of per-bill actions fetches in one go.
# Anything past either cap is picked up by the next cycle: the marker is
# left at the oldest update not yet applied (_resume_point), not at now.
_MAX_ACTION_FETCHES = 500

_tracker = PipelineRunTracker()

_ENACTED = str(BillStage.ENACTED)


def bill_tracker() -> PipelineRunTracker:
    """The in-process guard the scheduled refresh runs under (lease.tracked_job)."""
    return _tracker


def _window_start(db: Session, now: datetime) -> datetime:
    stored = api_cache_get(
        db, _LAST_RUN_TIER, LAST_RUN_CACHE_KEY,
        max_age_hours=int(_MAX_LOOKBACK.total_seconds() // 3600),
    )
    if stored and stored.get("lastRun"):
        try:
            last_run = datetime.fromisoformat(stored["lastRun"])
            return max(now - _MAX_LOOKBACK, last_run - _WINDOW_OVERLAP)
        except ValueError:
            logger.warning("Unparseable %s marker %r — using default lookback", LAST_RUN_CACHE_KEY, stored)
    return now - _DEFAULT_LOOKBACK


def _update_time(item: dict) -> datetime | None:
    """A listing item's updateDate as naive UTC. The listing gives only the
    day ("2026-09-25", checked live 2026-09-27), read as its start: a cycle
    resuming there re-reads that day, which costs nothing for what it has
    applied, and always gets past it — a day brings hundreds of updates
    (321 and 639 on 2026-09-24/25), far under _MAX_LIST_PAGES' 2,000."""
    raw = item.get("updateDate") or ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


async def _fetch_recently_updated(
    client: httpx.AsyncClient, since: datetime,
) -> tuple[dict[str, dict], datetime | None]:
    """({our bill_id format ("HR.22") -> Congress.gov bill list item},
    resume_at) for current-congress bills updated since `since`.

    Oldest update first, so that when _MAX_LIST_PAGES cuts the listing off
    what is left is the newer end, and `resume_at` (the last update read)
    is where the next cycle starts. Read newest first, the cut dropped the
    oldest updates, and the window then moved past them for good. A bill
    updated again while the pages are read moves to the end of the
    listing; one skipped by that shift is newer than `resume_at`, so the
    next cycle still reads it. `resume_at` is None when the listing was
    read to its end. Deliberately NOT ApiCache'd — the whole point of the
    call is what changed since the last cycle."""
    from_param = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    found: dict[str, dict] = {}
    offset = 0
    last_seen: datetime | None = None
    for _ in range(_MAX_LIST_PAGES):
        data = await _fetch_with_retry(
            client,
            f"{CONGRESS_API_BASE}/bill"
            f"?fromDateTime={from_param}&sort=updateDate+asc&limit={_PAGE_SIZE}&offset={offset}",
        )
        page = (data or {}).get("bills") or []
        for item in page:
            last_seen = _update_time(item) or last_seen
            bill_type = (item.get("type") or "").upper()
            number = item.get("number")
            if not bill_type or number is None:
                continue
            if (item.get("congress") or 0) < settings.CURRENT_CONGRESS:
                continue
            # Oldest update first, so a later occurrence is the more
            # current view of the bill.
            found[f"{bill_type}.{number}"] = item
        if len(page) < _PAGE_SIZE:
            return found, None
        offset += _PAGE_SIZE
    return found, last_seen


async def _fetch_fresh_actions(
    db: Session, client: httpx.AsyncClient, congress: int, bill_type: str, number,
) -> list[dict]:
    """Like congress.fetch_bill_actions but bypassing the ApiCache read —
    the bill just changed, so a cached actions list is exactly what we
    don't want. The result is written back to the same cache key, so the
    nightly pipeline's fetch_bill_actions reads current data too."""
    data = await _fetch_with_retry(
        client,
        f"{CONGRESS_API_BASE}/bill/{congress}/{bill_type}/{number}/actions?limit=100",
    )
    raw = (data or {}).get("actions", [])
    results = raw.get("item", []) if isinstance(raw, dict) else (raw or [])
    if results:
        api_cache_set(db, "congress", f"bill-actions-{congress}-{bill_type}-{number}", results)
    return results


def _supersedes(new_date: str, stored_date: str | None, makes_law: bool = False) -> bool:
    """Whether a listing's latest action may replace the stored one on its
    date alone: one dated strictly after it, or anything over a row with no
    date — and not an undated one over a dated row, which it can't be
    ordered against. On the stored action's own day a date can't say which
    came first, and the listing can lag a later action that day the nightly
    pipeline stored; there the bill's own history decides (_newest_action),
    except for the "Became Public/Private Law" action (`makes_law`): it ends
    a bill's history, so nothing stored that day came after it — and
    Congress.gov dates it the day the President signed, often the day of the
    stored "Signed by President." action."""
    if not stored_date:
        return True
    if not new_date:
        return False
    return new_date > stored_date or (makes_law and new_date == stored_date)


def _newest_action(actions: list[dict]) -> str | None:
    """The text of a bill's newest action, from its history (Congress.gov
    lists it newest first, same-day actions in order), or None without one."""
    return ((actions[0].get("text") or "").strip() or None) if actions else None


async def _apply_updates(
    db: Session, client: httpx.AsyncClient, recent: dict[str, dict],
) -> dict:
    """Update tracked sponsored-bill rows whose latest action moved.

    Returns {"matched": tracked bills that appeared in the update feed,
    "changed": rows actually rewritten, "action_fetches": per-bill actions
    calls made, "skipped_at_cap": changed bills left for a later cycle}.
    """
    matched = 0
    changed = 0
    skipped_at_cap = 0
    deferred_from: datetime | None = None
    actions_cache: dict[str, list[dict]] = {}
    bill_ids = list(recent)

    # Every write waits for the end of the pass and is applied in one go:
    # none holds SQLite's write lock across the fetches between them.
    writes: list[tuple[type, int, str, str, bool, str | None, dict]] = []
    for model in (SponsoredBill, RepSponsoredBill):
        # Values, not ORM rows: the actions fetch below commits, expiring
        # every loaded row, and a row read again after that holds whatever
        # the nightly pipeline has written since (a reused id, a newer
        # action) — not what the guards were checked against — or is gone.
        rows = []
        for i in range(0, len(bill_ids), 500):  # stay under SQLite's bind-parameter limit
            rows.extend(
                db.query(
                    model.id, model.bill_id, model.latest_action, model.latest_action_date,
                    model.is_law, model.bill_type, model.congress,
                )
                .filter(model.bill_id.in_(bill_ids[i:i + 500]))
                .filter(model.congress >= settings.CURRENT_CONGRESS)
                .all()
            )
        for row in rows:
            item = recent[row.bill_id]
            latest = item.get("latestAction") or {}
            new_text = latest.get("text") or ""
            new_date = latest.get("actionDate") or ""
            if not new_text and not new_date:
                continue
            matched += 1
            if new_text == row.latest_action and new_date == row.latest_action_date:
                continue  # updateDate churn without a new action — nothing to do
            # The latest action alone decides whether a same-day action may
            # replace the stored one (_supersedes); is_law itself is read
            # below, from the history too, as every writer reads it.
            becomes_law = became_law_action(new_text)
            # Not "and not row.is_law": a row read as law from its history
            # ("Signed by President.") still takes the same-day law number.
            makes_law = becomes_law and not became_law_action(row.latest_action)
            same_day = bool(new_date) and new_date == row.latest_action_date and not makes_law
            if not same_day and not _supersedes(new_date, row.latest_action_date, makes_law):
                # The listing can lag what the nightly pipeline stored from the
                # bill itself: an action dated before the stored one never
                # replaces it (see _supersedes).
                continue

            congress = item.get("congress") or row.congress
            bill_type = (item.get("type") or row.bill_type or "").lower()
            number = item.get("number")
            actions_key = f"{congress}-{bill_type}-{number}"
            if actions_key in actions_cache:
                actions = actions_cache[actions_key]
            else:
                if len(actions_cache) >= _MAX_ACTION_FETCHES:
                    skipped_at_cap += 1
                    updated = _update_time(item)
                    if updated is not None and (deferred_from is None or updated < deferred_from):
                        deferred_from = updated
                    continue
                actions = await _fetch_fresh_actions(db, client, congress, bill_type, number)
                actions_cache[actions_key] = actions
            if same_day and _newest_action(actions) != new_text.strip():
                # A later action the same day, by the bill's own history — or
                # no history to say: the stored one stands (_supersedes).
                continue
            enacted = is_enacted(new_text, actions)
            # is_law is monotone: never un-set it.
            is_law = row.is_law or enacted
            values: dict = {"latest_action": new_text, "latest_action_date": new_date}
            if enacted:
                # Only ever set, never cleared — and so monotone at write
                # time too, whatever the row holds by then.
                values["is_law"] = True
            if actions or is_law:
                # A law recorded since the row was read (is_law at write
                # time) keeps its stage whatever the actions said.
                values["stage"] = case(
                    (model.is_law, _ENACTED),
                    else_=str(classify_bill_stage_from_actions(actions, is_law, bill_type)),
                )
            # else: keep the stored stage — a failed/empty actions fetch
            # must not regress a real stage to the INTRODUCED fallback.
            # A same-day write goes only over the action it was checked
            # against: had the row moved since, the history said nothing
            # about the action it holds now.
            same_day_over = (row.latest_action or "") if same_day else None
            writes.append((model, row.id, row.bill_id, new_date, makes_law, same_day_over, values))

    for model, row_id, bill_id, new_date, makes_law, same_day_over, values in writes:
        # _supersedes again at write time: the row may have moved on since it
        # was read (the nightly pipeline rewrites these). It rewrites them by
        # delete and insert, and SQLite can hand a deleted row's id to a new
        # one — the bill_id is what says it is still the same bill's row. A
        # row deleted since simply matches nothing.
        undated = or_(model.latest_action_date.is_(None), model.latest_action_date == "")
        if same_day_over is not None:
            superseded = and_(
                model.latest_action_date == new_date, func.coalesce(model.latest_action, "") == same_day_over,
            )
        elif not new_date:
            superseded = undated
        elif makes_law:
            # The same day, only over an action that isn't itself the law.
            stored = func.lower(func.coalesce(model.latest_action, ""))
            superseded = or_(
                undated, model.latest_action_date < new_date,
                and_(
                    model.latest_action_date == new_date,
                    *(~stored.contains(phrase) for phrase in LAW_ACTION_PHRASES),  # became_law_action's
                ),
            )
        else:
            superseded = or_(undated, model.latest_action_date < new_date)
        rows_written = db.query(model).filter(model.id == row_id, model.bill_id == bill_id, superseded)
        changed += rows_written.update(values, synchronize_session=False)
    db.commit()
    if skipped_at_cap:
        logger.warning(
            "Bill status refresh hit the %d actions-fetch cap — %d changed bills "
            "left for the next cycle", _MAX_ACTION_FETCHES, skipped_at_cap,
        )
    summary = {
        "matched": matched,
        "changed": changed,
        "action_fetches": len(actions_cache),
        "skipped_at_cap": skipped_at_cap,
    }
    if deferred_from is not None:
        summary["deferred_from"] = deferred_from
    return summary


def _resume_point(now: datetime, listing_cut_at: datetime | None, deferred_from: datetime | None) -> datetime:
    """Where the next cycle's window starts (less _WINDOW_OVERLAP): the
    oldest update this cycle did not apply, or now when it applied all."""
    pending = [t for t in (listing_cut_at, deferred_from) if t is not None]
    return min(pending) if pending else now


async def refresh_bill_statuses(db: Session | None = None) -> dict:
    """Run one incremental refresh cycle. Pass `db` for tests; production
    opens (and closes) its own session. The scheduler runs one pass at a
    time (lease.run_tracked over bill_tracker()) and cuts a hung one off
    (lease.CutOff) at its next await — which always comes: every step
    between awaits is bounded (a request times out, SQLite gives up after
    its busy timeout) — so no second pass ever runs beside it."""
    owns_session = db is None
    if owns_session:
        from app.database import SessionLocal
        db = SessionLocal()
    try:
        now = utcnow()
        since = _window_start(db, now)
        async with make_async_client() as client:
            recent, listing_cut_at = await _fetch_recently_updated(client, since)
            summary = await _apply_updates(db, client, recent)
        # Only advance the window marker after a full successful pass, so
        # a crashed cycle is retried over the same window next hour — and
        # only as far as what it applied.
        resume = _resume_point(now, listing_cut_at, summary.pop("deferred_from", None))
        api_cache_set(db, _LAST_RUN_TIER, LAST_RUN_CACHE_KEY, {"lastRun": resume.isoformat()})
    finally:
        if owns_session:
            db.close()

    if summary["changed"]:
        from app.services.bill_service import warm_bill_collection_cache
        warm_bill_collection_cache()

    summary["status"] = "completed"
    summary["window_start"] = since.isoformat()
    if resume != now:
        summary["resumes_at"] = resume.isoformat()
    summary["recently_updated"] = len(recent)
    return summary
