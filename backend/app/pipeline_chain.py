"""Running pipelines one after another, each independent of the last.

The nightly run is five pipelines in order — Senate, Supplementary, House,
Stock trades, Election — and the admin triggers run some of them. They run
one at a time because the Pi can't hold two in memory at once, not because
one needs another's output from the same night: each reads whatever the
database holds, and yesterday's data is a fine input when today's run of
the one before it didn't happen. So a link that is skipped, fails or
crashes is reported and the next link runs anyway. (Until 2026-09 any of
those ended the chain there: a Supplementary failure once left House,
Stock trades and Election unrun for 19 nights.) The one skip that ends a
chain is a data reset holding the database: every later link would be
refused the same way. What this can't cover is the process itself dying —
a kill takes the rest of that chain with it;
ops_alerts.check_pipeline_staleness is the backstop for that.

Chains run one at a time, in the order they started: a trigger sent while
the nightly chain runs waits for it, and vice versa, so two pipelines never
run at once. A chain that has made no progress for STALE_PIPELINE_TIMEOUT
is hung — its own alerts say so — and loses its turn to the next rather
than stall every chain after it.

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

from app.pipeline.run_tracker import STALE_PIPELINE_TIMEOUT

logger = logging.getLogger(__name__)

# How often a chain waiting its turn looks again.
POLL_S = 1.0

# A link's outcome: its run's own status ("completed", "failed", "partial",
# "no_data", "skipped" …, from the dict it returned), or CRASHED (it raised).
CRASHED = "crashed"
# Outcomes that are a lost run of the pipeline, alerted as such.
FAILED = frozenset({CRASHED, "failed", "no_data"})

FULL = "full"  # the nightly run's five links: the nightly chain, or a full trigger


class _Queue:
    """Chains in the order they started; the first holds the turn. Polled
    from each chain's own event loop (a cancelled waiter leaves the queue),
    never blocking a thread that can't be woken."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._order: deque[int] = deque()
        # id -> (kind, monotonic time of its last progress)
        self._chains: dict[int, tuple[str, float]] = {}
        self._ids = itertools.count(1)

    def _hung(self, chain_id: int, now: float) -> bool:
        return now - self._chains[chain_id][1] > STALE_PIPELINE_TIMEOUT.total_seconds()

    def _live(self, kind: str | None, now: float) -> bool:
        return any((kind is None or k == kind) and not self._hung(i, now) for i, (k, _) in self._chains.items())

    def join(self, kind: str, *, unless_running: str | None = None) -> int | None:
        """A place in the queue, or None when a chain of `unless_running`'s
        kind is in progress. The check and the joining are one step."""
        with self._lock:
            now = time.monotonic()
            if unless_running is not None and self._live(unless_running, now):
                return None
            chain_id = next(self._ids)
            self._chains[chain_id] = (kind, now)
            self._order.append(chain_id)
            return chain_id

    def first(self, chain_id: int) -> bool:
        """Whether it is `chain_id`'s turn: it is first, or everything ahead
        of it is hung (and drops out)."""
        with self._lock:
            now = time.monotonic()
            while self._order and self._order[0] != chain_id and self._hung(self._order[0], now):
                hung = self._order.popleft()
                logger.warning("Pipeline chain #%d has made no progress in %s — hung; the next goes ahead",
                               hung, STALE_PIPELINE_TIMEOUT)
            return bool(self._order) and self._order[0] == chain_id

    def progress(self, chain_id: int) -> None:
        with self._lock:
            if chain_id in self._chains:
                self._chains[chain_id] = (self._chains[chain_id][0], time.monotonic())

    def leave(self, chain_id: int) -> None:
        with self._lock:
            self._chains.pop(chain_id, None)
            try:
                self._order.remove(chain_id)
            except ValueError:
                pass

    def running(self, kind: str | None = None) -> bool:
        with self._lock:
            return self._live(kind, time.monotonic())


_queue = _Queue()


def chain_running(kind: str | None = None) -> bool:
    """A chain in progress, waiting its turn included (none hung): what
    check-and-deploy.sh reads (pipelineChainIsRunning), so a restart doesn't
    drop links a chain has yet to run — and what a full run is refused on."""
    return _queue.running(kind)


def reserve(kind: str) -> int | None:
    """A place in the queue now, before the chain's thread starts, or None
    while a chain of `kind` is in progress (two requests can't both pass).
    Given to run_chain(reserved=...), or leave()-d if it never starts."""
    return _queue.join(kind, unless_running=kind)


def leave(chain_id: int) -> None:
    _queue.leave(chain_id)


@dataclass(frozen=True)
class Link:
    label: str
    run: Callable[[], Awaitable[dict]]
    # After the link, whatever its outcome, still in the chain's turn (a
    # cache the link may have changed).
    after: Callable[[], None] | None = None


@dataclass(frozen=True)
class Outcome:
    status: str
    result: dict | None = None
    error: BaseException | None = None


async def _run_link(link: Link) -> Outcome:
    """One link and its follow-up. Never raises for the link's own failure
    (an Exception is its CRASHED outcome); cancellation and exit do."""
    try:
        result = await link.run()
    except Exception as error:
        logger.exception("%s pipeline crashed", link.label)
        outcome = Outcome(CRASHED, error=error)
    else:
        result = result if isinstance(result, dict) else {}
        outcome = Outcome(str(result.get("status") or "completed"), result)
    if link.after is not None:
        try:
            link.after()
        except Exception:
            logger.exception("After %s: follow-up failed", link.label)
    return outcome


async def run_chain(
    links: list[Link], on_outcome: Callable[[Link, Outcome], None] | None = None, *,
    kind: str = "", reserved: int | None = None,
) -> dict[str, Outcome]:
    """Each link in turn, whatever the one before it did, once this chain's
    turn comes. `reserved`: the place a trigger took when it was accepted
    (reserve), else one is taken here."""
    from app.pipeline import lease

    chain_id = reserved if reserved is not None else _queue.join(kind)
    outcomes: dict[str, Outcome] = {}
    try:
        logged = False
        while not _queue.first(chain_id):
            if not logged:
                logger.info("Pipeline chain #%d (%s) waiting for the one before it",
                            chain_id, ", ".join(link.label for link in links))
                logged = True
            await asyncio.sleep(POLL_S)
        for link in links:
            _queue.progress(chain_id)
            outcome = await _run_link(link)
            _queue.progress(chain_id)
            outcomes[link.label] = outcome
            logger.info("%s pipeline: %s", link.label,
                        outcome.result if outcome.result is not None else outcome.status)
            if on_outcome is not None:
                try:
                    on_outcome(link, outcome)
                except Exception:
                    logger.exception("Reporting %s's outcome failed", link.label)
            if outcome.status == "skipped" and (outcome.result or {}).get("reason") == lease.REFUSED_BY_RESET:
                # Every later link would be refused the same way.
                logger.info("A data reset holds the database — the rest of this chain is not run")
                break
    finally:
        _queue.leave(chain_id)
    return outcomes
