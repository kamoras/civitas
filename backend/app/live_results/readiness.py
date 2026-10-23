"""Before election night: can every covered state's feed still be read?

The election-night sync (sync.py) learns a feed is broken only once its
polls have closed. Feeds do break between a state's last verified read and
the night itself, so for the READINESS_DAYS before election day, once a
day, every covered state is read exactly as the sync reads it.

A state whose general is staged is ready when its federal contests account
for every race on file (by label, or by candidates where the label is one
the reader can't place: sync.place_by_candidates). Its responses are then
recorded (RECORDINGS) as that state's reader self-check for the next cycle.

A state whose general is not staged yet is the normal state of many feeds
two weeks out: Colorado's and Iowa's Clarity indexes, Georgia's, Idaho's
and Rhode Island's Enhanced Voting indexes listed no 2026 general on
2026-10-22, and Montana's results host sends its default page to the
Secretary of State's landing page, which says results come "after 8:00 on
November 3". The read still proves the state's own index answered. What it
can't prove is that the reader would parse the general once staged, so the
reader runs a self-check instead (self_check): the same reader, unchanged,
on the state's previous general, read live through the state's own index
where the index still lists it (it did for AR, CO, GA, SC, UT, VA and WV on
2026-10-22), else on the state's recording, replayed offline. It passes
when the contests it reads account for every House seat on file. A state
whose self-check passes is not a problem, and is listed only as waiting.

Lead time for an unstaged state: none before its polls close. Montana's
Secretary of State says its results appear after its polls close, so any
earlier alert would fire on a state doing what it said it would, and the
self-check has already shown its reader works. From poll close the sync
reads every state every five minutes, and check_missing_counts alerts for
a state with no count NO_COUNT_ALERT_AFTER (an hour) after its polls close.

Nothing of a count is stored here: before polls close nothing of a count is
kept or shown (fetch/poll_close.py). The check stops the day before
election day, when the sync's own checks take over.
"""

import json
import logging
import os
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from app.atomic_write import runtime_data_path, write_text_atomic
from app.election_calendar import previous_election_day
from app.http_client import make_async_client
from app.live_results.sync import _contest_race, contest_is_sane, place_by_candidates
from app.models import Race
from app.ops_alerts import resolve_ops_alert, send_ops_alert
from app.pipeline.fetch.election_results import (
    FeedUnreadable, StateCount, UntrustedCount, fetch_state_count, live_results_states,
)
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

# How long before election day the daily check runs. Every vendor here has
# staged its general by a week out in past cycles (Nebraska's, Washington's
# and Virginia's were up by 2026-10-08, 26 days ahead); two weeks gives a
# reader that fails its self-check fourteen daily retries before the night.
READINESS_DAYS = 14

# Each state's recording: the responses of its last read that passed (a
# staged general all of whose races were accounted for, or a previous
# general read live), as {url: {status, headers, body}}, replayed offline as
# the reader's self-check once the state's index no longer lists that
# election. Written by this module only (RECORDINGS on the data volume);
# the bundled copies are the first-install fallback for states whose index
# had already dropped their previous general when the self-check shipped.
RECORDINGS = "live_results_recordings"
_BUNDLED_RECORDINGS = Path(__file__).resolve().parent.parent / "data" / RECORDINGS
# The response headers a reader reads; nothing else is kept. Never
# Content-Encoding: the body is stored decoded.
_RECORDED_HEADERS = ("content-type", "location", "last-modified")


def readiness_due(election_day: date, today: date) -> bool:
    """Whether the daily check runs `today` (Eastern): inside the window,
    and never on election day itself, when the sync takes over."""
    return 0 < (election_day - today).days <= READINESS_DAYS


# One alert a day for every state that failed, its reasons in the body.
CONDITION = "results-readiness"


def legacy_condition(state: str) -> str:
    """The per-state condition alerts used until 2026-10; every check
    resolves them, so none stays open on the panel."""
    return f"results-readiness-{state}"


@contextmanager
def _recording(client: httpx.AsyncClient | None, into: dict):
    """Record every response `client` receives while open into `into`."""
    if client is None or not hasattr(client, "event_hooks"):
        yield
        return

    async def hook(response: httpx.Response) -> None:
        await response.aread()
        into[str(response.request.url)] = {
            "status": response.status_code,
            "headers": {h: response.headers[h] for h in _RECORDED_HEADERS if h in response.headers},
            "body": response.text,
        }

    before = client.event_hooks
    client.event_hooks = {**before, "response": [*before.get("response", []), hook]}
    try:
        yield
    finally:
        client.event_hooks = before


def _recording_path(state: str) -> str:
    return runtime_data_path(os.path.join(RECORDINGS, f"{state}.json"))


def load_recording(state: str) -> dict | None:
    """The state's recording: the data volume's, else the bundled one."""
    for path in (_recording_path(state), _BUNDLED_RECORDINGS / f"{state}.json"):
        try:
            with open(path) as f:
                recording = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(recording, dict) and recording.get("responses") and recording.get("election_day"):
            return recording
    return None


def save_recording(state: str, election_day: date, responses: dict) -> None:
    path = _recording_path(state)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_text_atomic(path, json.dumps({
            "_source": f"Recorded by live_results/readiness.py from {state}'s feed, read through its reader "
                       f"at {utcnow().isoformat(timespec='seconds')}Z.",
            "state": state, "election_day": election_day.isoformat(), "responses": responses,
        }))
    except OSError:
        logger.exception("Could not record %s's feed", state)


def _replay(recording: dict) -> httpx.AsyncClient:
    responses = recording["responses"]

    def handler(request: httpx.Request) -> httpx.Response:
        got = responses.get(str(request.url))
        if got is None:
            return httpx.Response(404, request=request)
        return httpx.Response(got["status"], headers=got.get("headers") or {}, content=got["body"].encode(),
                              request=request)

    return make_async_client(transport=httpx.MockTransport(handler))


def _unaccounted(
    db: Session, state: str, election_day: date, count: StateCount, offices: tuple[str, ...],
) -> list[str]:
    """The `offices` races on file for `election_day`'s cycle that `count`
    holds no contest for, by label or by candidates. With no such race on
    file, a count holding no federal contest at all is one gap."""
    cycle = election_day.year
    matched = set()
    for contest in count.contests:
        race = _contest_race(db, cycle, state, contest) if contest_is_sane(contest) else None
        if race is not None:
            matched.add(race.id)
    expected = {
        r.id for r in db.query(Race.id).filter(Race.state == state, Race.cycle_year == cycle, Race.office.in_(offices))
    }
    matched |= set(place_by_candidates(db, state, election_day, count.unplaced, matched))
    if not expected:
        return [] if any(c.office in offices for c in count.contests) else ["any federal contest"]
    return sorted(expected - matched)


async def self_check(db: Session, client: httpx.AsyncClient | None, state: str, election_day: date) -> str | None:
    """Why `state`'s reader fails its self-check, or None when it passes:
    the reader, run on the state's previous general (live where its index
    still lists it, else its recording replayed), must account for every
    House seat on file for this cycle."""
    recording = load_recording(state)
    if recording is not None:
        day = date.fromisoformat(recording["election_day"])
        where = f"its recorded {day.isoformat()} feed"
        try:
            async with _replay(recording) as replay:
                count = await fetch_state_count(replay, state, day)
        except (UntrustedCount, FeedUnreadable) as exc:
            return f"its reader failed on {where}: {exc}"
        except Exception as exc:  # a reader bug is a failed self-check
            logger.exception("Self-check replay failed for %s", state)
            return f"its reader failed on {where} ({type(exc).__name__})"
    else:
        day = previous_election_day(election_day - timedelta(days=1))
        where = f"its {day.isoformat()} general, read live"
        responses: dict = {}
        try:
            with _recording(client, responses):
                count = await fetch_state_count(client, state, day)
        except (UntrustedCount, FeedUnreadable) as exc:
            return f"its reader failed on {where}: {exc}"
        except Exception as exc:
            logger.exception("Self-check read failed for %s", state)
            return f"its reader failed on {where} ({type(exc).__name__})"
        if count is None:
            return (f"its index no longer lists its {day.isoformat()} general and no recording of an earlier "
                    "feed is held, so its reader could not be checked")
    if count is None:
        return f"its reader found no election in {where}"
    missing = _unaccounted(db, state, election_day, count, ("H",))
    if missing:
        return f"its reader, on {where}, found no contest for {', '.join(missing)}"
    if recording is None and responses:
        save_recording(state, day, responses)
    return None


async def state_status(
    db: Session, client: httpx.AsyncClient | None, state: str, election_day: date,
) -> tuple[str, str | None]:
    """("ready" | "waiting" | "problem", why) for `state`'s feed."""
    responses: dict = {}
    try:
        with _recording(client, responses):
            count = await fetch_state_count(client, state, election_day)
    except UntrustedCount as refused:
        return "problem", f"its feed was refused: {refused}"
    except FeedUnreadable as unread:
        return "problem", f"its feed could not be read: {unread}"
    except Exception as exc:  # one state's broken feed never stops the check
        logger.exception("Readiness read failed for %s", state)
        return "problem", f"its feed could not be read ({type(exc).__name__})"
    if count is None:
        # The reader read the state's own index or results page and it
        # serves nothing on that day yet (a failed read raises instead).
        failed = await self_check(db, client, state, election_day)
        return ("problem", f"its general is not staged yet, and {failed}") if failed else ("waiting", None)
    if not count.contests and not count.unplaced:
        # New Mexico's staged general, 2026-10-21: its date and type, no
        # contest loaded yet. The reader is proven the same way as an
        # unstaged state's.
        failed = await self_check(db, client, state, election_day)
        return ("problem", f"its general is staged with no contest yet, and {failed}") if failed else ("waiting", None)
    missing = _unaccounted(db, state, election_day, count, ("S", "H"))
    if missing:
        return "problem", (f"its feed lists {len(count.contests) + len(count.unplaced)} contests but none, by "
                           f"label or by candidates, for {', '.join(missing)}")
    if responses:
        save_recording(state, election_day, responses)
    return "ready", None


async def state_readiness(db: Session, client: httpx.AsyncClient | None, state: str, election_day: date) -> str | None:
    """Why `state`'s feed isn't ready for election night, or None (ready,
    or not staged yet with a reader that passes its self-check)."""
    return (await state_status(db, client, state, election_day))[1]


async def check_feed_readiness(
    db: Session, client: httpx.AsyncClient | None, election_day: date, today: date,
) -> dict[str, str]:
    """Read every covered state; one alert a day naming each state that
    failed with its reason (superseded by the next day's), cleared once
    none does. Returns {state: problem}."""
    states = sorted(live_results_states())
    problems: dict[str, str] = {}
    waiting: list[str] = []
    for state in states:
        status, why = await state_status(db, client, state, election_day)
        if status == "problem":
            problems[state] = why
        elif status == "waiting":
            waiting.append(state)
    for state in states:
        resolve_ops_alert(legacy_condition(state))
    if waiting:
        logger.info("Live results: not staged yet, readers passed their self-checks: %s", ", ".join(waiting))
    if not problems:
        resolve_ops_alert(CONDITION)
        return problems
    ready = [s for s in states if s not in problems and s not in waiting]
    send_ops_alert(
        f"Live results readiness: {len(problems)} of {len(states)} states failed ({', '.join(problems)})",
        f"Election day is {election_day.isoformat()}. Each state's feed is read once a day as election "
        "night's sync will read it, and where the general is not staged yet the state's reader is run on its "
        "previous general instead. The states below failed; each is read again tomorrow, and from its "
        "polls' close the sync reads it every five minutes.\n"
        + "\n".join(f"- {state}: {problem}" for state, problem in problems.items())
        + (f"\nNot staged yet, reader self-check passed: {', '.join(waiting)}." if waiting else "")
        + (f"\nReady: {', '.join(ready)}." if ready else ""),
        dedupe_key=f"{CONDITION}-{election_day.isoformat()}-{today.isoformat()}",
        condition=CONDITION,
    )
    return problems
