"""Which election the site is about right now, and what stage it is in.

`next_election_day` answers "when is the next one", which is the wrong
question the moment polls close: on the morning after, it already says
2028, and every page keyed to it would drop the count still coming in for
a campaign two years away. So the site's election is the one just held
for as long as its results are still worth showing, and the next one only
after that:

  campaign      before election day — ballot research, lean maps.
  election_day  the day itself (Eastern date, which is where polls close
                first and where the networks' "election night" is dated).
  results       after it, until results_window_end.

`season` (below) refines that into the six stretches /elections and its hub
lay themselves out by, from the same rule: election night and the results
window are this phase's; the rest of the calendar is split by January 3 and
the FEC's own primary calendar.

"Results are in" is read from the count, not from the calendar: no feed
this system reads says how many ballots remain, and states take anywhere
from a night to five weeks to finish, so the page holds the election
until RESULTS_GRACE_DAYS after the vote totals last moved in any race.
It never holds past the day the new Congress is seated (new_congress_day),
when the winners are serving and the election is history.

The database is consulted only between an election day and that January
3; the rest of the year this is pure calendar arithmetic.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.election_calendar import new_congress_day, next_election_day, previous_election_day
from app.models import FederalElectionDate
from app.state_names import STATE_NAMES
from app.time_utils import eastern_date

# How long a finished count stays the page's subject before it moves on to
# the next election: two weeks after the last change is long enough to read
# the outcome and short enough that the next campaign's research isn't
# hidden behind a result everyone already knows. An editorial choice, not
# a measured value.
RESULTS_GRACE_DAYS = 14

ELECTION_TZ = ZoneInfo("America/New_York")

CAMPAIGN = "campaign"
ELECTION_DAY = "election_day"
RESULTS = "results"


@dataclass(frozen=True)
class ActiveElection:
    election_day: date
    phase: str
    # The last day the results stay up, while in the election_day/results
    # phases; None during a campaign.
    results_until: date | None
    # When any race's vote totals last moved; None before the first count.
    last_result_change: datetime | None

    @property
    def cycle(self) -> int:
        return self.election_day.year

    @property
    def shows_results(self) -> bool:
        return self.phase != CAMPAIGN


def election_today() -> date:
    return datetime.now(ELECTION_TZ).date()


def results_window_end(election_day: date, last_change: date | None) -> date:
    """The last day an election's results are the page's subject."""
    settled_from = max(election_day, last_change or election_day)
    return min(new_congress_day(election_day), settled_from + timedelta(days=RESULTS_GRACE_DAYS))


def resolve_active_election(
    today: date, last_change_for: Callable[[date], datetime | None],
) -> ActiveElection:
    """The pure rule, with the count's last change supplied by the caller
    (and asked for only inside a results window's outer bound)."""
    held = previous_election_day(today)
    if held is not None and today <= new_congress_day(held):
        last_change = last_change_for(held)
        until = results_window_end(held, eastern_date(last_change) if last_change else None)
        if today <= until:
            return ActiveElection(
                election_day=held,
                phase=ELECTION_DAY if today == held else RESULTS,
                results_until=until,
                last_result_change=last_change,
            )
    return ActiveElection(
        election_day=next_election_day(today), phase=CAMPAIGN,
        results_until=None, last_result_change=None,
    )


def last_result_change(db: Session, election_day: date) -> datetime | None:
    from app.models import RaceResult

    return db.query(func.max(RaceResult.last_change_at)).filter(
        RaceResult.election_date == election_day.isoformat(),
    ).scalar()


def active_election(db: Session | None = None, today: date | None = None) -> ActiveElection:
    """The site's election as of `today` (Eastern). Opens its own session
    when given none, and only when the calendar says it needs one."""
    today = today or election_today()

    def lookup(election_day: date) -> datetime | None:
        if db is not None:
            return last_result_change(db, election_day)
        from app.database import SessionLocal

        session = SessionLocal()
        try:
            return last_result_change(session, election_day)
        finally:
            session.close()

    return resolve_active_election(today, lookup)


def election_is_held(election: ActiveElection, today: date | None = None) -> bool:
    """Whether the election has been held: its day has passed, so its
    ballot can no longer change. (Not state_candidates_common.ballot_final,
    which is 45 days earlier: the ballot mailed to voters.) Through the results window the site stays on the election just
    held, but its ballot sources move on -- a state's single "next election"
    page lists the next one, a candidate list reads as "not published yet"
    -- so re-reading them would unwrite a certified ballot. What was read
    before the day stands."""
    return election.election_day < (today or election_today())




# The six seasons of the elections section (season() below), in calendar order.
SEASON_CAMPAIGN = "campaign"
SEASON_ELECTION_NIGHT = "election_night"
SEASON_RESULTS = "results"
SEASON_POST_CERTIFICATION = "post_certification"
SEASON_OFF = "off_season"
SEASON_RAMP = "ramp"

# The FEC election-date type codes that open and close a cycle's primary
# season: its primaries, presidential caucuses and primary runoffs (the
# FEC's own codes, /v1/election-dates/ `election_type_id`). Specials are
# their own calendar and never move the season.
_OPENS_PRIMARIES = ("P", "CAU")
_SETTLES_NOMINEES = ("P", "PR")

# Which of the section's pages lead the hub in each season, most useful
# first (frontend lib/routes.ts SECTIONS.elections keys). A page is never
# dropped, only moved: a tab never disappears by season (#1047).
SEASON_LEAD = {
    SEASON_CAMPAIGN: ("ballots", "map", "redistricting", "calendar", "archive"),
    SEASON_ELECTION_NIGHT: ("ballots", "archive", "redistricting", "calendar", "map"),
    SEASON_RESULTS: ("ballots", "archive", "redistricting", "calendar", "map"),
    SEASON_POST_CERTIFICATION: ("archive", "calendar", "ballots", "redistricting", "map"),
    SEASON_OFF: ("calendar", "archive", "ballots", "redistricting", "map"),
    SEASON_RAMP: ("calendar", "ballots", "map", "redistricting", "archive"),
}


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None


@dataclass(frozen=True)
class Season:
    """Which stretch of the election calendar /elections is in.

    name     one of the six SEASON_* values.
    cycle    the general election the season leads up to, or (election
             night, results) the one being counted: active_election's.
    held     the last general election held on or before today, the one
             the archive and the new Congress are about; None before any.
    until    the last day of the season where the calendar states it
             (the primaries' end needs the FEC's calendar; None without).
    first_primary / last_primary
             the cycle's first primary or caucus and its last primary or
             primary runoff, from the FEC's calendar; None until listed.
    """
    name: str
    cycle: int
    election_day: date
    held: int | None
    until: date | None
    first_primary: date | None
    last_primary: date | None

    @property
    def between_cycles(self) -> bool:
        return self.name in (SEASON_POST_CERTIFICATION, SEASON_OFF)

    def as_json(self) -> dict:
        return {
            "season": self.name,
            "betweenCycles": self.between_cycles,
            "cycle": self.cycle,
            "electionDay": self.election_day.isoformat(),
            "heldCycle": self.held,
            "until": _iso(self.until),
            "firstPrimary": _iso(self.first_primary),
            "lastPrimary": _iso(self.last_primary),
            "lead": list(SEASON_LEAD[self.name]),
        }


def resolve_season(today: date, election: ActiveElection, first_primary: date | None,
                   last_primary: date | None) -> Season:
    """The pure rule, with the cycle's primary calendar supplied.

    election night  active_election's election_day phase (Eastern date).
    results         its results window.
    post_certification
                    from the window's close to January 3: the count has
                    settled; how much of it states list as official is the
                    archive's to say, never assumed here.
    off_season      January 3 to the cycle's first primary or caucus; with
                    none on the FEC's calendar, any day before the cycle's
                    own year (no state holds a regular federal primary in
                    an odd year).
    ramp            the first primary to the last primary or runoff.
    campaign        after it, to election day. With no primary calendar
                    stored, the whole of the election year.
    """
    held = previous_election_day(today)

    def make(name: str, until: date | None) -> Season:
        return Season(name=name, cycle=election.cycle, election_day=election.election_day,
                      held=held.year if held else None, until=until,
                      first_primary=first_primary, last_primary=last_primary)

    if election.phase == ELECTION_DAY:
        return make(SEASON_ELECTION_NIGHT, election.election_day)
    if election.phase == RESULTS:
        return make(SEASON_RESULTS, election.results_until)
    if held is not None and today < new_congress_day(held):
        return make(SEASON_POST_CERTIFICATION, new_congress_day(held) - timedelta(days=1))
    if first_primary is None:
        if today.year < election.cycle:
            return make(SEASON_OFF, None)
        return make(SEASON_CAMPAIGN, election.election_day)
    if today < first_primary:
        return make(SEASON_OFF, first_primary - timedelta(days=1))
    if last_primary is not None and today <= last_primary:
        return make(SEASON_RAMP, last_primary)
    return make(SEASON_CAMPAIGN, election.election_day)


def primary_calendar(db: Session, cycle: int) -> tuple[date | None, date | None]:
    """(first primary or caucus, last primary or primary runoff) of `cycle`,
    from the FEC's calendar as stored (fetch/special_elections.py)."""
    # The states and DC (the territories' delegate primaries aren't a
    # state's), and only before the general: a contest the FEC files as a
    # primary on election day itself, or after it (an open "jungle"
    # primary and its runoff), nominates nobody for that general.
    regular = db.query(FederalElectionDate.election_date).filter(
        FederalElectionDate.election_year == cycle, FederalElectionDate.is_special.is_(False),
        FederalElectionDate.state.in_(STATE_NAMES),
        FederalElectionDate.election_date < next_election_day(date(cycle, 1, 1)).isoformat(),
    )
    first = regular.filter(FederalElectionDate.type_code.in_(_OPENS_PRIMARIES)).order_by(
        FederalElectionDate.election_date).limit(1).scalar()
    # Senate and House nominees: the presidential calendar ends in June,
    # months before the last state's congressional primary.
    last = regular.filter(
        FederalElectionDate.type_code.in_(_SETTLES_NOMINEES), FederalElectionDate.office.in_(("H", "S")),
    ).order_by(FederalElectionDate.election_date.desc()).limit(1).scalar()
    return (date.fromisoformat(first) if first else None, date.fromisoformat(last) if last else None)


def season(db: Session, today: date | None = None) -> Season:
    """The elections section's season as of `today` (Eastern)."""
    today = today or election_today()
    election = active_election(db, today)
    return resolve_season(today, election, *primary_calendar(db, election.cycle))
