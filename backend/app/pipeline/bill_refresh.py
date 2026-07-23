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
from sqlalchemy.orm import Session

from app.config import settings
from app.models import RepSponsoredBill, SponsoredBill
from app.pipeline.analyze.bill_stage import classify_bill_stage_from_actions
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.congress import CONGRESS_API_BASE, _fetch_with_retry
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

_running_since: datetime | None = None


def is_bill_refresh_running() -> bool:
    return _running_since is not None


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

            # is_law is monotone: never un-set it, and the latest-action
            # text is the same "hard fact from the API" the pipelines use.
            is_law = row.is_law or "public law" in new_text.lower()

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
            if actions or is_law:
                row.stage = str(classify_bill_stage_from_actions(actions, is_law))
            # else: keep the stored stage — a failed/empty actions fetch
            # must not regress a real stage to the INTRODUCED fallback.

            row.latest_action = new_text
            row.latest_action_date = new_date
            row.is_law = is_law
            changed += 1

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
    opens (and closes) its own session."""
    global _running_since
    if _running_since is not None:
        return {"status": "skipped", "reason": "previous refresh still running"}
    _running_since = utcnow()
    try:
        owns_session = db is None
        if owns_session:
            from app.database import SessionLocal
            db = SessionLocal()
        try:
            now = utcnow()
            since = _window_start(db, now)
            async with httpx.AsyncClient() as client:
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
    finally:
        _running_since = None
