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
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.election_calendar import new_congress_day, next_election_day, previous_election_day

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


def eastern_date(stamp: datetime) -> date:
    """The Eastern calendar date of a stored (naive UTC) timestamp: a count
    that last moved at 9 PM ET on the 16th is stored as the 17th."""
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(ELECTION_TZ).date()


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


def ballot_is_final(election: ActiveElection, today: date | None = None) -> bool:
    """Whether the election's ballot can no longer change: its day has
    passed. Through the results window the site stays on the election just
    held, but its ballot sources move on -- a state's single "next election"
    page lists the next one, a candidate list reads as "not published yet"
    -- so re-reading them would unwrite a certified ballot. What was read
    before the day stands."""
    return election.election_day < (today or election_today())


def days_until(election: ActiveElection, today: date | None = None) -> int:
    today = today or election_today()
    return (election.election_day - today).days
