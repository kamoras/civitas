"""The Congress reports: what each chamber did on a day, in a week, in a month."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.response_helpers import cached_json
from app.database import get_db
from app.models import RollCall
from app.services import congress_service
from app.services.bill_record import vote_detail

router = APIRouter(prefix="/congress")

# Short: a session day's floor logs change every half hour.
_TTL_S = 300


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="Expected a date as YYYY-MM-DD")


@router.get("/latest")
def latest(db: Session = Depends(get_db)) -> JSONResponse:
    """The most recent day either chamber met, as a day report; 404 before
    anything has been recorded."""
    day = congress_service.latest_day(db)
    if day is None:
        raise HTTPException(status_code=404, detail="No day of Congress recorded yet")
    return cached_json(congress_service.day_report(db, day), max_age=_TTL_S)


@router.get("/day/{day}")
def day(day: str = Path(pattern=r"^\d{4}-\d{2}-\d{2}$"), db: Session = Depends(get_db)) -> JSONResponse:
    return cached_json(congress_service.day_report(db, _parse_date(day)), max_age=_TTL_S)


@router.get("/week/{day}")
def week(day: str = Path(pattern=r"^\d{4}-\d{2}-\d{2}$"), db: Session = Depends(get_db)) -> JSONResponse:
    """The Monday-to-Sunday week containing `day`."""
    return cached_json(congress_service.week_report(db, _parse_date(day)), max_age=_TTL_S)


@router.get("/month/{month}")
def month(month: str = Path(pattern=r"^\d{4}-\d{2}$"), db: Session = Depends(get_db)) -> JSONResponse:
    year, mon = (int(x) for x in month.split("-"))
    if not 1 <= mon <= 12:
        raise HTTPException(status_code=422, detail="Expected a month as YYYY-MM")
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
