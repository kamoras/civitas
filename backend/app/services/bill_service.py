"""
Unified "bills currently moving through Congress" feed.

Unions SponsoredBill (Senate) and RepSponsoredBill (House) across all
current members into a single normalized, filterable, paginated list —
the cross-chamber counterpart to how politicians.py unions Senator/
Representative/President/Justice into one directory.
"""
import json
import logging
import threading
import time
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import settings
from app.config_definitions import BillStage
from app.models import ActionIssue, Representative, RepSponsoredBill, Senator, SponsoredBill
from app.schemas import (
    BillDetailSchema,
    BillInFlightSchema,
    PaginatedBillsSchema,
    PolicyAreaDetail,
    RelatedIssueSchema,
)

logger = logging.getLogger(__name__)

# _build_rows materializes ~17k rows (query + join + Pydantic construction)
# from scratch — over a second on the production Pi, even for a perPage=1
# request, because none of that cost is in the pagination. Every filter/sort
# combination starts from the same unfiltered base set, so that base set is
# cached at module scope and served STALE-WHILE-REVALIDATE: a request that
# finds the cache older than the TTL still answers from it instantly and
# kicks off a background rebuild, so no user request ever blocks on the
# rebuild once the cache has been populated (startup pre-warms it — see
# main.py's lifespan). The TTL matches the 120s the API already promises
# callers via Cache-Control; writers that change the underlying data
# (hourly bill-status refresh, action-center refresh, nightly pipeline)
# call warm_bill_collection_cache() to swap in fresh data sooner.
_COLLECT_CACHE_TTL_SECONDS = 120
_collect_cache: tuple[float, list["_Row"]] | None = None
_refresh_state_lock = threading.Lock()
_refresh_in_progress = False


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


def _build_rows(db: Session) -> list[_Row]:
    """The uncached rebuild: every current-member sponsored bill across both
    chambers, as _Row objects ready for filtering/sorting.

    Selects explicit columns rather than (SponsoredBill, Senator) entity
    pairs: full ORM hydration also loads columns this feed never shows
    (policy_areas JSON blobs, party_leaning) and pays per-instance identity-
    map bookkeeping for ~17k throwaway objects — measured as a large share
    of the rebuild cost on the Pi.
    """
    rows: list[_Row] = []
    min_congress = settings.CURRENT_CONGRESS
    mention_counts = _bill_mention_counts(db)

    chamber_queries = [
        (
            "senate",
            db.query(
                SponsoredBill.bill_id, SponsoredBill.title, SponsoredBill.introduced_date,
                SponsoredBill.latest_action, SponsoredBill.latest_action_date,
                SponsoredBill.stage, SponsoredBill.policy_area, SponsoredBill.congress,
                SponsoredBill.bill_type, SponsoredBill.is_law,
                Senator.id, Senator.name, Senator.party, Senator.state, Senator.bioguide_id,
            )
            .join(Senator, SponsoredBill.senator_id == Senator.id)
            .filter(SponsoredBill.congress >= min_congress)
            .filter(Senator.is_current == True),  # noqa: E712
        ),
        (
            "house",
            db.query(
                RepSponsoredBill.bill_id, RepSponsoredBill.title, RepSponsoredBill.introduced_date,
                RepSponsoredBill.latest_action, RepSponsoredBill.latest_action_date,
                RepSponsoredBill.stage, RepSponsoredBill.policy_area, RepSponsoredBill.congress,
                RepSponsoredBill.bill_type, RepSponsoredBill.is_law,
                Representative.id, Representative.name, Representative.party,
                Representative.state, Representative.bioguide_id,
            )
            .join(Representative, RepSponsoredBill.representative_id == Representative.id)
            .filter(RepSponsoredBill.congress >= min_congress)
            .filter(Representative.is_current == True),  # noqa: E712
        ),
    ]
    for chamber, query in chamber_queries:
        for (
            bill_id, title, introduced_date, latest_action, latest_action_date,
            stage, policy_area, congress, bill_type, is_law,
            member_id, member_name, member_party, member_state, bioguide_id,
        ) in query.all():
            mention_count = mention_counts.get(bill_id, 0)
            rows.append(_Row(
                bill=BillInFlightSchema(
                    bill_id=bill_id,
                    title=title,
                    chamber=chamber,
                    sponsor_id=member_id,
                    sponsor_name=member_name,
                    sponsor_party=member_party,
                    sponsor_state=member_state,
                    sponsor_thumbnail_url=_bioguide_photo(bioguide_id),
                    introduced_date=introduced_date,
                    latest_action=latest_action,
                    latest_action_date=latest_action_date,
                    stage=stage or BillStage.INTRODUCED,
                    policy_area=policy_area,
                    congress=congress,
                    bill_type=bill_type,
                    is_law=is_law,
                    mention_count=mention_count,
                ),
                latest_action_date=latest_action_date,
                mention_count=mention_count,
            ))
    return rows


def _refresh_cache_in_background() -> None:
    """Rebuild the collection on a daemon thread (own DB session) and swap
    it into the cache atomically. At most one rebuild runs at a time; extra
    triggers while one is in flight are dropped, not queued."""
    global _refresh_in_progress
    with _refresh_state_lock:
        if _refresh_in_progress:
            return
        _refresh_in_progress = True

    def _run() -> None:
        global _collect_cache, _refresh_in_progress
        try:
            from app.database import session_scope
            with session_scope() as db:
                rows = _build_rows(db)
            _collect_cache = (time.monotonic(), rows)
        except Exception:
            logger.exception("Background bill-collection rebuild failed — serving the previous snapshot")
        finally:
            with _refresh_state_lock:
                _refresh_in_progress = False

    threading.Thread(target=_run, daemon=True, name="bill-cache-refresh").start()


def warm_bill_collection_cache() -> None:
    """Rebuild the collection cache in the background regardless of TTL.

    Called at startup (so the first /api/bills request after a deploy is a
    cache hit) and after anything that changes the underlying data — the
    hourly bill-status refresh, the action-center refresh (mention counts
    feed the "hot" sort), and the nightly pipeline. Unlike
    clear_bill_collection_cache this never leaves the cache empty, so no
    request ever pays the cold-rebuild cost because data got fresher.
    """
    _refresh_cache_in_background()


def _collect_bills(db: Session) -> list[_Row]:
    global _collect_cache
    cached = _collect_cache
    if cached is not None:
        if (time.monotonic() - cached[0]) >= _COLLECT_CACHE_TTL_SECONDS:
            # Stale-while-revalidate: answer from the stale snapshot now,
            # rebuild behind the scenes for the next caller.
            _refresh_cache_in_background()
        # A fresh list every call — callers (get_bills_in_flight) sort this
        # in place, and when no filter is active that's the very list we'd
        # be handing back out of the cache on the next call too.
        return list(cached[1])

    # Cold start (first request before the startup warm finishes, or right
    # after clear_bill_collection_cache): nothing to serve, build inline.
    rows = _build_rows(db)
    _collect_cache = (time.monotonic(), rows)
    return list(rows)


def _parse_policy_areas(raw_json: str | None) -> list[PolicyAreaDetail]:
    if not raw_json:
        return []
    try:
        raw_areas = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return []
    return [
        PolicyAreaDetail(
            area=a.get("area", ""),
            confidence=a.get("confidence", 0.0),
            party=a.get("party", "bipartisan"),
        )
        for a in raw_areas
        if a.get("area")
    ]


def _issues_mentioning(db: Session, bill_id: str) -> list[RelatedIssueSchema]:
    """Current Action Center issues whose related_bill_ids references this bill."""
    issues = (
        db.query(ActionIssue.id, ActionIssue.date, ActionIssue.title, ActionIssue.related_bill_ids)
        .filter(ActionIssue.is_current == True)  # noqa: E712
        .all()
    )
    result: list[RelatedIssueSchema] = []
    for issue_id, date, title, related_bill_ids in issues:
        if not related_bill_ids:
            continue
        try:
            entries = json.loads(related_bill_ids)
        except (json.JSONDecodeError, TypeError):
            continue
        if any(e.get("id") == bill_id for e in entries):
            result.append(RelatedIssueSchema(id=issue_id, date=date, title=title))
    return result


def get_bill_detail(db: Session, bill_id: str) -> BillDetailSchema | None:
    """Look up a single bill by its bill_id (e.g. "S.4967", "HR.22") among
    current senators' and representatives' sponsored bills.

    Queried directly rather than through _collect_bills' cache — a single
    lookup on the indexed bill_id column (ix_*_bill_id, database.py) is
    cheaper than scanning the ~17k-row cache, and this endpoint is hit far
    less often than the list view.
    """
    bill_id = bill_id.upper()

    row = (
        db.query(SponsoredBill, Senator)
        .join(Senator, SponsoredBill.senator_id == Senator.id)
        .filter(SponsoredBill.bill_id == bill_id)
        .filter(Senator.is_current == True)  # noqa: E712
        .first()
    )
    chamber = "senate"
    if row is None:
        row = (
            db.query(RepSponsoredBill, Representative)
            .join(Representative, RepSponsoredBill.representative_id == Representative.id)
            .filter(RepSponsoredBill.bill_id == bill_id)
            .filter(Representative.is_current == True)  # noqa: E712
            .first()
        )
        chamber = "house"
    if row is None:
        return None

    sp, member = row
    related = _issues_mentioning(db, bill_id)

    return BillDetailSchema(
        bill_id=sp.bill_id,
        title=sp.title,
        chamber=chamber,
        sponsor_id=member.id,
        sponsor_name=member.name,
        sponsor_party=member.party,
        sponsor_state=member.state,
        sponsor_thumbnail_url=_bioguide_photo(member.bioguide_id),
        introduced_date=sp.introduced_date,
        latest_action=sp.latest_action,
        latest_action_date=sp.latest_action_date,
        stage=sp.stage or BillStage.INTRODUCED,
        policy_area=sp.policy_area,
        congress=sp.congress,
        bill_type=sp.bill_type,
        is_law=sp.is_law,
        mention_count=len(related),
        policy_areas=_parse_policy_areas(sp.policy_areas),
        party_leaning=sp.party_leaning,
        related_issues=related,
    )


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

    filtered = all_rows
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

    # Per-stage counts with every filter EXCEPT stage applied: an unfiltered
    # call still describes the whole pipeline (the funnel viz), and a
    # chamber/party/q-filtered call yields the header count for every stage
    # group in one response — so the grouped bills view doesn't need a
    # separate count request per stage.
    stage_counts: dict[str, int] = {}
    for row in filtered:
        stage_counts[row.bill.stage] = stage_counts.get(row.bill.stage, 0) + 1

    if stage:
        filtered = [r for r in filtered if r.bill.stage == stage]

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
