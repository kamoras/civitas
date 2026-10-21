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
ops alerts: a feed that lists no general yet (not staged, or a source
entry reading a list the state no longer updates), one that can't be read
(an outage, a moved URL, a wall), a refused one (test, preview or
another election's data), or one whose federal contests don't account
for the state's races. Nothing is
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
from app.pipeline.fetch.election_results import FeedUnreadable, UntrustedCount, fetch_state_count, live_results_states

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


# One alert a day for every state not ready, its reasons in the body: a
# condition per state put eleven near-identical alerts on the panel on
# 2026-10-20, most of them the honest "not staged yet".
CONDITION = "results-readiness"


def legacy_condition(state: str) -> str:
    """The per-state condition alerts used until 2026-10; every check
    resolves them, so none stays open on the panel."""
    return f"results-readiness-{state}"


async def state_readiness(db: Session, client: httpx.AsyncClient, state: str, election_day: date) -> str | None:
    """Why `state`'s feed isn't ready for election night, or None."""
    try:
        count = await fetch_state_count(client, state, election_day)
    except UntrustedCount as refused:
        return f"its feed was refused: {refused}"
    except FeedUnreadable as unread:
        return f"its feed could not be read: {unread}"
    except Exception as exc:  # one state's broken feed never stops the check
        logger.exception("Readiness read failed for %s", state)
        return f"its feed could not be read ({type(exc).__name__})"
    if count is None:
        # The reader read the state's own election list or results page and
        # it shows nothing held that day (a failed read raises instead).
        return (f"its results site lists no election on {election_day.isoformat()} yet: the general is not "
                "staged, or the source entry reads a list the state no longer updates")
    if not count.contests:
        # New Mexico's staged general, 2026-10-21: its date and type, no
        # contest loaded yet.
        return "its feed serves the general but lists no federal contest yet (staged before its contests)"
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
    """Read every covered state; one alert a day naming each state that
    isn't ready with its reason (superseded by the next day's), cleared once
    every state is ready. Returns {state: problem}."""
    states = sorted(live_results_states())
    problems: dict[str, str] = {}
    for state in states:
        problem = await state_readiness(db, client, state, election_day)
        if problem is not None:
            problems[state] = problem
    for state in states:
        resolve_ops_alert(legacy_condition(state))
    if not problems:
        resolve_ops_alert(CONDITION)
        return problems
    ready = [s for s in states if s not in problems]
    send_ops_alert(
        f"Live results not ready: {len(problems)} of {len(states)} states ({', '.join(problems)})",
        f"Election day is {election_day.isoformat()}. Each state is read exactly as election night's sync "
        "will read it; fix a source entry or reader before polls close, or that state shows no count that "
        "night. A general not staged yet is the honest state of a feed until the state stages it.\n"
        + "\n".join(f"- {state}: {problem}" for state, problem in problems.items())
        + (f"\nReady: {', '.join(ready)}." if ready else ""),
        dedupe_key=f"{CONDITION}-{election_day.isoformat()}-{today.isoformat()}",
        condition=CONDITION,
    )
    return problems
