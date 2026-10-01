"""Action Center API — serves daily civic action issues."""

import asyncio
import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import or_
from sqlalchemy.exc import OperationalError, TimeoutError as SATimeoutError
from sqlalchemy.orm import Session, selectinload

from datetime import date, timedelta

from app.config_definitions import RECENT_FEED_MAX_LIMIT, RECENT_FEED_POOL_MULTIPLIER
from app.retractions import retraction_for_issue
from app.api.admin import require_admin
from app.database import get_db, get_visits_db
from app.election_calendar import next_election_day, previous_election_day, seats_up_for_year
from app.election_phase import election_today
from app.pipeline.analyze.score_calculator import get_district_pvi_map
from app.fact_diff import new_facts_since
from app.issue_ids import from_public_id, to_public_id
from app.ordinals import ordinal
from app.pipeline.fetch.congress import expected_current_congress
from app.services.bill_record import parse_bill_id
from app.time_utils import comment_period_today, utcnow
from app.trending import compute_trending_issue_ids
from app.models import (
    ActionIssue, ActionIssueStatus, ApiCache, ExploreDocument, IssueView, MonitorStatus,
    NationalMonitor, RepSponsoredBill, SponsoredBill,
    TimelineEntry, Representative, Senator,
    WeekSummary, MonthSummary, YearSummary,
)
from app.schemas import (
    ActionIssueSchema, ActionItemSchema, RelatedBillSchema,
    RelatedExploreDoc, RelatedSenator,
    NationalMonitorSchema, NationalMonitorDetailSchema, MonitorUpdateSchema,
    TimelineEntrySchema,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/action")

# The one lifetime for /api/action/issues: nginx takes its cache lifetime
# from this header, and so does the browser's own HTTP cache — which is why
# it stays short, with no stale-while-revalidate (2026-08 incident: a
# response cached before a deploy added a field crashed the whole Action
# Center for any visitor whose BROWSER, not nginx, was still holding it).
# nginx serves the two lists stale while it refreshes them on its own
# (nginx/civitas.conf), which a browser never sees.
_ACTION_ISSUES_CACHE_TTL_S = 30


def _parse_json_field(raw: str, default: list | None = None) -> list:
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else (default or [])
    except (json.JSONDecodeError, TypeError):
        return default or []


def _renumber_for_display(issues: list[ActionIssue]) -> list[ActionIssue]:
    """Give a degraded-fallback result set consistent, gap-free, non-
    duplicate ranks — in memory only, never persisted (these rows are
    is_current=False; a real run's own renumbering pass, not a read
    request, owns the stored value).

    A row's stored `rank` is only ever kept unique WITHIN the single run
    that last touched it (_apply_matched_issue_update's renumbering pass
    operates over that run's is_current set). _latest_current_issues's
    fallback branches can return rows last touched by DIFFERENT runs that
    happen to share a `date` value — each independently had its own #1,
    #2... — so the raw stored ranks collide (confirmed live, 2026-08:
    four rows sharing rank 1 on the same date once everything had
    retired). Sorted by the stored rank first, so relative importance
    from each row's own run is preserved, then by recency as the
    tiebreaker among collisions — the more recently touched one reads as
    the "realer" #1.
    """
    ordered = sorted(
        issues,
        key=lambda i: (i.rank or 0, -(i.created_at.timestamp() if i.created_at else 0)),
    )
    for new_rank, issue in enumerate(ordered, start=1):
        issue.rank = new_rank
    return ordered


_NOT_DEVELOPING = or_(ActionIssue.status.is_(None), ActionIssue.status != ActionIssueStatus.DEVELOPING)


def _latest_issue_date(db: Session) -> str | None:
    """The newest day of CONFIRMED current issues (any current issue's, if
    there is none). Keyed to the newest date of any current row, a draft
    dated before midnight Eastern dropped off the list at the first refresh
    after it -- and one dated just after (a seat flip restamped by the
    five-minute count sync) hid every confirmed story until that refresh
    ran."""
    return (
        db.query(ActionIssue.date)
        .filter(ActionIssue.is_current == True, _NOT_DEVELOPING)  # noqa: E712
        .order_by(ActionIssue.date.desc())
        .limit(1)
        .scalar()
    ) or (
        db.query(ActionIssue.date)
        .filter(ActionIssue.is_current == True)  # noqa: E712
        .order_by(ActionIssue.date.desc())
        .limit(1)
        .scalar()
    )


def _is_iso_day(text: str) -> bool:
    """A real calendar day written YYYY-MM-DD: not "2026-1-2", not
    "2026-02-30", not a date with a trailing newline (which `$` lets
    through)."""
    if len(text) != 10 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _pager_dates(db: Session) -> list[str]:
    """Every day that holds confirmed issues, newest first: the pager's days
    and the Archive's openable ones. Not capped at the newest few: the
    Archive offers a listed day to open, and a capped list made an older day
    openable only while Today showed a day beside it. (About a year's worth
    of dates is a few kilobytes; issues are kept 14 days unless posted.)
    Confirmed issues only: a developing draft is listed beside the newest
    day, not on a day of its own, so a day holding nothing but a draft (a
    seat flip restamped past midnight before the next refresh) would page
    to a view the landing view already shows."""
    return [row[0] for row in
            db.query(ActionIssue.date).filter(_NOT_DEVELOPING).distinct().order_by(ActionIssue.date.desc())]


def _latest_current_issues(db: Session, for_date: str | None = None) -> list[ActionIssue]:
    """Return the most recent day's action issues, tolerating a wedged refresh.

    Normally this is just "issues for `for_date` (or the latest current day)
    where is_current is true." But the hourly refresh's retirement pass can
    mark every row untouched-by-this-run as not-current before it's
    confirmed anything new to replace them — if that run then wedges (a
    slow/hung LLM call with no overall time budget, 2026-07-14), is_current
    can go false table-wide for hours with nothing new inserted. The strict
    query then returns nothing even though the DB holds a perfectly good
    "current" set — the site should keep showing that rather than going
    blank. Fall back to the most recent date with ANY rows, is_current or
    not, only when the strict query comes up empty.
    """
    latest_date = _latest_issue_date(db)
    if for_date and for_date != latest_date:
        issues = (
            db.query(ActionIssue)
            .filter(ActionIssue.date == for_date, ActionIssue.is_current == True)  # noqa: E712
            .order_by(ActionIssue.rank)
            .all()
        )
        if issues:
            return issues
        # Requested date has rows but none are current (or has none at
        # all) — fall back to whatever that date has rather than nothing.
        return _renumber_for_display(
            db.query(ActionIssue)
            .filter(ActionIssue.date == for_date)
            .order_by(ActionIssue.rank)
            .all()
        )

    # The newest day (_latest_issue_date) lists every current DEVELOPING
    # draft beside it whatever its own date (it still ranks last) -- asked
    # for by date or not, so the pager's newest day is the landing view.
    if latest_date:
        return (
            db.query(ActionIssue)
            .filter(
                ActionIssue.is_current == True,  # noqa: E712
                or_(ActionIssue.date == latest_date, ActionIssue.status == ActionIssueStatus.DEVELOPING),
            )
            .order_by(ActionIssue.rank)
            .all()
        )

    # Nothing anywhere is marked current — fall back to the most recent
    # date with any data at all rather than rendering nothing.
    fallback_date = (
        db.query(ActionIssue.date)
        .order_by(ActionIssue.date.desc())
        .limit(1)
        .scalar()
    )
    if not fallback_date:
        return []
    return _renumber_for_display(
        db.query(ActionIssue)
        .filter(ActionIssue.date == fallback_date)
        .order_by(ActionIssue.rank)
        .all()
    )


def _issue_bill_ids(issue: ActionIssue) -> set[str]:
    """Bill IDs (e.g. "HR.22") referenced by an issue's related_bill_ids JSON."""
    return {
        b["id"].upper()
        for b in _parse_json_field(issue.related_bill_ids)
        if isinstance(b, dict) and b.get("id")
    }


def _internal_bill_congresses(db: Session, bill_ids: set[str]) -> dict[str, set[int]]:
    """{bill_id: {congress, ...}} for bills the site holds its own record
    of (current members' sponsored bills, either chamber — bill_service.
    get_bill_detail's lookup): which Congress an entry that never recorded
    one refers to, when it can be told at all."""
    if not bill_ids:
        return {}
    found: dict[str, set[int]] = {}
    senate_rows = (
        db.query(SponsoredBill.bill_id, SponsoredBill.congress)
        .join(Senator, SponsoredBill.senator_id == Senator.id)
        .filter(SponsoredBill.bill_id.in_(bill_ids))
        .filter(Senator.is_current == True)  # noqa: E712
        .all()
    )
    house_rows = (
        db.query(RepSponsoredBill.bill_id, RepSponsoredBill.congress)
        .join(Representative, RepSponsoredBill.representative_id == Representative.id)
        .filter(RepSponsoredBill.bill_id.in_(bill_ids))
        .filter(Representative.is_current == True)  # noqa: E712
        .all()
    )
    for bill_id, congress in senate_rows + house_rows:
        found.setdefault(bill_id, set()).add(congress)
    return found


def _build_issue_response(
    issue: ActionIssue, db: Session,
    explore_docs_map: dict[int, ExploreDocument] | None = None,
    internal_bills: dict[str, set[int]] | None = None,
    is_trending: bool = False,
) -> dict:
    explore_ids = _parse_json_field(issue.related_explore_ids)
    related_docs: list[dict] = []
    if explore_ids:
        if explore_docs_map is not None:
            docs = [explore_docs_map[eid] for eid in explore_ids if eid in explore_docs_map]
        else:
            docs = (
                db.query(ExploreDocument)
                .filter(ExploreDocument.id.in_(explore_ids))
                .all()
            )
        related_docs = [
            RelatedExploreDoc(
                id=d.id, title=d.title, doc_type=d.doc_type,
                date=d.date, url=d.url,
                comment_url=d.comment_url or None,
                comments_close_on=d.comments_close_on or None,
            ).model_dump(by_alias=True)
            for d in docs
        ]

    senator_data = _parse_json_field(getattr(issue, "related_senators", "[]"))
    related_senators = [
        RelatedSenator(**s).model_dump(by_alias=True)
        for s in senator_data if isinstance(s, dict) and s.get("id")
    ]

    raw_actions = _parse_json_field(issue.actions)
    action_items: list[dict] = []
    for a in raw_actions:
        if isinstance(a, dict) and "text" in a:
            action_items.append(
                ActionItemSchema(
                    text=a["text"],
                    type=a.get("type", "general"),
                    url=a.get("url"),
                ).model_dump(by_alias=True)
            )
        elif isinstance(a, str):
            action_items.append(
                ActionItemSchema(text=a, type="general").model_dump(by_alias=True)
            )

    raw_bills = _parse_json_field(issue.related_bill_ids)
    if internal_bills is None:
        internal_bills = _internal_bill_congresses(db, _issue_bill_ids(issue))
    current_congress = expected_current_congress()
    related_bills: list[dict] = []
    for b in raw_bills:
        if isinstance(b, dict) and b.get("id") and b.get("url"):
            bill_id = b["id"].upper()
            # The site's bill page shows any bill of any Congress that has
            # convened, named by ?congress= (its record comes from
            # Congress.gov on demand). A bill number alone names a different
            # bill in each Congress, so the link always says which: the
            # entry's own, or for an entry that never recorded one, the
            # newest Congress we hold the bill from (and no link when we
            # hold it from none).
            entry_congress = b.get("congress")
            internal_congresses = internal_bills.get(bill_id)
            if isinstance(entry_congress, int) and entry_congress <= current_congress:
                link_congress = entry_congress if parse_bill_id(bill_id) is not None else None
            elif entry_congress is None and internal_congresses:
                link_congress = max(internal_congresses)
            else:
                link_congress = None
            related_bills.append(
                RelatedBillSchema(
                    name=b.get("name", b["id"]),
                    id=b["id"],
                    url=b["url"],
                    internal_url=(
                        f"/congress/bills/{bill_id}?congress={link_congress}" if link_congress else None
                    ),
                ).model_dump(by_alias=True)
            )

    monitor_slugs = _parse_json_field(
        getattr(issue, "related_monitor_slugs", "[]")
    )

    current_facts = _parse_json_field(issue.facts)
    previous_facts = _parse_json_field(getattr(issue, "previous_facts", "[]"))
    # Empty previous_facts means never-updated, not "everything changed" —
    # marking a brand-new issue's own facts as "new" would be true but
    # meaningless (of course they are, the issue just appeared).
    new_facts = new_facts_since(current_facts, previous_facts) if previous_facts else []

    # Stamped on the issue with its facts (live_results/signals.py), never
    # read from the live count row: a held poll moves that row's time and
    # official flag without rewriting the issue's figures.
    count_as_of = count_official = None
    if (getattr(issue, "source_type", None) == _ELECTION_RESULTS_SOURCE
            and (getattr(issue, "status", None) or "confirmed") == ActionIssueStatus.DEVELOPING
            and getattr(issue, "count_as_of", None) is not None):
        count_as_of = issue.count_as_of.isoformat() + "Z"
        count_official = bool(issue.count_official)

    return ActionIssueSchema(
        id=issue.id,
        public_id=to_public_id(issue.id),
        first_surfaced=issue.created_at.strftime("%Y-%m-%d"),
        date=issue.date,
        rank=issue.rank,
        title=issue.title,
        summary=issue.summary,
        facts=current_facts,
        fact_sources=_parse_json_field(getattr(issue, "fact_sources", "[]")),
        fact_source_urls=_parse_json_field(getattr(issue, "fact_source_urls", None) or "[]"),
        summary_source=getattr(issue, "summary_source", None),
        summary_source_url=getattr(issue, "summary_source_url", None),
        new_facts=new_facts,
        actions=action_items,
        source_urls=_parse_json_field(issue.source_urls),
        source_names=_parse_json_field(issue.source_names),
        policy_areas=_parse_json_field(issue.policy_areas),
        related_bills=related_bills,
        related_explore_docs=related_docs,
        related_senators=related_senators,
        related_monitor_slugs=monitor_slugs,
        full_story=getattr(issue, "full_story", None),
        is_trending=is_trending,
        status=getattr(issue, "status", None) or "confirmed",
        source_type=getattr(issue, "source_type", None),
        count_as_of=count_as_of,
        count_official=count_official,
        image_url=getattr(issue, "image_url", None),
        image_alt=getattr(issue, "image_alt", "") or "",
        image_credit=getattr(issue, "image_credit", "") or "",
    ).model_dump(by_alias=True)


def _trending_ids_for(issues: list[ActionIssue], db_visits: Session) -> set[str]:
    """Which of these issues' public_ids clear app/trending.py's traction
    bar, judged against TODAY's view counts only — a trending badge is
    about current momentum, not all-time popularity. IssueView lives in
    the separate visits database (see models.py), so this is its own
    query rather than a join.

    get_visits_db has no error handling of its own — its docstring says
    it's for the read-only admin visitor-stats endpoints, where a failure
    is low-stakes. This is the first non-admin caller, and /action/issues
    is one of the most-hit routes on the site: a locked or briefly
    unavailable visits DB (this codebase has a documented 2026-07 incident
    of exactly that, under sustained write load from track_visit's own
    consumer) must not take down issue listing over a badge. Best-effort,
    matching track_visit's own write path — degrade to "nothing trends"
    rather than propagate.
    """
    public_ids = {to_public_id(i.id) for i in issues}
    today = utcnow().date().isoformat()
    try:
        rows = (
            db_visits.query(IssueView.issue_public_id, IssueView.count)
            .filter(IssueView.date == today, IssueView.issue_public_id.in_(public_ids))
            .all()
        )
    except (OperationalError, SATimeoutError):
        logger.warning("Trending lookup failed (visits DB unavailable) — no issues flagged trending")
        return set()
    view_counts = {pid: count for pid, count in rows}
    return compute_trending_issue_ids(view_counts)


# Runs to look back through for one that wrote issues: two days of hourly
# refreshes. Past that the line is omitted rather than naming a stale time.
_REFRESH_LOOKBACK_RUNS = 48


def _last_refresh_with_issues(db: Session) -> str | None:
    """ISO time (UTC, "Z") of the newest Action Center run that published or
    re-matched at least one issue, or None if none did recently."""
    rows = (
        db.query(ApiCache.cached_at, ApiCache.data_json)
        .filter(ApiCache.tier == "action-metrics")
        .order_by(ApiCache.cached_at.desc())
        .limit(_REFRESH_LOOKBACK_RUNS)
        .all()
    )
    for cached_at, data_json in rows:
        try:
            counts = json.loads(data_json).get("counts", {})
        except (ValueError, TypeError, AttributeError):
            continue
        if counts.get("issues_new_topic", 0) + counts.get("issues_matched_existing", 0) > 0:
            return cached_at.isoformat() + "Z"
    return None


@router.get("/issues")
async def get_action_issues(
    response: Response,
    date: str | None = Query(None, description="Date in YYYY-MM-DD format; defaults to most recent"),
    db: Session = Depends(get_db),
    db_visits: Session = Depends(get_visits_db),
):
    """Return the current day's action issues (or most recent available)."""
    response.headers["Cache-Control"] = f"public, max-age={_ACTION_ISSUES_CACHE_TTL_S}"
    issues = _latest_current_issues(db, for_date=date)

    # When the live view was last refreshed: the newest run that wrote
    # issues. Every run leaves an action-metrics row (action_metrics.py),
    # aborted ones included, so the newest row alone would read "updated
    # just now" over issues hours old exactly when the feeds are down.
    # Only for the live view; a past day's page is not "updated".
    generated_at = _last_refresh_with_issues(db) if date is None else None

    if not issues:
        # A day whose issues all moved on (a re-matched issue is restamped
        # to the day that matched it) still gets a pager, so a reader who
        # followed a link to it can page to the days either side.
        return {
            "date": date, "issues": [],
            "availableDates": _pager_dates(db) if date and _is_iso_day(date) else [],
            "generatedAt": generated_at,
        }

    issue_date = date or _latest_issue_date(db) or issues[0].date
    available_dates = _pager_dates(db)

    all_explore_ids: list[int] = []
    for i in issues:
        all_explore_ids.extend(_parse_json_field(i.related_explore_ids))
    explore_docs_map: dict[int, ExploreDocument] = {}
    if all_explore_ids:
        docs = (
            db.query(ExploreDocument)
            .filter(ExploreDocument.id.in_(set(all_explore_ids)))
            .all()
        )
        explore_docs_map = {d.id: d for d in docs}

    all_bill_ids: set[str] = set()
    for i in issues:
        all_bill_ids |= _issue_bill_ids(i)
    internal_bills = _internal_bill_congresses(db, all_bill_ids)

    trending_ids = _trending_ids_for(issues, db_visits)

    return {
        "date": issue_date,
        "issues": [
            _build_issue_response(
                i, db, explore_docs_map, internal_bills,
                is_trending=to_public_id(i.id) in trending_ids,
            )
            for i in issues
        ],
        "availableDates": available_dates,
        "generatedAt": generated_at,
    }


_RECENT_ISSUES_DEFAULT_LIMIT = 10
_RECENT_ISSUES_MAX_LIMIT = RECENT_FEED_MAX_LIMIT


@router.get("/issues/recent")
async def get_recent_action_issues(
    response: Response,
    limit: int = Query(_RECENT_ISSUES_DEFAULT_LIMIT, ge=1, le=_RECENT_ISSUES_MAX_LIMIT),
    db: Session = Depends(get_db),
):
    """The most recently touched issues, regardless of is_current.

    Backs the homepage's "record index" — a permanent-feeling ledger of
    recent activity — which used to call the same endpoint as the Action
    Center page and inherited its is_current filter. That reads fine for
    the Action Center itself (today's live board), but it meant an issue
    vanished from the homepage's own "recent record" the instant it
    retired, sometimes shrinking a day's worth of entries to one within
    24 hours (2026-08-22 report: "we're saying we're collecting a record
    but records seem to disappear"). Ordered by `date` (bumped on every
    real update, same signal the homepage already sorts by) rather than
    `created_at`, so a still-developing story keeps its place near the
    top and a stopped one just sinks — same recency the reader already
    sees, without a retired row dropping out of it entirely.

    Route must be declared before /issues/{issue_id} — FastAPI matches
    path routes in declaration order, and {issue_id} would otherwise
    swallow "recent" as a path parameter.

    Near-identical duplicates are left out: retiring a row for BEING a
    duplicate only flips is_current, which this query ignores by design —
    without this, a duplicate retired off the Action Center resurfaced
    right back here (2026-08-22 report: "I see 3 copies of the beef import
    issue on the homepage"). Which rows are duplicates is decided by the
    hourly refresh (action_center.mark_recent_duplicates), over the same
    pool this reads from: it was decided here, per request, which ran the
    embedding model on a public GET inside the event loop.
    """
    response.headers["Cache-Control"] = f"public, max-age={_ACTION_ISSUES_CACHE_TTL_S}"
    pool = (
        db.query(ActionIssue)
        .order_by(ActionIssue.date.desc(), ActionIssue.rank.asc())
        .limit(RECENT_FEED_MAX_LIMIT * RECENT_FEED_POOL_MULTIPLIER)
        .all()
    )
    by_id = {p.id: p for p in pool}
    issues = [i for i in pool if not _hidden_as_duplicate(i, by_id)][:limit]
    return {"issues": [_build_issue_response(i, db) for i in issues]}


# ActionIssue.source_type of an election-night seat-flip issue
# (live_results/signals.SOURCE_TYPE; not imported, to keep this module's
# import graph clear of the live-results package's).
_ELECTION_RESULTS_SOURCE = "election_results"


def _hidden_as_duplicate(issue: ActionIssue, pool_by_id: dict[int, ActionIssue]) -> bool:
    """Whether the homepage leaves a row out as a duplicate. Seat-flip
    issues are exempt from being hidden behind ANOTHER seat-flip issue: one
    race's count each, their titles differ only by the district ("…
    Georgia's 2nd …" / "… Georgia's 6th …") and they share the state's
    results page as their source, so the refresh's duplicate pass reads
    every flip in a state as one story and kept only the newest. Behind a
    news story about the same flip, one still gives way."""
    if issue.duplicate_of_id is None:
        return False
    if issue.source_type != _ELECTION_RESULTS_SOURCE:
        return True
    kept = pool_by_id.get(issue.duplicate_of_id)
    return kept is not None and kept.source_type != _ELECTION_RESULTS_SOURCE


@router.get("/issues/{issue_id}")
async def get_action_issue(issue_id: str, response: Response, db: Session = Depends(get_db)):
    """Return a single action issue by its public id (used for OG metadata /
    deep-link previews).

    Falls back to the legacy numeric id when the path segment is all
    digits: every share link published before public ids existed points at
    the raw autoincrement id, and `from_public_id` only ever accepts its own
    letter-prefixed format, so the two paths can't collide.
    """
    response.headers["Cache-Control"] = f"public, max-age={_ACTION_ISSUES_CACHE_TTL_S}"
    resolved_id = from_public_id(issue_id)
    if resolved_id is None and issue_id.isdigit():
        # SQLite's INTEGER column caps at a signed 8-byte int; a longer
        # digit string (a bot, a mistyped URL) isn't a ROWID that could
        # ever exist, and binding it as a query parameter overflows and
        # raises rather than just missing — a real id never gets close to
        # this bound, so treat anything past it as "no such issue".
        candidate = int(issue_id)
        resolved_id = candidate if candidate <= 2**63 - 1 else None
    issue = (
        db.query(ActionIssue).filter(ActionIssue.id == resolved_id).first()
        if resolved_id is not None else None
    )
    if not issue:
        from fastapi import HTTPException
        retracted = retraction_for_issue(issue_id)
        if retracted:
            # 410, with the reason: a link someone saw in a withdrawn post
            # explains itself instead of reading as a broken page.
            raise HTTPException(status_code=410, detail={"retracted": True, **retracted})
        raise HTTPException(status_code=404, detail="Issue not found")
    # Pass None, not {} — an empty map is a *populated* prefetch that happens to
    # contain nothing, so _build_issue_response would resolve every related
    # explore id to a miss and return no documents at all. None tells it to look
    # them up itself. The list endpoint prefetches across all issues to avoid an
    # N+1; a single issue has nothing to batch.
    return _build_issue_response(issue, db, None)


@router.get("/open-comments")
def get_open_comments(response: Response, db: Session = Depends(get_db)):
    """Return Federal Register documents with open public comment periods, sorted by deadline."""
    # Comment-period deadlines move in days, not minutes — an hour of
    # staleness has no real effect on this list.
    response.headers["Cache-Control"] = "public, max-age=3600"
    today = comment_period_today()
    docs = (
        db.query(ExploreDocument)
        .filter(
            ExploreDocument.comment_url.isnot(None),
            ExploreDocument.comment_url != "",
            ExploreDocument.comments_close_on.isnot(None),
            ExploreDocument.comments_close_on >= today,
        )
        .order_by(ExploreDocument.comments_close_on)
        .limit(8)
        .all()
    )
    result = []
    for d in docs:
        try:
            areas = json.loads(d.policy_areas) if d.policy_areas else []
        except Exception:
            areas = []
        result.append({
            "id": d.id,
            "title": d.title,
            "agencyName": d.agency_name,
            "commentsCloseOn": d.comments_close_on,
            "commentUrl": d.comment_url,
            "policyAreas": areas,
            "docType": d.doc_type,
            "date": d.date,
            "summary": (d.summary or "")[:200],
        })
    return result


@router.post("/refresh", dependencies=[Depends(require_admin)])
async def refresh_action_center(db: Session = Depends(get_db)):
    """Trigger an action center refresh (admin only). Runs in background."""
    from app.pipeline.analyze.action_center import refresh_action_issues

    def _run():
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(asyncio.sleep(0))
            refresh_action_issues()
        except Exception:
            logger.exception("Action center refresh failed")
        finally:
            loop.close()

    from app.background import start_writer

    start_writer(_run, name="action-refresh")
    return {"message": "Action center refresh triggered"}


def _monitor_to_schema(m: NationalMonitor, include_updates: bool = False):
    """Convert a NationalMonitor ORM object to a schema dict."""
    base = NationalMonitorSchema(
        id=m.id,
        slug=m.slug,
        title=m.title,
        description=m.description,
        category=m.category,
        status=m.status,
        policy_areas=_parse_json_field(m.policy_areas),
        created_at=m.created_at.strftime("%Y-%m-%d"),
        updated_at=m.updated_at.strftime("%Y-%m-%d"),
        last_article_date=m.last_article_date,
        update_count=len(m.updates) if m.updates else 0,
    )
    if not include_updates:
        return base.model_dump(by_alias=True)

    updates = [
        MonitorUpdateSchema(
            id=u.id,
            date=u.date,
            summary=u.summary,
            source_url=u.source_url,
            source_name=u.source_name,
            article_title=u.article_title,
            created_at=u.created_at.isoformat() + "Z" if u.created_at else "",
        ).model_dump(by_alias=True)
        for u in (m.updates or [])
    ]
    detail = NationalMonitorDetailSchema(
        **{k: getattr(base, k) for k in base.model_fields},
        updates=updates,
    )
    return detail.model_dump(by_alias=True)


@router.get("/monitors")
async def list_monitors(response: Response, db: Session = Depends(get_db)):
    """List all active and watching national monitors."""
    # Monitor creation/status changes on the pipeline's hourly cadence, not
    # continuously — matches the other Action Center list endpoints.
    response.headers["Cache-Control"] = "public, max-age=300"
    monitors = (
        db.query(NationalMonitor)
        .options(selectinload(NationalMonitor.updates))
        .filter(NationalMonitor.status.in_([MonitorStatus.ACTIVE, MonitorStatus.WATCHING]))
        .order_by(NationalMonitor.updated_at.desc())
        .all()
    )
    return {"monitors": [_monitor_to_schema(m) for m in monitors]}


@router.get("/monitors/{slug}")
async def get_monitor(response: Response, slug: str, db: Session = Depends(get_db)):
    """Get full detail for a national monitor including timeline."""
    # Same cadence as the monitors list above — a single monitor's
    # timeline only grows on the same hourly refresh.
    response.headers["Cache-Control"] = "public, max-age=300"
    monitor = (
        db.query(NationalMonitor)
        .options(selectinload(NationalMonitor.updates))
        .filter(NationalMonitor.slug == slug)
        .first()
    )
    if not monitor:
        raise HTTPException(status_code=404, detail="Monitor not found")
    return _monitor_to_schema(monitor, include_updates=True)


_MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _series(parts: list[str]) -> str:
    """"a, b and c"."""
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _upcoming_civic_events(year: int, today: date) -> list[dict]:
    """Return known upcoming civic events for the given year."""
    events: list[dict] = []

    # On the day itself too: next_election_day is strictly after `today`,
    # which dropped election day from the calendar on election day.
    election_day = today if previous_election_day(today) == today else next_election_day(today)
    if election_day.year == year and election_day >= today:
        is_presidential = year % 4 == 0
        label = "Presidential & Congressional" if is_presidential else "Midterm Congressional"
        # Counted, not typed: the House's seats from its apportionment (the
        # district table the Clerk's list builds), the Senate's from the
        # class up this year (the Senate's own list). Specials are extra.
        house_seats = len(get_district_pvi_map())
        senate_seats = len(seats_up_for_year(year))
        seats = [f"all {house_seats} House seats" if house_seats else "every House seat"]
        if senate_seats:
            seats.append(f"{senate_seats} Senate seats")
        if is_presidential:
            seats.append("the presidency")
        events.append({
            "date": election_day.isoformat(),
            "title": f"{label} Election Day",
            "description": f"Federal election day: {_series(seats)} are on the ballot.",
            "category": "election",
            "link": "/elections",
            "linkLabel": "View races & state info",
        })

    # The first Monday in October (28 U.S.C. § 2). This was October 7th
    # moved off a weekend, which is the first Monday only when the 7th is one:
    # 2026's term was dated Wednesday the 7th, not Monday the 5th.
    october_first = date(year, 10, 1)
    scotus_term_start = october_first + timedelta(days=(0 - october_first.weekday()) % 7)
    if scotus_term_start >= today and scotus_term_start.year == year:
        events.append({
            "date": scotus_term_start.isoformat(),
            "title": f"Supreme Court {year}-{year + 1} Term Begins",
            "description": "The Supreme Court begins its new term on the first Monday in October,"
                           " hearing oral arguments and issuing opinions through June.",
            "category": "scotus",
            "link": "/politicians?branch=scotus",
            "linkLabel": "View justice scorecards",
        })

    if year % 2 == 1:
        jan3 = date(year, 1, 3)
        if jan3.weekday() == 6:
            jan3 = date(year, 1, 4)
        if jan3 >= today:
            events.append({
                "date": jan3.isoformat(),
                "title": f"{ordinal((year - 1789) // 2 + 1)} Congress Convenes",
                "description": "New session of Congress begins. Newly elected members are sworn in"
                               " and leadership elections take place.",
                "category": "congress",
                "link": "/leaderboard",
                "linkLabel": "View congressional rankings",
            })

    if year % 4 == 1:
        jan20 = date(year, 1, 20)
        if jan20.weekday() == 6:
            jan20 = date(year, 1, 21)
        if jan20 >= today and jan20.year == year:
            events.append({
                "date": jan20.isoformat(),
                "title": "Presidential Inauguration Day",
                "description": "The president-elect is sworn into office at the U.S. Capitol.",
                "category": "executive",
                "link": "/politicians?branch=president",
                "linkLabel": "View presidential scorecards",
            })

    return sorted(events, key=lambda e: e["date"])


@router.get("/timeline")
async def get_timeline(
    response: Response,
    # Bounded: the handler builds calendar dates from it, and date() refuses
    # a year outside 1-9999 with a 500 rather than a 422.
    year: int | None = Query(None, ge=1900, le=2100, description="Year (defaults to current)"),
    db: Session = Depends(get_db),
):
    """Return the year's timeline with hierarchical week/month/year structure."""
    # Same reasoning as the other Action Center aggregate views above.
    response.headers["Cache-Control"] = "public, max-age=300"
    if year is None:
        year = utcnow().date().year

    today = utcnow().date()
    current_year = today.year
    current_month = today.month if year == current_year else 12
    current_week_num = today.isocalendar()[1] if year == current_year else 0

    # All daily entries for the year
    entries = (
        db.query(TimelineEntry)
        .filter(TimelineEntry.date >= f"{year}-01-01", TimelineEntry.date <= f"{year}-12-31")
        .order_by(TimelineEntry.date.desc())
        .all()
    )

    # Week summaries for the year
    week_summaries = {
        ws.week_num: ws
        for ws in db.query(WeekSummary).filter(WeekSummary.year == year).all()
    }

    # Month summaries for the year
    month_summaries = {
        ms.month: ms
        for ms in db.query(MonthSummary).filter(MonthSummary.year == year).all()
    }

    # Year summary (for completed year)
    year_summary_row = db.query(YearSummary).filter(YearSummary.year == year).first()

    # Group entries by month and week
    entries_by_month: dict[int, list] = {}
    theme_counts: dict[str, int] = {}
    for e in entries:
        mnum = int(e.date[5:7])
        entries_by_month.setdefault(mnum, []).append(e)
        for area in _parse_json_field(e.policy_areas):
            theme_counts[area] = theme_counts.get(area, 0) + 1

    def _entry_dict(e) -> dict:
        return TimelineEntrySchema(
            date=e.date,
            title=e.title,
            summary=e.summary,
            policy_areas=_parse_json_field(e.policy_areas),
            source_url=e.source_url,
            source_name=e.source_name,
            monitor_slug=e.monitor_slug,
        ).model_dump(by_alias=True)

    monthly_data = []
    # Process months in descending order
    for m_num in range(12, 0, -1):
        m_entries = entries_by_month.get(m_num, [])
        if not m_entries:
            continue

        m_themes: dict[str, int] = {}
        for e in m_entries:
            for area in _parse_json_field(e.policy_areas):
                m_themes[area] = m_themes.get(area, 0) + 1
        top_themes = sorted(m_themes.items(), key=lambda x: -x[1])[:5]

        # Build week breakdown for this month
        weeks_in_month: dict[int, list] = {}
        for e in m_entries:
            d = date.fromisoformat(e.date)
            wnum = d.isocalendar()[1]
            weeks_in_month.setdefault(wnum, []).append(e)

        weeks_data = []
        # Sort weeks in descending order
        for wnum in sorted(weeks_in_month.keys(), reverse=True):
            w_entries = weeks_in_month[wnum]
            first_d = date.fromisoformat(w_entries[0].date)
            monday = first_d - timedelta(days=first_d.weekday())
            sunday = monday + timedelta(days=6)
            ws = week_summaries.get(wnum)
            weeks_data.append({
                "weekNum": wnum,
                "startDate": monday.isoformat(),
                "endDate": sunday.isoformat(),
                "isCurrent": wnum == current_week_num and m_num == current_month,
                "summary": ws.summary if ws else None,
                "topAreas": _parse_json_field(ws.top_policy_areas) if ws else [t[0] for t in top_themes[:3]],
                "entryCount": len(w_entries),
                "entries": [_entry_dict(e) for e in w_entries],
            })

        ms = month_summaries.get(m_num)
        monthly_data.append({
            "month": m_num,
            "name": _MONTH_NAMES[m_num],
            "isCurrent": m_num == current_month,
            "summary": ms.summary if ms else None,
            "topAreas": _parse_json_field(ms.top_policy_areas) if ms else [t[0] for t in top_themes[:5]],
            "entries": [_entry_dict(e) for e in m_entries],
            "weeks": weeks_data,
            "topThemes": top_themes,
        })

    sorted_themes = sorted(theme_counts.items(), key=lambda x: -x[1])
    top_themes_all = [{"area": a, "count": c} for a, c in sorted_themes[:10]]

    monitors = (
        db.query(NationalMonitor)
        .options(selectinload(NationalMonitor.updates))
        .filter(NationalMonitor.created_at >= f"{year}-01-01")
        .order_by(NationalMonitor.last_article_date.desc())
        .all()
    )
    active_monitors = [
        {"slug": m.slug, "title": m.title, "status": m.status,
         "updateCount": len(m.updates) if m.updates else 0}
        for m in monitors
    ]

    # The civic calendar's day is the Eastern one: from 7 PM ET on
    # election day the UTC date is already tomorrow, which dropped the
    # election from the calendar for the whole of election night.
    civic_events = _upcoming_civic_events(year, election_today())

    return {
        "year": year,
        "totalDays": len(entries),
        "currentMonth": current_month,
        "currentWeekNum": current_week_num,
        "topThemes": top_themes_all,
        "monitors": active_monitors,
        "months": monthly_data,
        "upcomingEvents": civic_events,
        "yearSummary": {
            "summary": year_summary_row.summary,
            "topAreas": _parse_json_field(year_summary_row.top_policy_areas),
            "entryCount": year_summary_row.entry_count,
        } if year_summary_row else None,
    }
