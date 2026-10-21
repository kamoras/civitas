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

from app import comment_periods
from app.config_definitions import BILL_STAGES, RECENT_FEED_MAX_LIMIT, RECENT_FEED_POOL_MULTIPLIER
from app.retractions import retraction_for_issue
from app.roll_call_links import parse_ref, stored_refs
from app.api.admin import require_admin
from app.database import get_db, get_visits_db
from app.election_calendar import next_election_day, previous_election_day, seats_up_for_year
from app.ballot_links import issue_ballot
from app.election_phase import active_election, election_today
from app.pipeline.analyze.score_calculator import compute_overall_score, get_district_pvi_map
from app.issue_ids import from_public_id, to_public_id
from app.ordinals import ordinal
from app.photos import issue_photo
from app.pipeline.fetch.congress import congress_gov_url_for_bill_id, expected_current_congress
from app.pipeline.transform.normalize_votes import is_housekeeping
from app.services.bill_record import MemberLinks, parse_bill_id
from app.services.congress_service import bill_label
from app.state_names import STATE_NAMES
from app.time_utils import comment_period_today, eastern_date, utcnow
from app.timeline_entries import without_repeat_leads
from app.trending import compute_trending_issue_ids
from app.models import (
    ActionIssue, ActionIssueStatus, ApiCache, ExploreDocument, IssueView, MonitorStatus,
    MonitorUpdate, NationalMonitor, RepSponsoredBill, RollCall, RollCallPosition, SponsoredBill,
    TimelineEntry, Representative, Senator,
    WeekSummary, MonthSummary, YearSummary,
)
from app.schemas import (
    ActionIssueSchema, ActionItemSchema, CommitteeContactSchema, CommitteeLeaderSchema, DeciderSchema,
    IssueMemberSchema, IssueMembersSchema, IssueVoteSchema, RelatedBillSchema,
    RelatedExploreDoc, RelatedSenator,
    NationalMonitorSchema, NationalMonitorDetailSchema, MonitorUpdateSchema,
    TimelineEntrySchema,
)
from app.services.explore_keys import issue_doc_ids, public_key, resolve_keys

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
# Not a developing draft taken off the Action Center: retired unconfirmed,
# it never had coverage behind it; retired covered, the record lists the
# reporting that covered it instead (as mark_recent_duplicates already
# did). Retired confirmed stories stay part of the record.
NOT_RETIRED_DRAFT = or_(_NOT_DEVELOPING, ActionIssue.is_current.is_(True))


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


def _issue_member_ids(issue: ActionIssue) -> set[str]:
    """Member ids named in an issue's related_senators JSON."""
    return {
        s["id"] for s in _parse_json_field(getattr(issue, "related_senators", "[]"))
        if isinstance(s, dict) and s.get("id")
    }


def _current_member_facts(db: Session, member_ids: set[str]) -> dict[str, dict]:
    """{member_id: name/state/party/overall_score as the scorecard shows them
    now}. related_senators is written once, when the issue is built, and
    issues stay readable for good; serving its stored score showed an
    archived issue's members at whatever they scored that day — a
    different whole number from the scorecard the entry links to for 195
    of 203 entries (2026-10-09)."""
    if not member_ids:
        return {}
    facts: dict[str, dict] = {}
    for model in (Senator, Representative):
        for m in db.query(model).filter(model.id.in_(member_ids)).all():
            facts[m.id] = {
                "name": m.name, "state": m.state, "party": m.party,
                "overall_score": compute_overall_score(m),
                "office_phone": m.office_phone or None,
            }
    return facts


# How the stored rosters (fetch/committee_leadership.py) title a committee's
# chair and ranking member: congress-legislators writes "Chairman" or
# "Chair" by committee.
_CHAIR_TITLES = {"Chair", "Chairman"}
_RANKING_TITLES = {"Ranking Member"}


def _committee_leaders(db: Session, names: set[str]) -> dict[str, dict]:
    """{committee name: {"chair", "ranking_member"}} from the sitting
    members' stored committee assignments, as they stand now."""
    if not names:
        return {}
    out: dict[str, dict] = {name: {} for name in names}
    for model, chamber in ((Senator, "senate"), (Representative, "house")):
        rows = db.query(model).filter(
            model.is_current.is_(True),
            or_(*[model.committees.contains(name) for name in names]),
        ).all()
        for m in rows:
            for assignment in _parse_json_field(m.committees):
                name = assignment.get("committeeName") if isinstance(assignment, dict) else None
                if name not in out:
                    continue
                title = assignment.get("title")
                role = ("chair" if title in _CHAIR_TITLES
                        else "ranking_member" if title in _RANKING_TITLES else None)
                if role and role not in out[name]:
                    out[name][role] = CommitteeLeaderSchema(
                        id=m.id, name=m.name, party=m.party, state=m.state, chamber=chamber)
    return out


# Senate-side bill types; every other site prefix is the House's.
_SENATE_PREFIXES = {"S", "SJRES", "SCONRES", "SRES"}
# Joint resolutions and bills go to the President; concurrent resolutions
# need both chambers but no signature; simple resolutions stay in one.
_SIGNED_PREFIXES = {"HR", "S", "HJRES", "SJRES"}
_ONE_CHAMBER_PREFIXES = {"HRES", "SRES"}


def bill_decider(bill_id: str, stage: str) -> str | None:
    """Who acts next on a bill at `stage` (config_definitions.BILL_STAGES),
    from the bill's type and stage alone, or None once nobody does (enacted,
    or a stage this doesn't know)."""
    prefix = bill_id.split(".", 1)[0].upper()
    origin, other = ("Senate", "House") if prefix in _SENATE_PREFIXES else ("House", "Senate")
    if stage in ("INTRODUCED", "REFERRED", "IN_COMMITTEE"):
        return f"A {origin} committee"
    if stage in ("REPORTED", "ON_FLOOR"):
        return f"The full {origin}"
    if prefix in _ONE_CHAMBER_PREFIXES:
        return None
    if stage in ("PASSED_CHAMBER", "IN_OTHER_CHAMBER"):
        return f"The {other}"
    if prefix not in _SIGNED_PREFIXES:
        return None
    if stage == "PASSED_BOTH":
        return "Congress, then the President"
    if stage == "TO_PRESIDENT":
        return "The President"
    return None


def _bill_records(db: Session, wanted: set[tuple[str, int]]) -> dict[tuple[str, int], tuple[str, str, str]]:
    """{(bill id, congress): (stage, latest action, its date)} for bills the
    site holds a sponsor's record of."""
    if not wanted:
        return {}
    out: dict[tuple[str, int], tuple[str, str, str]] = {}
    ids = {b for b, _ in wanted}
    for model in (SponsoredBill, RepSponsoredBill):
        for bid, congress, stage, action, when in db.query(
            model.bill_id, model.congress, model.stage, model.latest_action, model.latest_action_date,
        ).filter(model.bill_id.in_(ids)):
            if (bid, congress) in wanted and (bid, congress) not in out:
                out[(bid, congress)] = (stage or "", action or "", when or "")
    return out


# The newest roll calls on an issue's bills that it lists: a bill can
# collect dozens of amendment votes.
_ISSUE_VOTES_MAX = 10


def _ref_clauses(refs: list[str]) -> list:
    out = []
    for ref in refs:
        if parsed := parse_ref(ref):
            chamber, congress, session, number = parsed
            out.append((RollCall.chamber == chamber) & (RollCall.congress == congress)
                       & (RollCall.session == session) & (RollCall.number == number))
    return out


def issue_roll_calls(db: Session, issue: ActionIssue) -> list[RollCall]:
    """The roll calls an issue is about: the vote a roll-call issue reports
    (its primary source is the vote's own record), the stored votes on a
    bill its coverage names, in that bill's Congress, and the votes matched
    to the story (app/roll_call_links.py). Housekeeping questions (quorum,
    adjourning, tabling) are left out, as everywhere votes are compared
    (normalize_votes.is_housekeeping)."""
    clauses = _ref_clauses(stored_refs(getattr(issue, "related_roll_call_refs", None)))
    if issue.primary_source_url:
        clauses.append(RollCall.source_url == issue.primary_source_url)
    for b in _parse_json_field(issue.related_bill_ids):
        if isinstance(b, dict) and b.get("id") and isinstance(b.get("congress"), int):
            clauses.append((RollCall.bill_id == b["id"].upper()) & (RollCall.congress == b["congress"]))
    if not clauses:
        return []
    rows = (
        db.query(RollCall).filter(or_(*clauses))
        .order_by(RollCall.date.desc(), RollCall.number.desc())
        .all()
    )
    return [rc for rc in rows if not is_housekeeping(rc.question, rc.chamber)][:_ISSUE_VOTES_MAX]


def vote_schema(rc: RollCall) -> dict:
    return IssueVoteSchema(
        chamber=rc.chamber, congress=rc.congress, session=rc.session, number=rc.number,
        date=rc.date, question=rc.question or "", result=rc.result or "",
        bill_id=rc.bill_id, bill_label=bill_label(rc.bill_id),
        title=rc.title or "", yeas=rc.yeas or 0, nays=rc.nays or 0, rejected=rc.rejected,
        margin=(rc.yeas or 0) - (rc.nays or 0),
    ).model_dump(by_alias=True)


def _month_day(iso_day: str) -> str:
    day = date.fromisoformat(iso_day)
    return f"{day.strftime('%B')} {day.day}"


def _roll_call_ref(rc: RollCall) -> str:
    return f"{rc.chamber}-{rc.congress}-{rc.session}-{rc.number}"


def _build_issue_response(
    issue: ActionIssue, db: Session,
    explore_docs_map: dict[int, ExploreDocument] | None = None,
    internal_bills: dict[str, set[int]] | None = None,
    is_trending: bool = False,
    member_facts: dict[str, dict] | None = None,
) -> dict:
    # By stored key (explore_keys.issue_doc_ids): the rows the ids named
    # can have been replaced since.
    explore_ids = issue_doc_ids(db, issue.related_explore_ids, issue.related_explore_keys)
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
                id=d.id, key=public_key(d.external_id, d.id), title=d.title, doc_type=d.doc_type,
                date=d.date, url=d.url,
                comment_url=d.comment_url or None,
                comments_close_on=d.comments_close_on or None,
                agency_name=d.agency_name or None,
                agency_slug=d.agency_slug or None,
            ).model_dump(by_alias=True)
            for d in docs
        ]

    senator_data = _parse_json_field(getattr(issue, "related_senators", "[]"))
    if member_facts is None:
        member_facts = _current_member_facts(db, _issue_member_ids(issue))
    related_senators = [
        RelatedSenator(**{**s, **member_facts.get(s["id"], {})}).model_dump(by_alias=True)
        for s in senator_data if isinstance(s, dict) and s.get("id")
    ]

    raw_actions = _parse_json_field(issue.actions)
    action_items: list[dict] = []
    # A roll-call issue's vote, on its own page here: every member's
    # position by party, linked, with the chamber's record linked from it.
    # The issue's source is that record (the Clerk's XML, Senate.gov's),
    # which a reader can't use to see how their members voted.
    # Looked up directly, not among issue_roll_calls' newest ten on the bill,
    # which later votes on it can push the issue's own vote out of.
    rc = issue.primary_source_url and db.query(RollCall).filter(
        RollCall.source_url == issue.primary_source_url,
    ).first()
    if rc:
        action_items.append(ActionItemSchema(
            text="How each member voted",
            type="roll_call",
            url=f"/congress/votes/{rc.chamber}/{rc.congress}/{rc.session}/{rc.number}",
        ).model_dump(by_alias=True))
    committee_names = {
        a["committee"]["name"] for a in raw_actions
        if isinstance(a, dict) and a.get("type") == "contact_committee" and isinstance(a.get("committee"), dict)
    }
    leaders = _committee_leaders(db, committee_names)
    today = str(comment_period_today())
    comment_docs = _comment_doc_ids(db, raw_actions)
    for n, a in enumerate(raw_actions):
        if isinstance(a, dict) and "text" in a:
            kind = a.get("type", "general")
            # Open-only, as everywhere a comment link is shown (#903): an
            # issue stays readable for good, its comment period doesn't.
            if kind == "comment_period" and not (a.get("closesOn") and a["closesOn"] >= today):
                continue
            # The document by its key, wherever its row is now; gone, the
            # action goes with it rather than link a missing page.
            doc_id, doc_key, url = a.get("docId"), a.get("docKey"), a.get("url")
            if kind == "comment_period":
                doc_id = comment_docs.get(n)
                if doc_id is None:
                    continue
                if doc_key:
                    url = f"/explore/{doc_key}#comment"
            committee = None
            if kind == "contact_committee" and isinstance(a.get("committee"), dict):
                c = a["committee"]
                committee = CommitteeContactSchema(
                    code=c["code"], name=c["name"], chamber=c["chamber"], url=a.get("url"),
                    **leaders.get(c["name"], {}),
                )
            action_items.append(
                ActionItemSchema(
                    text=a["text"],
                    type=kind,
                    url=url,
                    doc_id=doc_id,
                    doc_key=doc_key,
                    closes_on=a.get("closesOn"),
                    committee=committee,
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
    # Roll-call issues stored before 2026-10-14 recorded their bill with no
    # link (early_signal._vote_bill_entries), and a bill is only shown with
    # one: build it from the id, in the issue's own Congress.
    bills = []
    for b in raw_bills:
        if isinstance(b, dict) and b.get("id") and not b.get("url"):
            congress = b.get("congress") or current_congress
            if url := congress_gov_url_for_bill_id(b["id"], congress):
                b = {**b, "url": url, "congress": congress}
        bills.append(b)
    records = _bill_records(db, {
        (b["id"].upper(), b["congress"]) for b in bills
        if isinstance(b, dict) and b.get("id") and isinstance(b.get("congress"), int)
    })
    related_bills: list[dict] = []
    deciders: list[dict] = []
    for b in bills:
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
            stage, action, action_date = records.get((bill_id, b.get("congress")), ("", "", ""))
            stage_name = BILL_STAGES.get(stage, {}).get("name")
            related_bills.append(
                RelatedBillSchema(
                    name=b.get("name", b["id"]),
                    id=b["id"],
                    url=b["url"],
                    internal_url=(
                        f"/congress/bills/{bill_id}?congress={link_congress}" if link_congress else None
                    ),
                    stage_name=stage_name,
                    latest_action=action or None,
                    latest_action_date=action_date or None,
                ).model_dump(by_alias=True)
            )
            if stage_name and (body := bill_decider(bill_id, stage)):
                deciders.append({"body": body, "basis": f"{bill_label(bill_id) or bill_id} · {stage_name}"})

    monitor_slugs = _parse_json_field(
        getattr(issue, "related_monitor_slugs", "[]")
    )
    monitor_titles = dict(
        db.query(NationalMonitor.slug, NationalMonitor.title)
        .filter(NationalMonitor.slug.in_(monitor_slugs))
        .all()
    ) if monitor_slugs else {}

    rolls = issue_roll_calls(db, issue)
    current_facts = _parse_json_field(issue.facts)
    # Worked out when the facts were written (app/fact_diff.py): diffing
    # them here needed the embedding model on a public GET. NULL on a row
    # the hourly refresh hasn't backfilled yet reads as nothing new.
    new_facts = _parse_json_field(getattr(issue, "new_facts", None) or "[]")

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
        first_surfaced=eastern_date(issue.created_at).isoformat(),
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
        related_monitors=[
            {"slug": slug, "title": monitor_titles[slug]}
            for slug in monitor_slugs if slug in monitor_titles
        ],
        deciders=deciders + [
            DeciderSchema(
                body=d["agencyName"],
                basis=f"taking public comments until {_month_day(d['commentsCloseOn'])}",
            ).model_dump(by_alias=True)
            for d in related_docs
            if d["agencyName"] and d["commentUrl"] and d["commentsCloseOn"] and d["commentsCloseOn"] >= today
        ],
        ballot=issue_ballot(getattr(issue, "ballot_links", None), active_election(db).election_day),
        vote_count=len(rolls),
        related_votes=[vote_schema(rc) for rc in rolls],
        full_story=getattr(issue, "full_story", None),
        is_trending=is_trending,
        status=getattr(issue, "status", None) or "confirmed",
        is_current=issue.is_current is not False,
        retired_reason=None if issue.is_current is not False else getattr(issue, "retired_reason", None),
        covered_by=to_public_id(issue.duplicate_of_id) if issue.duplicate_of_id else None,
        source_type=getattr(issue, "source_type", None),
        count_as_of=count_as_of,
        count_official=count_official,
        image_url=issue_photo(issue),
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
    return compute_trending_issue_ids(view_counts, live=len(public_ids))


# Runs to look back through for one that wrote issues: two days of hourly
# refreshes. Past that the line is omitted rather than naming a stale time.
_REFRESH_LOOKBACK_RUNS = 48


def _refresh_times(db: Session) -> tuple[str | None, str | None]:
    """ISO times (UTC, "Z") of the newest Action Center run that read the
    news, and of the newest one that published or re-matched an issue;
    None for either when no run in the lookback did.

    Both are shown, labelled for what they are. "Updated" used to be the
    second alone, so on a day when every hourly run found nothing that
    passed its checks the page read "Updated 4:17 AM" at 4 PM, as if the
    refresh had stopped (2026-10-15). A run that fetched no articles (the
    feeds down) read nothing, so it is not a check: counting it would say
    "checked just now" through an outage.
    """
    rows = (
        db.query(ApiCache.cached_at, ApiCache.data_json)
        .filter(ApiCache.tier == "action-metrics")
        .order_by(ApiCache.cached_at.desc())
        .limit(_REFRESH_LOOKBACK_RUNS)
        .all()
    )
    checked = changed = None
    for cached_at, data_json in rows:
        try:
            counts = json.loads(data_json).get("counts", {})
        except (ValueError, TypeError, AttributeError):
            continue
        stamp = cached_at.isoformat() + "Z"
        if checked is None and counts.get("articles_fetched", 0) > 0:
            checked = stamp
        if changed is None and counts.get("issues_new_topic", 0) + counts.get("issues_matched_existing", 0) > 0:
            changed = stamp
        if checked and changed:
            break
    return checked, changed


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

    # When the live view last read the news (checkedAt) and last changed
    # (generatedAt: the newest run that wrote issues). Every run leaves an
    # action-metrics row (action_metrics.py), aborted ones included, so the
    # newest row alone would read "checked just now" exactly when the feeds
    # are down. Only for the live view; a past day's page is not "updated".
    checked_at, generated_at = _refresh_times(db) if date is None else (None, None)

    if not issues:
        # A day whose issues all moved on (a re-matched issue is restamped
        # to the day that matched it) still gets a pager, so a reader who
        # followed a link to it can page to the days either side.
        return {
            "date": date, "issues": [],
            "availableDates": _pager_dates(db) if date and _is_iso_day(date) else [],
            "generatedAt": generated_at,
            "checkedAt": checked_at,
        }

    issue_date = date or _latest_issue_date(db) or issues[0].date
    available_dates = _pager_dates(db)

    all_explore_ids: list[int] = []
    for i in issues:
        all_explore_ids.extend(issue_doc_ids(db, i.related_explore_ids, i.related_explore_keys))
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
    member_facts = _current_member_facts(
        db, set().union(*(_issue_member_ids(i) for i in issues)),
    )

    trending_ids = _trending_ids_for(issues, db_visits)

    return {
        "date": issue_date,
        "issues": [
            _build_issue_response(
                i, db, explore_docs_map, internal_bills,
                is_trending=to_public_id(i.id) in trending_ids,
                member_facts=member_facts,
            )
            for i in issues
        ],
        "availableDates": available_dates,
        "generatedAt": generated_at,
        "checkedAt": checked_at,
    }


_RECENT_ISSUES_DEFAULT_LIMIT = 10
_RECENT_ISSUES_MAX_LIMIT = RECENT_FEED_MAX_LIMIT


@router.get("/issues/recent")
async def get_recent_action_issues(
    response: Response,
    limit: int = Query(_RECENT_ISSUES_DEFAULT_LIMIT, ge=1, le=_RECENT_ISSUES_MAX_LIMIT),
    db: Session = Depends(get_db),
):
    """The most recently touched issues, regardless of is_current — except
    developing drafts retired unconfirmed (NOT_RETIRED_DRAFT), which never
    entered the record: two month-old vote drafts led it on 2026-10-15.

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
    embedding model on a public GET inside the event loop. Then one issue
    per national monitor (_one_per_monitor).
    """
    response.headers["Cache-Control"] = f"public, max-age={_ACTION_ISSUES_CACHE_TTL_S}"
    issues = _one_per_monitor(recent_issues(db), db)[:limit]
    member_facts = _current_member_facts(
        db, set().union(*(_issue_member_ids(i) for i in issues)),
    )
    return {"issues": [_build_issue_response(i, db, member_facts=member_facts) for i in issues]}


def recent_issues(db: Session) -> list[ActionIssue]:
    """The recent record's pool, newest day first and each day in rank
    order, with near-identical duplicates left out (_hidden_as_duplicate):
    what /issues/recent and the homepage's front page (api/notable.py) read."""
    pool = (
        db.query(ActionIssue)
        .filter(NOT_RETIRED_DRAFT)
        .order_by(ActionIssue.date.desc(), ActionIssue.rank.asc())
        .limit(RECENT_FEED_MAX_LIMIT * RECENT_FEED_POOL_MULTIPLIER)
        .all()
    )
    by_id = {p.id: p for p in pool}
    return [i for i in pool if not _hidden_as_duplicate(i, by_id)]


def _one_per_monitor(issues: list[ActionIssue], db: Session) -> list[ActionIssue]:
    return [kept for kept, _, _ in monitor_groups(issues, db)]


def monitor_groups(issues: list[ActionIssue], db: Session) -> list[tuple[ActionIssue, set[str], list[ActionIssue]]]:
    """The newest issue of each national monitor's story, in order; issues
    of no monitor are all kept. The homepage record also lists the monitor
    itself, so an older development stays one click away.

    Two developments of one story are not duplicates: a Sunday statement by
    six senators against the Russian diesel deal (10-12) and their dozen's
    letter two days later (10-15) are separate events, 0.82 apart by title
    and three days apart, which the near-identical pass rightly keeps
    apart. But both, and the deal itself, filled half the record's six
    entries. Replayed over the stored issues for each day from 2026-09-01
    to 2026-10-16: 18 of 46 days had a story twice or more in the record's
    first six, and none after. An issue belongs to the monitors it links
    (related_monitor_slugs) and to those whose updates quote its title (a
    monitor opened after the issue backfills updates from it)."""
    linked: dict[str, set[str]] = {}
    for title, slug in (
        db.query(MonitorUpdate.article_title, NationalMonitor.slug)
        .join(NationalMonitor, MonitorUpdate.monitor_id == NationalMonitor.id)
        .filter(MonitorUpdate.article_title.in_(sorted({i.title for i in issues})))
    ):
        linked.setdefault(title, set()).add(slug)
    kept: list[tuple[ActionIssue, set[str], list[ActionIssue]]] = []
    owner: dict[str, int] = {}
    for issue in issues:
        slugs = set(_parse_json_field(issue.related_monitor_slugs or "[]")) | linked.get(issue.title, set())
        hit = next((owner[s] for s in slugs if s in owner), None)
        if hit is not None:
            kept[hit][2].append(issue)
            continue
        owner.update(dict.fromkeys(slugs, len(kept)))
        kept.append((issue, slugs, []))
    return kept


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


def _issue_for_path(issue_id: str, db: Session) -> ActionIssue | None:
    """The issue a path segment names: its public id, or the legacy numeric
    id every share link published before public ids points at
    (`from_public_id` only accepts its own letter-prefixed format, so the
    two can't collide)."""
    resolved_id = from_public_id(issue_id)
    if resolved_id is None and issue_id.isdigit():
        # SQLite's INTEGER column caps at a signed 8-byte int; a longer
        # digit string (a bot, a mistyped URL) isn't a ROWID that could
        # ever exist, and binding it as a query parameter overflows and
        # raises rather than just missing — a real id never gets close to
        # this bound, so treat anything past it as "no such issue".
        candidate = int(issue_id)
        resolved_id = candidate if candidate <= 2**63 - 1 else None
    if resolved_id is None:
        return None
    return db.query(ActionIssue).filter(ActionIssue.id == resolved_id).first()


# A state's members and their votes move nightly at most.
_ISSUE_MEMBERS_CACHE_TTL_S = 300


@router.get("/issues/{issue_id}/members", response_model=IssueMembersSchema, response_model_by_alias=True)
def get_issue_members(
    issue_id: str,
    response: Response,
    state: str = Query(..., min_length=2, max_length=2, description="Two-letter state code the reader picked"),
    db: Session = Depends(get_db),
):
    """A state's sitting senators and representatives, with each office's
    phone and contact form, and how each voted on the roll calls this issue
    is about (issue_roll_calls). The state is the reader's own pick from a
    list, sent in the URL and recorded nowhere: Civitas never asks where a
    visitor lives (AGENTS.md section 8). Read path only: ORM queries."""
    response.headers["Cache-Control"] = f"public, max-age={_ISSUE_MEMBERS_CACHE_TTL_S}"
    code = state.upper()
    if code not in STATE_NAMES:
        raise HTTPException(status_code=404, detail="No such state")
    issue = _issue_for_path(issue_id, db)
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found")

    members = [
        (m, "senate") for m in db.query(Senator).filter(Senator.state == code, Senator.is_current.is_(True))
        .order_by(Senator.name)
    ] + [
        (m, "house") for m in db.query(Representative)
        .filter(Representative.state == code, Representative.is_current.is_(True))
        .order_by(Representative.district)
    ]

    rolls = issue_roll_calls(db, issue)
    positions: dict[str, dict[str, str]] = {}
    if rolls:
        ref_of = {rc.id: _roll_call_ref(rc) for rc in rolls}
        chamber_of = {rc.id: rc.chamber for rc in rolls}
        links = MemberLinks(db) if any(rc.chamber == "senate" for rc in rolls) else None
        by_bioguide = {m.bioguide_id: m.id for m, chamber in members if chamber == "house" and m.bioguide_id}
        for p in db.query(RollCallPosition).filter(
            RollCallPosition.roll_call_id.in_(list(ref_of)), RollCallPosition.state == code,
        ):
            if chamber_of[p.roll_call_id] == "house":
                member_id = by_bioguide.get(p.member_id)
            else:
                page = links.page(last_name=p.last_name, state=p.state, lis_id=p.member_id)
                member_id = page.rsplit("/", 1)[-1] if page else None
            if member_id:
                positions.setdefault(member_id, {})[ref_of[p.roll_call_id]] = p.position

    return IssueMembersSchema(
        state=code,
        votes=[vote_schema(rc) for rc in rolls],
        members=[
            IssueMemberSchema(
                id=m.id, name=m.name, party=m.party, chamber=chamber,
                district=m.district if chamber == "house" else None,
                office_phone=m.office_phone or "", contact_form_url=m.contact_form_url or "",
                website_url=m.website_url or "", positions=positions.get(m.id, {}),
            )
            for m, chamber in members
        ],
    )


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
    issue = _issue_for_path(issue_id, db)
    if not issue:
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


OPEN_COMMENTS_SHOWN = 8
# An issue this recent that links a document (a related document or a
# comment action) puts it first: the reader just saw the story it is about.
OPEN_COMMENTS_ISSUE_DAYS = 14


def _issue_linked_doc_ids(db: Session, since: str) -> set[int]:
    ids: set[int] = set()
    for explore_ids, explore_keys, actions in db.query(
        ActionIssue.related_explore_ids, ActionIssue.related_explore_keys, ActionIssue.actions,
    ).filter(ActionIssue.date >= since, ActionIssue.duplicate_of_id.is_(None), NOT_RETIRED_DRAFT):
        ids.update(issue_doc_ids(db, explore_ids, explore_keys))
        ids.update(i for i in _comment_doc_ids(db, _parse_json_field(actions)).values() if i is not None)
    return ids


def _comment_doc_ids(db: Session, actions: list) -> dict[int, int | None]:
    """For each comment-period action (by its index in `actions`), the row
    now holding its document: by the action's docKey, or its docId for an
    action stored before keys; None when no row holds it any more."""
    wanted = {n: a for n, a in enumerate(actions)
              if isinstance(a, dict) and a.get("type") == "comment_period"
              and (isinstance(a.get("docKey"), str) or isinstance(a.get("docId"), int))}
    keys = [a["docKey"] for a in wanted.values() if isinstance(a.get("docKey"), str)]
    held = resolve_keys(db, keys, [a.get("docId") for a in wanted.values()]) if keys else {}
    return {n: (held.get(a["docKey"]) if isinstance(a.get("docKey"), str) else a["docId"])
            for n, a in wanted.items()}


@router.get("/open-comments")
def get_open_comments(response: Response, db: Session = Depends(get_db)):
    """Federal Register documents with open public comment periods, most
    salient first (comment_periods.salience: rules, then notices, paperwork
    notices last). Each with the abstract's first sentence and its window."""
    # Comment-period deadlines move in days, not minutes — an hour of
    # staleness has no real effect on this list.
    response.headers["Cache-Control"] = "public, max-age=3600"
    today = comment_period_today()
    # Columns, not rows: an open document's body runs to tens of kilobytes,
    # and there are hundreds open at once.
    open_docs = (
        db.query(
            ExploreDocument.id, ExploreDocument.external_id, ExploreDocument.title, ExploreDocument.agency_name,
            ExploreDocument.agency_slug, ExploreDocument.comments_close_on, ExploreDocument.comment_url, ExploreDocument.doc_type,
            ExploreDocument.date, ExploreDocument.summary, ExploreDocument.fr_page_views,
            ExploreDocument.fr_toc_subject,
        )
        .filter(
            ExploreDocument.comment_url.isnot(None),
            ExploreDocument.comment_url != "",
            ExploreDocument.comments_close_on.isnot(None),
            ExploreDocument.comments_close_on >= today,
        )
        .all()
    )
    since = (date.fromisoformat(str(today)) - timedelta(days=OPEN_COMMENTS_ISSUE_DAYS)).isoformat()
    linked = _issue_linked_doc_ids(db, since)
    docs = sorted(open_docs, key=lambda d: comment_periods.salience(d, linked))[:OPEN_COMMENTS_SHOWN]
    result = []
    # No policyAreas: nothing writes ExploreDocument.policy_areas (empty on
    # all 10,589 documents, 2026-10-09) and no page read it.
    for d in docs:
        result.append({
            "id": d.id,
            "key": public_key(d.external_id, d.id),
            "title": d.title,
            "agencyName": d.agency_name,
            "agencySlug": d.agency_slug or None,
            "commentsCloseOn": d.comments_close_on,
            "commentUrl": d.comment_url,
            "docType": d.doc_type,
            "date": d.date,
            "summary": (d.summary or "")[:200],
            "firstSentence": comment_periods.first_sentence(d.summary),
            "paperwork": comment_periods.is_paperwork(d.doc_type, d.fr_toc_subject),
            "commentWindow": comment_periods.comment_window(d.date, d.comments_close_on, str(today)),
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


def monitor_roll_calls(db: Session, m: NationalMonitor) -> list[RollCall]:
    """The roll calls matched to a monitor's updates, oldest first: its "On
    the record". Housekeeping questions are left out as in issue_roll_calls."""
    refs = sorted({r for u in (m.updates or []) for r in stored_refs(u.roll_call_refs)})
    if not refs:
        return []
    rows = db.query(RollCall).filter(or_(*_ref_clauses(refs))).order_by(RollCall.date, RollCall.number).all()
    return [rc for rc in rows if not is_housekeeping(rc.question, rc.chamber)]


def _monitor_to_schema(m: NationalMonitor, include_updates: bool = False, db: Session | None = None):
    """Convert a NationalMonitor ORM object to a schema dict."""
    base = NationalMonitorSchema(
        id=m.id,
        slug=m.slug,
        title=m.title,
        description=m.description,
        category=m.category,
        status=m.status,
        policy_areas=_parse_json_field(m.policy_areas),
        created_at=eastern_date(m.created_at).isoformat(),
        updated_at=eastern_date(m.updated_at).isoformat(),
        last_article_date=m.last_article_date,
        update_count=len(m.updates) if m.updates else 0,
        closed_on=eastern_date(m.updated_at).isoformat() if m.status == MonitorStatus.CLOSED else None,
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
        **{k: getattr(base, k) for k in type(base).model_fields},
        updates=updates,
        on_the_record=[vote_schema(rc) for rc in monitor_roll_calls(db, m)] if db is not None else [],
    )
    return detail.model_dump(by_alias=True)


# How far back the Ongoing tab lists monitors that closed.
_RECENTLY_CLOSED_DAYS = 60


@router.get("/monitors")
async def list_monitors(response: Response, db: Session = Depends(get_db)):
    """List all active and watching national monitors, and those closed
    in the last _RECENTLY_CLOSED_DAYS days."""
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
    # Closed recently, for a tab that would otherwise often be one stale
    # card: a monitor's row is last written when it closes (nothing touches
    # a closed one), so updated_at is the day it closed.
    closed = (
        db.query(NationalMonitor)
        .options(selectinload(NationalMonitor.updates))
        .filter(
            NationalMonitor.status == MonitorStatus.CLOSED,
            NationalMonitor.updated_at >= utcnow() - timedelta(days=_RECENTLY_CLOSED_DAYS),
        )
        .order_by(NationalMonitor.updated_at.desc())
        .all()
    )
    return {
        "monitors": [_monitor_to_schema(m) for m in monitors],
        "recentlyClosed": [_monitor_to_schema(m) for m in closed],
        "recentlyClosedDays": _RECENTLY_CLOSED_DAYS,
    }


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
    return _monitor_to_schema(monitor, include_updates=True, db=db)


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
            "link": "/court",
            "linkLabel": "View the justices and decisions",
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
                "link": "/congress/members",
                "linkLabel": "See the members' records",
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
                "link": "/executive/presidents",
                "linkLabel": "See the presidents' records",
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
    entries = without_repeat_leads(entries)

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
