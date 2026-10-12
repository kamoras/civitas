"""Before election night: can every covered state's feed still be read?

The election-night sync (sync.py) learns a feed is broken only once its
polls have closed: check_missing_counts pages someone an hour after that,
too late to fix a moved host or a renamed contest before the count is the
story. Feeds do break between a state's last verified read and the night
itself: three weeks before the 2026 general (read 2026-10-12) both New
Mexico's and Montana's election-night hosts sent their current-election
page to their Secretary of State's landing page (Montana's says results
come "after 8:00 on November 3"), where two weeks earlier New Mexico's had
served its staged general and in 2024 both served it a month ahead. A
state whose host stays dark until the night is alerted every day until it
serves the general: that is the honest state of its feed.

So for the READINESS_DAYS before election day, once a day, every covered
state is read exactly as the sync reads it and the problems are raised as
ops alerts: no count for the general (not staged yet, moved, or
unreadable), a refused one (test, preview or another election's data), or
one whose federal contests don't account for the state's races. Nothing is
stored: before polls close nothing of a count is kept or shown
(fetch/poll_close.py), and a staged feed holds no votes anyway. It stops
the day before election day, when the sync's own checks take over.
"""

import logging
from datetime import date

import httpx
from sqlalchemy.orm import Session

from app.live_results.sync import _contest_race, contest_is_sane
from app.models import Race
from app.ops_alerts import resolve_ops_alert, send_ops_alert
from app.pipeline.fetch.election_results import UntrustedCount, fetch_state_count, live_results_states

logger = logging.getLogger(__name__)

# How long before election day the daily check runs. Every vendor here has
# staged its general by a week out in past cycles (Nebraska's, Washington's
# and Virginia's were up by 2026-10-08, 26 days ahead), and two weeks leaves time
# to fix a reader once a source moves. A state that stages late is alerted
# as not staged until it does: that is the honest state of its feed.
READINESS_DAYS = 14


def readiness_due(election_day: date, today: date) -> bool:
    """Whether the daily check runs `today` (Eastern): inside the window,
    and never on election day itself, when the sync takes over."""
    return 0 < (election_day - today).days <= READINESS_DAYS


def _condition(state: str) -> str:
    return f"results-readiness-{state}"


async def state_readiness(db: Session, client: httpx.AsyncClient, state: str, election_day: date) -> str | None:
    """Why `state`'s feed isn't ready for election night, or None."""
    try:
        count = await fetch_state_count(client, state, election_day)
    except UntrustedCount as refused:
        return f"its feed was refused: {refused}"
    except Exception as exc:  # one state's broken feed never stops the check
        logger.exception("Readiness read failed for %s", state)
        return f"its feed could not be read ({type(exc).__name__})"
    if count is None:
        return (f"its feed has no count for the {election_day.isoformat()} general: not staged yet, moved, "
                "or unreadable")
    matched = set()
    for contest in count.contests:
        race = _contest_race(db, election_day.year, state, contest) if contest_is_sane(contest) else None
        if race is not None:
            matched.add(race.id)
    expected = {
        r.id for r in db.query(Race.id).filter(
            Race.state == state, Race.cycle_year == election_day.year, Race.office.in_(("S", "H")),
        )
    }
    missing = sorted(expected - matched)
    if missing:
        return (f"its feed lists {len(count.contests)} federal contests but none for {', '.join(missing)} "
                "(a label the reader can't parse, or a contest not staged yet)")
    return None


async def check_feed_readiness(
    db: Session, client: httpx.AsyncClient, election_day: date, today: date,
) -> dict[str, str]:
    """Read every covered state; alert on each problem (once per state a
    day, superseded by the next day's), clear the alert of a state that is
    ready again. Returns {state: problem}."""
    problems: dict[str, str] = {}
    for state in sorted(live_results_states()):
        problem = await state_readiness(db, client, state, election_day)
        if problem is None:
            resolve_ops_alert(_condition(state))
            continue
        problems[state] = problem
        send_ops_alert(
            f"Live results not ready: {state}",
            f"{state} {problem}. Election day is {election_day.isoformat()}; fix the source entry or reader "
            "before polls close, or the state shows no count that night.",
            dedupe_key=f"results-readiness-{state}-{election_day.isoformat()}-{today.isoformat()}",
            condition=_condition(state),
        )
    return problems
