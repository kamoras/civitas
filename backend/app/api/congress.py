"""The Congress reports: what each chamber did on a day, in a week, in a month."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from app.api.response_helpers import JSONResponse
from sqlalchemy.orm import Session

from app.api.response_helpers import cached_json
from app.database import get_db
from app.models import RollCall
from app.pipeline.congress_activity import eastern_today
from app.bill_ids import canonical_bill_id
from app.services import committee_record, congress_glance, congress_service
from app.services.bill_record import vote_detail
from app.state_names import STATE_NAMES

router = APIRouter(prefix="/congress")

# Short: a session day's floor logs change every half hour.
_TTL_S = 300

# The 1st Congress convened in 1789; nothing before it can have a record,
# and the week and month arithmetic runs off the calendar at year 1 and
# 9999 (a 500, not an answer). Next year is the latest worth asking about.
_FIRST_YEAR = 1789


def _check_year(year: int) -> None:
    if not _FIRST_YEAR <= year <= eastern_today().year + 1:
        raise HTTPException(status_code=422, detail="Outside the years Congress has met")


def _parse_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="Expected a date as YYYY-MM-DD") from None
    _check_year(parsed.year)
    return parsed


def _check_on_record(db: Session, first: date, last: date) -> None:
    """404 a period wholly outside the days on record (record_span): before
    the first, or starting after today."""
    span = congress_service.record_span(db)
    if span is None or last < span[0] or first > span[1]:
        raise HTTPException(status_code=404, detail="No record of Congress for this date")


@router.get("/latest")
def latest(db: Session = Depends(get_db)) -> JSONResponse:
    """The most recent day either chamber met, as a day report; 404 before
    anything has been recorded."""
    day = congress_service.latest_day(db)
    if day is None:
        raise HTTPException(status_code=404, detail="No day of Congress recorded yet")
    report = congress_service.day_report(db, day)
    # Only here: /congress is "what's happening now", a dated day report is
    # that day's record.
    report["recess"] = congress_service.recess(db, report)
    return cached_json(report, max_age=_TTL_S)


@router.get("/coverage")
def coverage(db: Session = Depends(get_db)) -> JSONResponse:
    """What Civitas's record of Congress holds (the first day on record, the
    Congresses whose roll calls are held) and the sentence a page states
    where a day, week, month or roll call is outside it."""
    return cached_json(congress_service.coverage(db), max_age=3600)


@router.get("/day/{day}")
def day(day: str = Path(pattern=r"^\d{4}-\d{2}-\d{2}$"), db: Session = Depends(get_db)) -> JSONResponse:
    parsed = _parse_date(day)
    _check_on_record(db, parsed, parsed)
    return cached_json(congress_service.day_report(db, parsed), max_age=_TTL_S)


@router.get("/week/{day}")
def week(day: str = Path(pattern=r"^\d{4}-\d{2}-\d{2}$"), db: Session = Depends(get_db)) -> JSONResponse:
    """The Monday-to-Sunday week containing `day`."""
    parsed = _parse_date(day)
    monday = parsed - timedelta(days=parsed.weekday())
    _check_on_record(db, monday, monday + timedelta(days=6))
    return cached_json(congress_service.week_report(db, parsed), max_age=_TTL_S)


@router.get("/month/{month}")
def month(month: str = Path(pattern=r"^\d{4}-\d{2}$"), db: Session = Depends(get_db)) -> JSONResponse:
    year, mon = (int(x) for x in month.split("-"))
    if not 1 <= mon <= 12:
        raise HTTPException(status_code=422, detail="Expected a month as YYYY-MM")
    _check_year(year)
    first = date(year, mon, 1)
    _check_on_record(db, first, date(year + mon // 12, mon % 12 + 1, 1) - timedelta(days=1))
    return cached_json(congress_service.month_report(db, year, mon), max_age=_TTL_S)


@router.get("/votes/{chamber}/{congress}/{session}/{number}")
def vote(
    chamber: str = Path(pattern="^(senate|house)$"),
    congress: int = Path(ge=93, le=200),
    session: int = Path(ge=1, le=3),
    number: int = Path(ge=1, le=5000),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """One roll call with every member's position and each party's split."""
    rc = db.query(RollCall).filter_by(chamber=chamber, congress=congress, session=session, number=number).one_or_none()
    if rc is None:
        raise HTTPException(status_code=404, detail="Vote not recorded")
    return cached_json(vote_detail(db, rc), max_age=3600)


@router.get("/glance")
def glance(db: Session = Depends(get_db)) -> JSONResponse:
    """The sitting Congress at a glance: the days each chamber sat, with
    record votes, and how many of its bills reached each step
    (congress_glance.glance)."""
    return cached_json(congress_glance.glance(db), max_age=_TTL_S)


@router.get("/members")
def state_members(
    state: str = Query(..., min_length=2, max_length=2, description="Two-letter state code the reader picked"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """A state's sitting members with their record this Congress: roll
    calls, missed votes, latest votes and bills (congress_glance.
    state_members). The state is the reader's pick from a list, in the URL
    and recorded nowhere (AGENTS.md section 8)."""
    code = state.upper()
    if code not in STATE_NAMES:
        raise HTTPException(status_code=404, detail="No such state")
    return cached_json({"state": code, "members": congress_glance.state_members(db, code)}, max_age=_TTL_S)


@router.get("/committees")
def committees(db: Session = Depends(get_db)) -> JSONResponse:
    """Every committee with a meeting on record, by chamber, with how many
    meetings and the latest one's date (committee_record.committees)."""
    return cached_json({"committees": committee_record.committees(db)}, max_age=_TTL_S)


@router.get("/committee-record")
def committee_search(
    chamber: str | None = Query(None, pattern="^(senate|house)$"),
    committee: str | None = Query(None, max_length=200),
    q: str | None = Query(None, max_length=200),
    bill: str | None = Query(None, max_length=16),
    kind: str | None = Query(None, pattern="^(meeting|report)$"),
    congress: int | None = Query(None, ge=93, le=200),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Committee meetings and the reports filed on measures, newest first,
    as the Daily Digest printed them, narrowed by chamber, committee, words
    (all of them), a bill (named anywhere in the entry, as Civitas names it:
    S.465), kind and Congress. A `q` that is one bill number is read as that
    bill (committee_record.search)."""
    # Any spelling of a bill ("S. 465", "s465"), read as Civitas names it.
    if bill is not None:
        bill = canonical_bill_id(bill)
        if bill is None:
            raise HTTPException(status_code=422, detail="Expected a bill, e.g. S.465 or H.R. 1")
    return cached_json(committee_record.search(
        db, chamber=chamber, committee=committee, q=q, bill=bill, kind=kind,
        congress=congress, page=page, per_page=per_page,
    ), max_age=_TTL_S)
