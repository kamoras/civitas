"""Special elections, vacant seats, the federal election calendar, which
season /elections is in, and the next cycle (app.next_cycle). Every figure comes from app.special_elections,
computed from stored data on the read (no model, no fetch)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.response_helpers import CACHE_TTL_DETAIL_S, CACHE_TTL_LIST_S, CACHE_TTL_REFERENCE_S, cached_json
from app.database import get_db
from app.election_phase import election_today, season
from app import electoral_votes
from app.api.elections import _candidate_summary
from app.election_administration import administration
from app.models import FederalElectionDate, Race
from app.next_cycle import deadline_event, field, new_congress
from app.pipeline.fetch.ballot_lookup import lookup_for_state
from app.state_ballot_scope import odd_year_elections
from app.state_names import STATE_NAMES
from app.special_elections import election_calendar, specials_board

router = APIRouter(prefix="/elections")


@router.get("/specials")
def specials(db: Session = Depends(get_db)):
    """The seats in Congress empty today, each special election the FEC
    lists (with its count from the state's own feed, and its swing against
    the seat's usual vote once settled), the average swing over the year's
    specials, and the season /elections is in."""
    return cached_json(
        {**specials_board(db), "season": season(db).as_json()},
        max_age=CACHE_TTL_DETAIL_S,
    )


@router.get("/calendar")
def calendar(db: Session = Depends(get_db)):
    """Federal election dates (the FEC's), each state's voter deadlines (its
    election authority's), and the FEC's reporting deadlines, from the month
    just gone to a year ahead, each with its status on the state's clock."""
    return cached_json(
        {**election_calendar(db), "season": season(db).as_json()},
        max_age=CACHE_TTL_LIST_S,
    )


@router.get("/field")
def next_field(db: Session = Depends(get_db)):
    """Who has registered with the FEC for the site's cycle: filings, open
    seats by the FEC's own coding, and where each sitting member whose seat
    is up stands. Registrations, never ballot positions."""
    return cached_json(field(db, season(db).cycle), max_age=CACHE_TTL_LIST_S)


@router.get("/new-congress")
def congress_compared(db: Session = Depends(get_db)):
    """How the sitting Congress compares with the one before: members new
    to each chamber, back after a gap, from the other chamber, and median
    age by birth year, from each member's Congress.gov record."""
    return cached_json(new_congress(db), max_age=CACHE_TTL_LIST_S)


@router.get("/fec-deadline")
def fec_deadline(db: Session = Depends(get_db)):
    """The FEC's reporting deadline the elections pages lead with, or null."""
    return cached_json({"deadline": deadline_event(db, election_today())}, max_age=CACHE_TTL_LIST_S)


@router.get("/odd-year")
def odd_year():
    """The states holding regular state elections in the coming odd year
    (this year when it is odd), and which kinds of contest, by the state
    constitution or law the scope file cites; each with its election
    office's link. Civitas lists no candidates or counts for these."""
    today = election_today()
    year = today.year if today.year % 2 else today.year + 1
    states = [
        {**row, "stateName": STATE_NAMES.get(row["state"], row["state"]),
         "office": {k: v for k, v in lookup_for_state(row["state"]).items() if k in ("url", "label", "sourceName")}}
        for row in odd_year_elections(year)
    ]
    return cached_json({"year": year, "states": states}, max_age=CACHE_TTL_LIST_S)


# How many of the presidential contest's registered candidates the page
# lists, most money raised first; the count says how many there are.
PRESIDENTIAL_SHOWN = 12


@router.get("/presidential")
def presidential(db: Session = Depends(get_db)):
    """The presidential contest of the site's cycle, when it holds one:
    electoral votes by state from the House apportionment, the candidates
    registered with the FEC (its statutory candidates; each state certifies
    who is on its own ballot), and the presidential primaries ahead on the
    FEC's calendar. No count is read for it and no race is called."""
    current = season(db)
    cycle = current.cycle
    body = {"cycle": cycle, "presidential": electoral_votes.is_presidential(cycle),
            "nextPresidential": cycle + (-cycle) % 4, "electoralVotes": electoral_votes.summary()}
    if body["presidential"]:
        race = db.get(Race, f"{cycle}-PRES")
        cands = sorted(race.candidates if race else [], key=lambda c: -(c.contributions or 0))
        body["registered"] = len(cands)
        body["candidates"] = [_candidate_summary(c) for c in cands[:PRESIDENTIAL_SHOWN]]
        body["primaries"] = [
            {"date": r.election_date, "state": r.state, "stateName": STATE_NAMES.get(r.state, r.state),
             "label": r.type_label or r.type_code}
            for r in db.query(FederalElectionDate).filter(
                FederalElectionDate.office == "P", FederalElectionDate.election_year == cycle,
                FederalElectionDate.is_special.is_(False),
                FederalElectionDate.election_date >= election_today().isoformat(),
            ).order_by(FederalElectionDate.election_date, FederalElectionDate.state).limit(PRESIDENTIAL_SHOWN)
        ]
    return cached_json(body, max_age=CACHE_TTL_LIST_S)


@router.get("/administration")
def election_administration():
    """How the last federal general was run, by state: registration,
    turnout, how people voted, and mail and provisional ballots rejected,
    from the EAC's Election Administration and Voting Survey. Null figures
    where a state's jurisdictions didn't all report."""
    data = administration()
    if data is None:
        raise HTTPException(status_code=503, detail="The EAC survey file is missing")
    return cached_json(data, max_age=CACHE_TTL_REFERENCE_S)
