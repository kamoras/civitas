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
from datetime import datetime, timedelta

import httpx
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.http_client import make_async_client
from app.models import RepSponsoredBill, SponsoredBill
from app.pipeline.analyze.bill_stage import classify_bill_stage_from_actions, is_enacted, became_law_action
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
_MAX_LIST_PAGES = 8  # up to 2,000 most-recently-updated bills per cycle
# Backstop against a pathological cycle (first run after a long outage,
# say) fanning out thousands of per-bill actions fetches in one go.
# Anything past the cap is caught by later cycles or the nightly rebuild.
_MAX_ACTION_FETCHES = 500

_tracker = PipelineRunTracker()

_ENACTED = str(classify_bill_stage_from_actions([], is_law=True))


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


async def _fetch_recently_updated(client: httpx.AsyncClient, since: datetime) -> dict[str, dict]:
    """{our bill_id format ("HR.22") -> Congress.gov bill list item} for
    current-congress bills updated since `since`, newest first, bounded by
    _MAX_LIST_PAGES. Deliberately NOT ApiCache'd — the whole point of the
    call is what changed in the last hour."""
    from_param = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    found: dict[str, dict] = {}
    offset = 0
    for _ in range(_MAX_LIST_PAGES):
        data = await _fetch_with_retry(
            client,
            f"{CONGRESS_API_BASE}/bill"
            f"?fromDateTime={from_param}&sort=updateDate+desc&limit={_PAGE_SIZE}&offset={offset}",
        )
        page = (data or {}).get("bills") or []
        for item in page:
            bill_type = (item.get("type") or "").upper()
            number = item.get("number")
            if not bill_type or number is None:
                continue
            if (item.get("congress") or 0) < settings.CURRENT_CONGRESS:
                continue
            # setdefault: sorted newest-update-first, so the first
            # occurrence is the most current view of the bill.
            found.setdefault(f"{bill_type}.{number}", item)
        if len(page) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
    return found


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
    """Whether a listing's latest action may replace the stored one: only
    one dated strictly after it, or anything over a row with no date. Not
    one on the same day — the listing can lag a later action that day the
    nightly pipeline stored from the bill itself, and a date can't say which
    came first (the nightly run settles it) — and not an undated one over a
    dated row, which it can't be ordered against. Except that the "Became
    Public/Private Law" action (`makes_law`) may replace any other action on
    its own day: becoming law ends a bill's history, so nothing stored that
    day came after it — and Congress.gov dates it the day the President
    signed, often the day of the stored "Signed by President." action."""
    if not stored_date:
        return True
    if not new_date:
        return False
    return new_date > stored_date or (makes_law and new_date == stored_date)


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
    actions_cache: dict[str, list[dict]] = {}
    bill_ids = list(recent)

    # Every write waits for the end of the pass and is applied in one go:
    # none holds SQLite's write lock across the fetches between them.
    writes: list[tuple[type, int, str, str, bool, dict]] = []
    for model in (SponsoredBill, RepSponsoredBill):
        rows = []
        for i in range(0, len(bill_ids), 500):  # stay under SQLite's bind-parameter limit
            rows.extend(
                db.query(model)
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
            if not _supersedes(new_date, row.latest_action_date, makes_law):
                # The listing can lag what the nightly pipeline stored from the
                # bill itself: an action not dated after the stored one never
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
                    continue
                actions = await _fetch_fresh_actions(db, client, congress, bill_type, number)
                actions_cache[actions_key] = actions
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
                    else_=str(classify_bill_stage_from_actions(actions, is_law)),
                )
            # else: keep the stored stage — a failed/empty actions fetch
            # must not regress a real stage to the INTRODUCED fallback.
            writes.append((model, row.id, row.bill_id, new_date, makes_law, values))

    for model, row_id, bill_id, new_date, makes_law, values in writes:
        # _supersedes again at write time: the row may have moved on since it
        # was read (the nightly pipeline rewrites these). It rewrites them by
        # delete and insert, and SQLite can hand a deleted row's id to a new
        # one — the bill_id is what says it is still the same bill's row. A
        # row deleted since simply matches nothing.
        undated = or_(model.latest_action_date.is_(None), model.latest_action_date == "")
        if not new_date:
            superseded = undated
        elif makes_law:
            # The same day, only over an action that isn't itself the law.
            stored = func.lower(func.coalesce(model.latest_action, ""))
            superseded = or_(
                undated, model.latest_action_date < new_date,
                and_(
                    model.latest_action_date == new_date,
                    ~stored.contains("became public law"), ~stored.contains("became private law"),
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
            "deferred to the next cycle", _MAX_ACTION_FETCHES, skipped_at_cap,
        )
    return {
        "matched": matched,
        "changed": changed,
        "action_fetches": len(actions_cache),
        "skipped_at_cap": skipped_at_cap,
    }


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
            recent = await _fetch_recently_updated(client, since)
            summary = await _apply_updates(db, client, recent)
        # Only advance the window marker after a full successful pass, so
        # a crashed cycle is retried over the same window next hour.
        api_cache_set(db, _LAST_RUN_TIER, LAST_RUN_CACHE_KEY, {"lastRun": now.isoformat()})
    finally:
        if owns_session:
            db.close()

    if summary["changed"]:
        from app.services.bill_service import warm_bill_collection_cache
        warm_bill_collection_cache()

    summary["status"] = "completed"
    summary["window_start"] = since.isoformat()
    summary["recently_updated"] = len(recent)
    return summary
