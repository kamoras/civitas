"""One live figure for each section page: what the site's section hubs and
the homepage's index show beside each link (frontend
`SECTIONS`, lib/routes.ts).

Every figure is a count or a rate over what the site already stores, read
the way the page it links to reads it; the frontend only words it. A
figure the site can't give says why (`available: False` with the page's
own reason, or a null), never a zero for something not yet read.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from app.api.response_helpers import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.action import _latest_current_issues, _latest_issue_date
from app.api.agencies import _budget_row
from app.api.election_archive import latest_check
from app.api.election_map import current_map
from app.api.explore import _explore_counts
from app.api.response_helpers import cached_json
from app.api.this_week import election
from app.database import get_db
from app.election_calendar import next_election_day
from app.election_phase import election_today
from app.models import (
    Agency,
    AgencyBudget,
    MonitorStatus,
    NationalMonitor,
    PresidentialSupport,
    Representative,
    RollCall,
    Senator,
    TimelineEntry,
)
from app.pipeline.analyze.presidential_support import counted_votes
from app.redistricting import overlap_file, redrawn_overlap_states
from app.services import committee_record, congress_service, court_service
from app.services.bill_service import get_bills_in_flight

router = APIRouter()

# The shortest lifetime among the pages these figures stand for (the
# Congress reports and the Action Center, five minutes).
_TTL_S = 300


def _votes_between(db: Session, first: date, last: date) -> int:
    return db.query(func.count(RollCall.id)).filter(
        RollCall.date >= first.isoformat(), RollCall.date <= last.isoformat(),
    ).scalar() or 0


def congress(db: Session) -> dict:
    day = congress_service.latest_day(db)
    out: dict = {"reports": None, "week": None, "month": None}
    if day:
        monday, sunday = congress_service.week_bounds(day)
        first, last = congress_service.month_bounds(day.year, day.month)
        out = {
            "reports": {"date": day.isoformat(), "recordVotes": _votes_between(db, day, day)},
            "week": {"start": monday.isoformat(), "recordVotes": _votes_between(db, monday, sunday)},
            "month": {"month": f"{day:%Y-%m}", "recordVotes": _votes_between(db, first, last)},
        }
        week_start = monday.isoformat()
    out["recess"] = _recess(db, day)
    committees = committee_record.committees(db)
    out["committees"] = {
        "count": len(committees),
        # Met in the same week the week card counts; null before any day.
        "metThisWeek": sum(1 for c in committees if c["latest"] >= week_start) if day else None,
    }
    out["bills"] = {"inMotion": get_bills_in_flight(db, per_page=1).in_motion}
    out["president"] = {c: _president(db, c) for c in ("house", "senate")}
    return out


def _recess(db: Session, day: date | None) -> dict | None:
    """When the latest day Congress met had no business (/congress's own
    `recess`), one line for the three report figures, which would each read
    "None": when the last business was, and the week to open. None when
    there was business, or before any day is on record."""
    if day is None:
        return None
    r = congress_service.recess(db, congress_service.day_report(db, day))
    if r is None:
        return None
    last = r["lastBusinessDay"]
    since = f"since {congress_service.long_date(date.fromisoformat(last))}" if last else "on record"
    return {"line": f"No record votes or floor business {since}", "lastBusinessDay": last, "week": r["week"]}


def _president(db: Session, chamber: str) -> dict | None:
    """Serving members' votes on the roll calls the president took a
    position on, the share cast on the president's side, and how many such
    roll calls: the chamber page's members, summed (chamber_view)."""
    model = Senator if chamber == "senate" else Representative
    rows = db.query(PresidentialSupport.congress, PresidentialSupport.with_president, PresidentialSupport.votes).join(
        model, model.id == PresidentialSupport.member_id,
    ).filter(PresidentialSupport.chamber == chamber, model.is_current.is_(True)).all()
    votes = sum(r.votes for r in rows)
    if not votes:
        return None
    return {
        "rate": round(sum(r.with_president for r in rows) / votes, 4),
        "rollCalls": len(counted_votes(db, chamber, rows[0].congress)),
    }


def elections(db: Session) -> dict:
    e = election(db)
    try:
        data = current_map()
        seats = data["chambers"]["senate"]["seats"]
        ranged = [s for s in seats if s["status"] == "range"]
        seat_map = {
            "available": True,
            "seats": len(seats),
            "withPolls": sum(1 for s in ranged if s.get("rangeBasis") == "polls"),
            "pollsDiffer": sum(1 for s in ranged if s["difference"]["strength"] > 0),
        }
    except HTTPException as exc:
        # Out of the campaign, or not built yet: the map page's own words.
        seat_map = {"available": False, "reason": exc.detail}
    cycle = overlap_file().get("cycle")
    return {
        "ballots": {"states": len(e["states"]), "date": e["date"], "phase": e["phase"], "daysUntil": e["daysUntil"]},
        "map": seat_map,
        "redistricting": {
            "states": len(redrawn_overlap_states()),
            "cycle": cycle,
            # Said as past once that election is held (api/redistricting.py).
            "upcoming": bool(cycle) and election_today() < next_election_day(date(int(cycle), 1, 1)),
        },
        # The newest archived election and how its map's ranges held against
        # the official count; null before the first archive (the card then
        # names the election it starts with, from `ballots`).
        "archive": latest_check(db),
    }


def agencies(db: Session, open_for_comment: int) -> dict:
    budgets = [b for b in db.query(AgencyBudget) if b.retired_at is None]
    fy = max((b.fiscal_year for b in budgets if b.fiscal_year), default=None)
    reported = [r for r in (_budget_row(b) for b in budgets if b.fiscal_year == fy) if r["reported"]]
    return {
        "directory": {"count": db.query(func.count(Agency.slug)).scalar() or 0},
        # Null before USAspending is first read: not a government without a budget.
        "budget": {"total": sum(r["budgetAuthority"] or 0 for r in reported), "fiscalYear": fy,
                   "reportedCount": len(reported)} if reported else None,
        "comments": {"open": open_for_comment},
    }


def action(db: Session) -> dict:
    return {
        "today": {"issues": len(_latest_current_issues(db)), "date": _latest_issue_date(db)},
        "ongoing": {"monitors": db.query(func.count(NationalMonitor.id)).filter(
            NationalMonitor.status.in_([MonitorStatus.ACTIVE, MonitorStatus.WATCHING])).scalar() or 0},
        "archive": {"days": db.query(func.count(TimelineEntry.id)).scalar() or 0,
                    "since": db.query(func.min(TimelineEntry.date)).scalar()},
    }


def politicians(db: Session) -> dict:
    return {
        "directory": {
            "senators": db.query(func.count(Senator.id)).filter(Senator.is_current.is_(True)).scalar() or 0,
            "representatives": db.query(func.count(Representative.id)).filter(
                Representative.is_current.is_(True)).scalar() or 0,
        },
    }


@router.get("/sections")
def sections(db: Session = Depends(get_db)) -> JSONResponse:
    documents, _, _, open_for_comment = _explore_counts(db)
    return cached_json({
        "action": action(db),
        "congress": congress(db),
        "agencies": agencies(db, open_for_comment),
        "politicians": politicians(db),
        # The latest term with decisions; null before any is stored.
        "court": {"court": court_service.summary(db)},
        "elections": elections(db),
        "explore": {"search": {"documents": documents}},
    }, max_age=_TTL_S)
