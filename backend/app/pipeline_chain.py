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
pipeline is running elsewhere, or that another chain began a whole run of
since its own chain began (it got there first), isn't run again. A holder that has held its
turn past STALE_PIPELINE_TIMEOUT is hung — its own alerts say so — and the
next link goes ahead beside it rather than stall every link after it.

Pipelines run only in the pipeline process (settings.PROCESS_ROLE), which
is always one process, so a process-local queue serializes all of them.
"""

import asyncio
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
# How often a link waiting on another run of its pipeline asks the database.
ELSEWHERE_POLL_S = 30.0

# A link's outcome: its run's own status ("completed", "failed", "partial",
# "skipped" …, from the dict it returned), or one of these.
CRASHED = "crashed"  # it raised
RAN_ELSEWHERE = "ran elsewhere"  # another run of it was live or just ran: not repeated


class _Turns:
    """One link at a time, in the order they asked. Polled from each
    chain's own event loop (a cancelled waiter leaves the queue), never
    blocking a thread that can't be woken."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queue: deque[object] = deque()
        self._held_since: float | None = None

    def try_take(self, ticket: object, hung_after: float) -> bool | None:
        """True: the turn is `ticket`'s. False: its turn, beside a holder
        hung past `hung_after` (not held). None: not yet."""
        with self._lock:
            if not self._queue or self._queue[0] is not ticket:
                return None
            if self._held_since is None:
                self._queue.popleft()
                self._held_since = time.monotonic()
                return True
            if time.monotonic() - self._held_since > hung_after:
                self._queue.popleft()
                return False
            return None

    def join(self, ticket: object) -> None:
        with self._lock:
            self._queue.append(ticket)

    def leave(self, ticket: object) -> None:
        with self._lock:
            try:
                self._queue.remove(ticket)
            except ValueError:
                pass

    def release(self) -> None:
        with self._lock:
            self._held_since = None

    def held(self) -> bool:
        with self._lock:
            return self._held_since is not None


_turns = _Turns()
# Chains in progress in this process, waits between links included: what
# check-and-deploy.sh reads (pipelineChainIsRunning), so a restart doesn't
# drop the links a chain has yet to reach, and what a trigger refuses on.
_chains = 0
_chains_lock = threading.Lock()


def chain_running() -> bool:
    with _chains_lock:
        return _chains > 0


@dataclass(frozen=True)
class Link:
    label: str
    run: Callable[[], Awaitable[dict]]
    # Its run-row model: how a run of it elsewhere is seen (run_tracker.live_run).
    model: type
    # After the link, whatever its outcome (a cache the link, or another
    # run of its pipeline, may have changed).
    after: Callable[[], None] | None = None
    # A whole run of its pipeline, which another chain's same link needn't
    # repeat — not a single-senator or fetch-only one.
    whole: bool = True


@dataclass(frozen=True)
class Outcome:
    status: str
    result: dict | None = None
    error: BaseException | None = None


# When each link's pipeline last began a whole run through a chain here:
# another chain that reaches the same link after that doesn't repeat it.
# (A run row can't say so — a single-senator run writes one too.)
_began: dict[str, datetime] = {}
_began_lock = threading.Lock()


def _running_elsewhere(model: type) -> bool:
    from app.database import SessionLocal
    from app.pipeline.run_tracker import run_in_progress

    db = SessionLocal()
    try:
        return run_in_progress(db, model)
    finally:
        db.close()


async def _check_elsewhere(link: Link, since: datetime) -> str | None:
    """'ran' when another chain began a whole run of this link's pipeline
    at or after `since` (this chain's start), 'running' when a run of it is
    live (a run outside any chain), else None. Unreadable is None — the
    link runs, and its pipeline's own lock refuses a real duplicate —
    never a reason to end the chain."""
    with _began_lock:
        began = _began.get(link.label)
    if began is not None and began >= since:
        return "ran"
    try:
        return "running" if await asyncio.to_thread(_running_elsewhere, link.model) else None
    except Exception:
        logger.warning("Could not tell whether %s is running elsewhere — going ahead", link.label, exc_info=True)
        return None


async def _take_turn(label: str) -> bool:
    """Wait for this link's turn; True when it holds it (release after)."""
    ticket = object()
    _turns.join(ticket)
    logged = False
    try:
        while (taken := _turns.try_take(ticket, STALE_PIPELINE_TIMEOUT.total_seconds())) is None:
            if not logged:
                logger.info("%s waiting for the pipeline before it to finish", label)
                logged = True
            await asyncio.sleep(POLL_S)
    except BaseException:
        _turns.leave(ticket)
        raise
    if not taken:
        logger.warning("%s: the pipeline before it has run past %s — going ahead beside it",
                       label, STALE_PIPELINE_TIMEOUT)
    return taken


async def run_link(link: Link, since: datetime) -> Outcome:
    """One link, in its turn: not run if its pipeline is running elsewhere
    (waited out) or ran since `since`, else run. Never raises for the
    link's own failure (an Exception is its CRASHED outcome); cancellation
    and exit still end the chain."""
    from app.time_utils import utcnow

    held = await _take_turn(link.label)
    try:
        where = await _check_elsewhere(link, since)
        if where == "running":
            # A run outside any chain (a pipeline's own entry point) or a
            # holder gone past its time: waited out, at most as long as
            # a run may take.
            deadline = time.monotonic() + STALE_PIPELINE_TIMEOUT.total_seconds()
            logger.info("%s already running elsewhere — waiting for it", link.label)
            while where == "running" and time.monotonic() < deadline:
                await asyncio.sleep(ELSEWHERE_POLL_S)
                where = await _check_elsewhere(link, since)
            where = "ran"  # that run did this link's work (or hung past any wait)
        if where is not None:
            logger.info("%s ran elsewhere — not run again", link.label)
            outcome = Outcome(RAN_ELSEWHERE)
        else:
            if link.whole:
                with _began_lock:
                    _began[link.label] = utcnow()
            try:
                result = await link.run()
            except Exception as error:
                logger.exception("%s pipeline crashed", link.label)
                outcome = Outcome(CRASHED, error=error)
            else:
                result = result if isinstance(result, dict) else {}
                outcome = Outcome(str(result.get("status") or "completed"), result)
    finally:
        if held:
            _turns.release()
    if link.after is not None:
        try:
            link.after()
        except Exception:
            logger.exception("After %s: follow-up failed", link.label)
    return outcome


async def run_chain(
    links: list[Link], on_outcome: Callable[[Link, Outcome], None] | None = None,
) -> dict[str, Outcome]:
    """Each link in turn, whatever the one before it did."""
    from app.time_utils import utcnow

    global _chains
    since = utcnow()
    outcomes: dict[str, Outcome] = {}
    with _chains_lock:
        _chains += 1
    try:
        for link in links:
            outcome = await run_link(link, since)
            outcomes[link.label] = outcome
            logger.info("%s pipeline: %s", link.label,
                        outcome.result if outcome.result is not None else outcome.status)
            if on_outcome is not None:
                try:
                    on_outcome(link, outcome)
                except Exception:
                    logger.exception("Reporting %s's outcome failed", link.label)
    finally:
        with _chains_lock:
            _chains -= 1
    return outcomes


def one_link(link: Link) -> Callable[[], Awaitable[None]]:
    """A single pipeline as a chain of one — for a trigger — so it takes its
    turn like every other run in the process."""
    async def chain() -> None:
        await run_chain([link])

    return chain
