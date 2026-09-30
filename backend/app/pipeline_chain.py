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
run at once. The chain holding the turn that makes no progress for
STALE_PIPELINE_TIMEOUT — one link that long — is hung, its own alerts say
so, and the next chain takes the turn rather than stall every chain after
it; should the hung one come back, it queues again before its next link.
A chain waiting its turn is alive, whatever the wait: it is never the one
declared hung.

A chain registers as a database writer only for each link it runs
(app.background.writing): waiting for its turn isn't writing, and a data
reset needn't wait for a queue.

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
from dataclasses import dataclass, field

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


@dataclass
class _Chain:
    kind: str
    # Links it has yet to start: a trigger for one of them would repeat it.
    pending: set[str]
    progress: float = field(default_factory=time.monotonic)


class _Queue:
    """Chains in the order they started; the first holds the turn. Polled
    from each chain's own event loop (a cancelled waiter leaves the queue),
    never blocking a thread that can't be woken."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._order: deque[int] = deque()
        self._chains: dict[int, _Chain] = {}
        self._ids = itertools.count(1)

    def _hung(self, chain_id: int, now: float) -> bool:
        return now - self._chains[chain_id].progress > STALE_PIPELINE_TIMEOUT.total_seconds()

    def _live(self) -> "list[_Chain]":
        now = time.monotonic()
        return [chain for i, chain in self._chains.items() if not self._hung(i, now)]

    def join(self, kind: str, labels: list[str], refuse: "Callable[[list[_Chain]], str | None] | None" = None,
             ) -> "tuple[int | None, str | None]":
        """(a place in the queue, None), or (None, why) when `refuse` names a
        reason among the live chains. The check and the joining are one
        step: two requests can't both pass."""
        with self._lock:
            if refuse is not None and (why := refuse(self._live())) is not None:
                return None, why
            chain_id = next(self._ids)
            self._chains[chain_id] = _Chain(kind, set(labels))
            self._order.append(chain_id)
            return chain_id, None

    def turn(self, chain_id: int) -> bool:
        """Whether it is `chain_id`'s turn: it is first, or the chain ahead
        of it is hung (and loses its place). Asking is progress: a waiting
        chain is alive. One that lost its place asks again from the back."""
        with self._lock:
            now = time.monotonic()
            if chain_id in self._chains:
                self._chains[chain_id].progress = now
            if chain_id not in self._order:
                self._order.append(chain_id)
            while self._order[0] != chain_id and self._hung(self._order[0], now):
                hung = self._order.popleft()
                logger.warning("Pipeline chain #%d has run one link past %s — hung; the next chain goes ahead",
                               hung, STALE_PIPELINE_TIMEOUT)
            return self._order[0] == chain_id

    def starting(self, chain_id: int, label: str) -> None:
        with self._lock:
            chain = self._chains.get(chain_id)
            if chain is not None:
                chain.pending.discard(label)
                chain.progress = time.monotonic()

    def leave(self, chain_id: int) -> None:
        with self._lock:
            self._chains.pop(chain_id, None)
            try:
                self._order.remove(chain_id)
            except ValueError:
                pass

    def running(self) -> bool:
        with self._lock:
            return bool(self._live())


_queue = _Queue()


def chain_running() -> bool:
    """A chain in progress, waiting its turn included (none hung): what
    check-and-deploy.sh reads (pipelineChainIsRunning), so a restart doesn't
    drop links a chain has yet to run."""
    return _queue.running()


def reserve(kind: str, labels: list[str]) -> "tuple[int | None, str | None]":
    """A place in the queue now, before the chain's thread starts — or
    (None, why) when it would repeat what a live chain will do: a full run
    while another full run is in progress, or a pipeline another chain has
    yet to start. Given to run_chain(reserved=...), or leave()-d if the
    chain never starts."""
    def refuse(live: "list[_Chain]") -> str | None:
        if kind == FULL and any(chain.kind == FULL for chain in live):
            return "a full run of the pipelines is already in progress"
        for label in labels:
            if any(label in chain.pending for chain in live):
                return f"the {label} pipeline is already due to run in the chain in progress"
        return None

    return _queue.join(kind, labels, refuse)


def queued_behind() -> bool:
    """Whether a chain started now would wait for another."""
    return _queue.running()


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
    """One link and its follow-up, registered as a database writer while it
    runs. Never raises for the link's own failure (an Exception is its
    CRASHED outcome); cancellation and exit do."""
    from app.background import WritesHeld, writing
    from app.pipeline import lease

    try:
        with writing(f"pipeline: {link.label}"):
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
    except WritesHeld:
        # A data reset took the database while this chain waited.
        outcome = Outcome("skipped", {"status": "skipped", "reason": lease.REFUSED_BY_RESET})
    return outcome


async def _wait_turn(chain_id: int, what: str) -> None:
    logged = False
    while not _queue.turn(chain_id):
        if not logged:
            logger.info("Pipeline chain #%d (%s) waiting for the one before it", chain_id, what)
            logged = True
        await asyncio.sleep(POLL_S)


def ends_chain(outcome: Outcome) -> bool:
    """A data reset refused it: every later link would be refused too."""
    from app.pipeline import lease

    return outcome.status == "skipped" and (outcome.result or {}).get("reason") == lease.REFUSED_BY_RESET


async def run_chain(
    links: list[Link], on_outcome: Callable[[Link, Outcome], None] | None = None, *,
    kind: str = "", reserved: int | None = None,
) -> dict[str, Outcome]:
    """Each link in turn, whatever the one before it did, once this chain's
    turn comes. `reserved`: the place a trigger took when it was accepted
    (reserve), else one is taken here."""
    chain_id = reserved if reserved is not None else _queue.join(kind, [link.label for link in links])[0]
    what = ", ".join(link.label for link in links)
    outcomes: dict[str, Outcome] = {}
    try:
        for link in links:
            await _wait_turn(chain_id, what)  # its turn, still or again
            _queue.starting(chain_id, link.label)
            outcome = await _run_link(link)
            _queue.starting(chain_id, link.label)  # progress
            outcomes[link.label] = outcome
            logger.info("%s pipeline: %s", link.label,
                        outcome.result if outcome.result is not None else outcome.status)
            if on_outcome is not None:
                try:
                    on_outcome(link, outcome)
                except Exception:
                    logger.exception("Reporting %s's outcome failed", link.label)
            if ends_chain(outcome):
                logger.info("A data reset holds the database — the rest of this chain is not run")
                break
    finally:
        _queue.leave(chain_id)
    return outcomes
