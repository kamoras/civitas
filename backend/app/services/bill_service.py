"""
Unified "bills currently moving through Congress" feed.

Unions SponsoredBill (Senate) and RepSponsoredBill (House) across all
current members into a single normalized, filterable, paginated list —
the cross-chamber counterpart to how politicians.py unions Senator/
Representative/President/Justice into one directory.
"""
import json
import time
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import settings
from app.models import ActionIssue, Representative, RepSponsoredBill, Senator, SponsoredBill
from app.schemas import BillInFlightSchema, PaginatedBillsSchema

# _collect_bills rebuilds ~17k rows (query + join + Pydantic construction)
# from scratch on every call — ~1.3-1.7s measured in production, even for a
# perPage=1 request, because none of that cost is in the pagination. Every
# filter/sort combination starts from the same unfiltered base set, so
# caching that base set for a short window (matching the 120s the API
# already promises callers via Cache-Control) turns every call after the
# first into an in-memory filter/sort instead of a fresh DB round trip —
# without this, a page that fires several of these concurrently (e.g. one
# per stage group in the grouped bills view) serializes into many seconds
# of wait on the single-worker Pi backend.
_COLLECT_CACHE_TTL_SECONDS = 120
_collect_cache: tuple[float, list["_Row"]] | None = None


def _bioguide_photo(bioguide_id: str | None) -> str | None:
    if not bioguide_id:
        return None
    return f"https://bioguide.congress.gov/bioguide/photo/{bioguide_id[0]}/{bioguide_id}.jpg"


def _bill_mention_counts(db: Session) -> dict[str, int]:
    """Return {bill_id: mention_count} across current Action Center issues.

    Mirrors politicians._build_active_issue_map's JSON-field-scan approach —
    no LLM, just counting how many currently-live issues reference each
    bill (ActionIssue.related_bill_ids entries are {name, id, url} dicts
    with `id` already in our bill_id format, e.g. "HR.22", produced by
    action_center._resolve_bills).
    """
    issues = (
        db.query(ActionIssue.related_bill_ids)
        .filter(ActionIssue.is_current == True)  # noqa: E712
        .all()
    )
    counts: dict[str, int] = {}
    for (related_bill_ids,) in issues:
        if not related_bill_ids:
            continue
        try:
            entries = json.loads(related_bill_ids)
        except Exception:
            continue
        for entry in entries:
            bill_id = entry.get("id")
            if bill_id:
                counts[bill_id] = counts.get(bill_id, 0) + 1
    return counts


@dataclass
class _Row:
    bill: BillInFlightSchema
    latest_action_date: str
    mention_count: int


def _collect_bills(db: Session) -> list[_Row]:
    global _collect_cache
    now = time.monotonic()
    if _collect_cache is not None and (now - _collect_cache[0]) < _COLLECT_CACHE_TTL_SECONDS:
        # A fresh list every call — callers (get_bills_in_flight) sort this
        # in place, and when no filter is active that's the very list we'd
        # be handing back out of the cache on the next call too.
        return list(_collect_cache[1])

    rows: list[_Row] = []
    min_congress = settings.CURRENT_CONGRESS
    mention_counts = _bill_mention_counts(db)

    senate_query = (
        db.query(SponsoredBill, Senator)
        .join(Senator, SponsoredBill.senator_id == Senator.id)
        .filter(SponsoredBill.congress >= min_congress)
        .filter(Senator.is_current == True)  # noqa: E712
    )
    for sp, senator in senate_query.all():
        mention_count = mention_counts.get(sp.bill_id, 0)
        rows.append(_Row(
            bill=BillInFlightSchema(
                bill_id=sp.bill_id,
                title=sp.title,
                chamber="senate",
                sponsor_id=senator.id,
                sponsor_name=senator.name,
                sponsor_party=senator.party,
                sponsor_state=senator.state,
                sponsor_thumbnail_url=_bioguide_photo(senator.bioguide_id),
                introduced_date=sp.introduced_date,
                latest_action=sp.latest_action,
                latest_action_date=sp.latest_action_date,
                stage=sp.stage or "INTRODUCED",
                policy_area=sp.policy_area,
                congress=sp.congress,
                bill_type=sp.bill_type,
                is_law=sp.is_law,
                mention_count=mention_count,
            ),
            latest_action_date=sp.latest_action_date,
            mention_count=mention_count,
        ))

    house_query = (
        db.query(RepSponsoredBill, Representative)
        .join(Representative, RepSponsoredBill.representative_id == Representative.id)
        .filter(RepSponsoredBill.congress >= min_congress)
        .filter(Representative.is_current == True)  # noqa: E712
    )
    for sp, rep in house_query.all():
        mention_count = mention_counts.get(sp.bill_id, 0)
        rows.append(_Row(
            bill=BillInFlightSchema(
                bill_id=sp.bill_id,
                title=sp.title,
                chamber="house",
                sponsor_id=rep.id,
                sponsor_name=rep.name,
                sponsor_party=rep.party,
                sponsor_state=rep.state,
                sponsor_thumbnail_url=_bioguide_photo(rep.bioguide_id),
                introduced_date=sp.introduced_date,
                latest_action=sp.latest_action,
                latest_action_date=sp.latest_action_date,
                stage=sp.stage or "INTRODUCED",
                policy_area=sp.policy_area,
                congress=sp.congress,
                bill_type=sp.bill_type,
                is_law=sp.is_law,
                mention_count=mention_count,
            ),
            latest_action_date=sp.latest_action_date,
            mention_count=mention_count,
        ))

    _collect_cache = (now, rows)
    return list(rows)


def clear_bill_collection_cache() -> None:
    """Drop the cached row list (e.g. between test runs, or after a write
    that should be visible immediately rather than waiting out the TTL)."""
    global _collect_cache
    _collect_cache = None


def get_bills_in_flight(
    db: Session,
    stage: str | None = None,
    chamber: str | None = None,
    party: str | None = None,
    q: str | None = None,
    sort: str = "recent",
    page: int = 1,
    per_page: int = 25,
) -> PaginatedBillsSchema:
    all_rows = _collect_bills(db)

    stage_counts: dict[str, int] = {}
    for row in all_rows:
        stage_counts[row.bill.stage] = stage_counts.get(row.bill.stage, 0) + 1

    filtered = all_rows
    if stage:
        filtered = [r for r in filtered if r.bill.stage == stage]
    if chamber:
        filtered = [r for r in filtered if r.bill.chamber == chamber]
    if party:
        filtered = [r for r in filtered if r.bill.sponsor_party == party]
    if q:
        q_lower = q.lower()
        filtered = [
            r for r in filtered
            if q_lower in r.bill.title.lower()
            or q_lower in r.bill.sponsor_name.lower()
            or q_lower in r.bill.bill_id.lower()
        ]

    if sort == "hot":
        # "Active in Congress right now" — bills currently referenced by a
        # live Action Center issue, most-mentioned first, ties broken by
        # how recently Congress acted on them.
        filtered = [r for r in filtered if r.mention_count > 0]
        filtered.sort(key=lambda r: (r.mention_count, r.latest_action_date), reverse=True)
    elif sort == "stale":
        # Oldest action first. Only meaningful within a single stage (a
        # committee bill idling for months is normal; the same idle time
        # for a bill already passed and sitting in the other chamber is
        # not) — callers should pass `stage` alongside this, since sorting
        # a mixed-stage set this way would just surface ancient dead bills
        # from every stage rather than "what's stuck right now."
        filtered.sort(key=lambda r: r.latest_action_date)
    else:
        filtered.sort(key=lambda r: r.latest_action_date, reverse=True)

    total = len(filtered)
    total_pages = max(1, (total + per_page - 1) // per_page)
    start = (page - 1) * per_page
    page_rows = filtered[start:start + per_page]

    return PaginatedBillsSchema(
        bills=[r.bill for r in page_rows],
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        stage_counts=stage_counts,
    )
