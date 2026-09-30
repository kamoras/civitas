"""Running pipelines one after another, each independent of the last.

The nightly run is five pipelines in order — Senate, Supplementary, House,
Stock trades, Election — and the admin triggers run some of them. They run
one at a time because the Pi can't hold two in memory at once, not because
one needs another's output from the same night: each reads whatever the
database holds, and yesterday's data is a fine input when today's run of
the one before it didn't happen. So a link that is skipped, fails or
crashes is reported and the next link runs anyway. (Until 2026-09 any of
those ended the chain there: a Supplementary failure once left House,
Stock trades and Election unrun for 19 nights.) What this can't cover is
the process itself dying — a kill takes the rest of that night's chain
with it; ops_alerts.check_pipeline_staleness is the backstop for that.

Every chain in the process takes turns on one queue, a link at a time, in
arrival order: a manual run and the nightly one interleave rather than run
two pipelines at once, and neither starves behind the other. A link whose
pipeline another chain has completed since this chain began (it got there
first) isn't run again, nor is one whose run outside any chain is waited
out. A turn held past STALE_PIPELINE_TIMEOUT is hung — its own alerts say
so — and the next link takes the turn from it rather than stall every link
after it; the queue goes on serializing the rest.

Pipelines run only in the pipeline process (settings.PROCESS_ROLE), which
is always one process, so a process-local queue serializes all of them.
"""

import asyncio
import itertools
import logging
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from app.pipeline.run_tracker import STALE_PIPELINE_TIMEOUT

logger = logging.getLogger(__name__)

# How often a link waiting its turn looks again.
POLL_S = 1.0
# How often a link waiting on a run outside any chain asks the database.
ELSEWHERE_POLL_S = 30.0

# A link's outcome: its run's own status ("completed", "failed", "partial",
# "skipped" …, from the dict it returned), or one of these.
CRASHED = "crashed"  # it raised
RAN_ELSEWHERE = "ran elsewhere"  # another run of it did the work: not repeated
# Statuses that are not a finished run another chain may count as its own.
NOT_DONE = frozenset({"skipped", "failed", CRASHED})


class _Turns:
    """One link at a time, in the order they asked. Polled from each
    chain's own event loop (a cancelled waiter leaves the queue), never
    blocking a thread that can't be woken."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queue: deque[object] = deque()
        self._holder: object | None = None
        self._held_since = 0.0

    def try_take(self, ticket: object, hung_after: float) -> str | None:
        """'held' when the turn is `ticket`'s; 'taken' when it is, from a
        holder hung past `hung_after` (whose late release then frees
        nothing); None: not yet."""
        with self._lock:
            if not self._queue or self._queue[0] is not ticket:
                return None
            if self._holder is None:
                how = "held"
            elif time.monotonic() - self._held_since > hung_after:
                how = "taken"
            else:
                return None
            self._queue.popleft()
            self._holder, self._held_since = ticket, time.monotonic()
            return how

    def join(self, ticket: object) -> None:
        with self._lock:
            self._queue.append(ticket)

    def leave(self, ticket: object) -> None:
        with self._lock:
            try:
                self._queue.remove(ticket)
            except ValueError:
                pass

    def release(self, ticket: object) -> None:
        with self._lock:
            if self._holder is ticket:
                self._holder = None

    def held(self) -> bool:
        with self._lock:
            return self._holder is not None


_turns = _Turns()

# Chains in progress, waits between links included, by id: (kind, monotonic
# time of their last progress). What check-and-deploy.sh reads
# (pipelineChainIsRunning) so a restart doesn't drop the links a chain has
# yet to run, and what a full trigger is refused on. One with no progress
# for STALE_PIPELINE_TIMEOUT is wedged, not busy: it would hold every
# deploy off, the one that fixes it included.
FULL = "full"  # the nightly run's five links (the nightly chain, a full trigger)
_chains: dict[int, tuple[str, float]] = {}
_chains_lock = threading.Lock()
_ids = itertools.count(1)


def _live(kind: str | None, now: float) -> bool:
    return any(
        (kind is None or k == kind) and now - progress < STALE_PIPELINE_TIMEOUT.total_seconds()
        for k, progress in _chains.values()
    )


def chain_running(kind: str | None = None) -> bool:
    with _chains_lock:
        return _live(kind, time.monotonic())


def reserve(kind: str) -> int | None:
    """Register a chain now — before its thread starts — or None when one of
    `kind` is running (a second full chain would queue behind the first and
    redo its pipelines). Two requests can't both pass: the check and the
    registration are one step. forget() it if the chain never starts."""
    with _chains_lock:
        now = time.monotonic()
        if _live(kind, now):
            return None
        chain_id = next(_ids)
        _chains[chain_id] = (kind, now)
        return chain_id


def _progress(chain_id: int) -> None:
    with _chains_lock:
        if chain_id in _chains:
            _chains[chain_id] = (_chains[chain_id][0], time.monotonic())


def forget(chain_id: int) -> None:
    with _chains_lock:
        _chains.pop(chain_id, None)


@dataclass(frozen=True)
class Link:
    label: str
    run: Callable[[], Awaitable[dict]]
    # Its run-row model: how a run of it outside any chain is seen
    # (run_tracker.live_run).
    model: type
    # After the link, whatever its outcome, in its turn (a cache the link,
    # or another run of its pipeline, may have changed).
    after: Callable[[], None] | None = None
    # A whole run of its pipeline, which another chain's same link needn't
    # repeat — not a single-senator or fetch-only one.
    whole: bool = True


@dataclass(frozen=True)
class Outcome:
    status: str
    result: dict | None = None
    error: BaseException | None = None


# When each link's pipeline last finished a whole run through a chain here,
# other than skipped, failed or crashed: another chain that began before
# then doesn't repeat it. (A run row can't say so — a single-senator run
# writes one too.)
_completed: dict[str, datetime] = {}
_completed_lock = threading.Lock()


def _running_elsewhere(model: type) -> bool:
    from app.database import SessionLocal
    from app.pipeline.run_tracker import run_in_progress

    db = SessionLocal()
    try:
        return run_in_progress(db, model)
    finally:
        db.close()


async def _is_running_elsewhere(link: Link) -> bool:
    """A run of this link's pipeline live outside any chain (every chain's
    runs take turns, and this link holds the turn). Off the loop; unreadable
    is False — the link runs, and its pipeline's own lock refuses a real
    duplicate — never a reason to end the chain."""
    try:
        return await asyncio.to_thread(_running_elsewhere, link.model)
    except Exception:
        logger.warning("Could not tell whether %s is running elsewhere — going ahead", link.label, exc_info=True)
        return False


async def _take_turn(label: str) -> object:
    """Wait for this link's turn; the ticket to release it with."""
    ticket = object()
    _turns.join(ticket)
    logged = False
    try:
        while (how := _turns.try_take(ticket, STALE_PIPELINE_TIMEOUT.total_seconds())) is None:
            if not logged:
                logger.info("%s waiting for the pipeline before it to finish", label)
                logged = True
            await asyncio.sleep(POLL_S)
    except BaseException:
        _turns.leave(ticket)
        raise
    if how == "taken":
        logger.warning("%s: the pipeline before it has held its turn past %s — hung; going ahead",
                       label, STALE_PIPELINE_TIMEOUT)
    return ticket


async def _run_in_turn(link: Link, since: datetime) -> Outcome:
    from app.time_utils import utcnow

    with _completed_lock:
        completed = _completed.get(link.label)
    if link.whole and completed is not None and completed >= since:
        logger.info("%s completed by another chain since this one began — not run again", link.label)
        return Outcome(RAN_ELSEWHERE)
    if await _is_running_elsewhere(link):
        # A run outside any chain: waited out, at most as long as a run
        # may take; then this link runs, and its pipeline's own lock
        # decides whether that other run is still live.
        deadline = time.monotonic() + STALE_PIPELINE_TIMEOUT.total_seconds()
        logger.info("%s already running elsewhere — waiting for it", link.label)
        while await _is_running_elsewhere(link):
            if time.monotonic() >= deadline:
                logger.warning("%s: the run elsewhere has gone past %s — running it anyway",
                               link.label, STALE_PIPELINE_TIMEOUT)
                break
            await asyncio.sleep(ELSEWHERE_POLL_S)
        else:
            logger.info("%s ran elsewhere — not run again", link.label)
            return Outcome(RAN_ELSEWHERE)
    try:
        result = await link.run()
    except Exception as error:
        logger.exception("%s pipeline crashed", link.label)
        return Outcome(CRASHED, error=error)
    result = result if isinstance(result, dict) else {}
    outcome = Outcome(str(result.get("status") or "completed"), result)
    if link.whole and outcome.status not in NOT_DONE:
        with _completed_lock:
            _completed[link.label] = utcnow()
    return outcome


async def run_link(link: Link, since: datetime) -> Outcome:
    """One link, in its turn, with its follow-up. Never raises for the
    link's own failure (an Exception is its CRASHED outcome); cancellation
    and exit still end the chain."""
    ticket = await _take_turn(link.label)
    try:
        outcome = await _run_in_turn(link, since)
        if link.after is not None:
            try:
                link.after()
            except Exception:
                logger.exception("After %s: follow-up failed", link.label)
        return outcome
    finally:
        _turns.release(ticket)


async def run_chain(
    links: list[Link], on_outcome: Callable[[Link, Outcome], None] | None = None, *,
    kind: str = "", reserved: int | None = None,
) -> dict[str, Outcome]:
    """Each link in turn, whatever the one before it did. `reserved`: the
    registration a trigger took at request time (reserve), else one is
    taken here."""
    from app.time_utils import utcnow

    since = utcnow()
    chain_id = reserved
    if chain_id is None:
        with _chains_lock:
            chain_id = next(_ids)
            _chains[chain_id] = (kind, time.monotonic())
    outcomes: dict[str, Outcome] = {}
    try:
        for link in links:
            _progress(chain_id)
            outcome = await run_link(link, since)
            _progress(chain_id)
            outcomes[link.label] = outcome
            logger.info("%s pipeline: %s", link.label,
                        outcome.result if outcome.result is not None else outcome.status)
            if on_outcome is not None:
                try:
                    on_outcome(link, outcome)
                except Exception:
                    logger.exception("Reporting %s's outcome failed", link.label)
    finally:
        forget(chain_id)
    return outcomes


def one_link(link: Link) -> Callable[[], Awaitable[None]]:
    """A single pipeline as a chain of one — for a trigger — so it takes its
    turn like every other run in the process."""
    async def chain() -> None:
        await run_chain([link])

    return chain
