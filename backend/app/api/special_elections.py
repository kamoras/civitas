"""Special elections, vacant seats, the federal election calendar and which
season /elections is in. Every figure comes from app.special_elections,
computed from stored data on the read (no model, no fetch)."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.response_helpers import CACHE_TTL_DETAIL_S, CACHE_TTL_LIST_S, cached_json
from app.database import get_db
from app.election_phase import election_today
from app.special_elections import election_calendar, season, specials_board

router = APIRouter(prefix="/elections")


@router.get("/specials")
def specials(db: Session = Depends(get_db)):
    """The seats in Congress empty today, each special election the FEC
    lists (with its count from the state's own feed, and its swing against
    the seat's usual vote once settled), the average swing over the year's
    specials, and the season /elections is in."""
    return cached_json(
        {**specials_board(db), "season": season(db, election_today())},
        max_age=CACHE_TTL_DETAIL_S,
    )


@router.get("/calendar")
def calendar(db: Session = Depends(get_db)):
    """Federal election dates (the FEC's), each state's voter deadlines (its
    election authority's), and the FEC's reporting deadlines, from the month
    just gone to a year ahead, each with its status on the state's clock."""
    return cached_json(
        {**election_calendar(db), "season": season(db, election_today())},
        max_age=CACHE_TTL_LIST_S,
    )
