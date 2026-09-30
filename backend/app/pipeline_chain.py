"""Running pipelines one after another, each independent of the last.

The nightly run is five pipelines in order — Senate, Supplementary, House,
Stock trades, Election — and a full trigger runs the same five. They run
one after another because the Pi can't hold two in memory at once, not
because one needs another's output from the same night: each reads
whatever the database holds, and yesterday's data is a fine input when
today's run of the one before it didn't happen. So a link that is skipped,
fails or crashes is reported and the next link runs anyway. (Until 2026-09
any of those ended the chain there: a Supplementary failure once left
House, Stock trades and Election unrun for 19 nights.)

A link skipped because another run holds the machine — another run of
the same pipeline (a single trigger, or another chain), or, for Stock
trades, a member pipeline — is not a lost run, and moving straight on would
put two heavy pipelines side by side. So the chain waits that run out
first: another run of the same pipeline refreshes its data, and the chain
moves on; a Stock trades run held off by a member pipeline is tried again.
A run that holds the machine past STALE_PIPELINE_TIMEOUT is hung, and the
chain ends there (WEDGED) rather than wait on it forever.

The skips that end a chain are that one and a data reset holding the
database: every later link would be refused the same way. What this can't
cover is the process itself dying — a kill takes the rest of the chain with
it; ops_alerts.check_pipeline_staleness is the backstop for that.
"""

import asyncio
import itertools
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.pipeline.run_tracker import ALREADY_RUNNING, MEMBER_PIPELINE_RUNNING, STALE_PIPELINE_TIMEOUT

logger = logging.getLogger(__name__)

# A link's outcome: its run's own status ("completed", "failed", "partial",
# "no_data", "skipped" …, from the dict it returned), or CRASHED (it raised).
CRASHED = "crashed"
# Held off by another run that outlived STALE_PIPELINE_TIMEOUT: the chain
# stops waiting and ends.
WEDGED = "wedged"
# Returned no status: a bug in the pipeline, alerted rather than taken for
# a success.
UNKNOWN = "unknown"
# Outcomes that are a lost run of the pipeline, alerted as such.
FAILED = frozenset({CRASHED, WEDGED, UNKNOWN, "failed", "no_data"})
# Waiting on another run: how often to look, and for how long at most.
WAIT_POLL_S = 30


@dataclass(frozen=True)
class Link:
    label: str
    run: Callable[[], Awaitable[dict]]
    # After the link, unless it was skipped (a cache the link may have
    # changed — a crash may have changed it partway).
    after: Callable[[], None] | None = None


@dataclass(frozen=True)
class Outcome:
    status: str
    result: dict | None = None
    error: BaseException | None = None


# Chains in progress, by id: the monotonic time of their last progress (a
# link starting or ending). What check-and-deploy.sh reads
# (pipelineChainIsRunning), so a restart between two links doesn't drop the
# rest. One with no progress for STALE_PIPELINE_TIMEOUT — one link that
# long — is wedged, not busy: reported busy it would hold every deploy off,
# the one that fixes it included.
_chains: dict[int, float] = {}
_chains_lock = threading.Lock()
_ids = itertools.count(1)


def chain_running() -> bool:
    now = time.monotonic()
    with _chains_lock:
        return any(now - progress < STALE_PIPELINE_TIMEOUT.total_seconds() for progress in _chains.values())


def _progress(chain_id: int) -> None:
    with _chains_lock:
        _chains[chain_id] = time.monotonic()


def _skip_reason(outcome: Outcome) -> str | None:
    return (outcome.result or {}).get("reason") if outcome.status == "skipped" else None


def ends_chain(outcome: Outcome) -> bool:
    """A data reset refused it, or another run held the machine past the
    point of being hung: every later link would be refused too."""
    from app.pipeline import lease

    return outcome.status == WEDGED or _skip_reason(outcome) == lease.REFUSED_BY_RESET


def ran_elsewhere(outcome: Outcome) -> bool:
    """Skipped because another run of the same pipeline holds it — its run
    lock (ALREADY_RUNNING) or its job's lease (REFUSED_HELD): that run
    refreshes the pipeline's data, so this is no lost run."""
    from app.pipeline import lease

    return _skip_reason(outcome) in (ALREADY_RUNNING, lease.REFUSED_HELD)


def _held_off(outcome: Outcome) -> bool:
    """Skipped because another run holds the machine: the chain waits it out."""
    return ran_elsewhere(outcome) or _skip_reason(outcome) == MEMBER_PIPELINE_RUNNING


async def _wait_out(busy: Callable[[], bool]) -> bool:
    """Until busy() is false (True), or STALE_PIPELINE_TIMEOUT has passed
    (False: whatever holds the machine is hung)."""
    deadline = time.monotonic() + STALE_PIPELINE_TIMEOUT.total_seconds()
    while True:
        try:
            if not busy():
                return True
        except Exception:
            # Can't tell: take it as free — the next link's own lock refuses
            # it if not.
            logger.exception("Checking for a running pipeline failed")
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(WAIT_POLL_S)


async def _run_link(link: Link) -> Outcome:
    """One link and its follow-up. Never raises for the link's own failure
    (an Exception is its CRASHED outcome); cancellation and exit do."""
    try:
        result = await link.run()
    except Exception as error:
        logger.exception("%s pipeline crashed", link.label)
        outcome = Outcome(CRASHED, error=error)
    else:
        # Every pipeline says how it went; one that doesn't is not taken
        # for a success.
        result = result if isinstance(result, dict) else {}
        outcome = Outcome(str(result.get("status") or UNKNOWN), result)
    if link.after is not None and outcome.status != "skipped":
        try:
            link.after()
        except Exception:
            logger.exception("After %s: follow-up failed", link.label)
    return outcome


async def run_chain(
    links: list[Link], on_outcome: Callable[[Link, Outcome], None] | None = None,
    busy: Callable[[], bool] | None = None,
) -> dict[str, Outcome]:
    """Each link in turn, whatever the one before it did. `busy`: whether
    any pipeline is running — what a link held off by another run waits on
    (without it, the chain moves straight on)."""
    chain_id = next(_ids)
    outcomes: dict[str, Outcome] = {}
    try:
        for link in links:
            _progress(chain_id)
            outcome = await _run_link(link)
            if busy is not None and _held_off(outcome):
                logger.info("%s pipeline held off by another run — waiting it out", link.label)
                # Still this chain's progress while it waits (up to the
                # staleness cap, which is the wait's bound too).
                _progress(chain_id)
                if not await _wait_out(busy):
                    outcome = Outcome(WEDGED, {
                        "status": WEDGED, "reason": (outcome.result or {}).get("reason"),
                        "error": f"another run held it off for over {STALE_PIPELINE_TIMEOUT}",
                    })
                elif not ran_elsewhere(outcome):
                    # Held off by another pipeline, not run by it.
                    outcome = await _run_link(link)
            _progress(chain_id)
            outcomes[link.label] = outcome
            logger.info("%s pipeline: %s", link.label,
                        outcome.result if outcome.result is not None else outcome.status)
            if on_outcome is not None:
                try:
                    on_outcome(link, outcome)
                except Exception:
                    logger.exception("Reporting %s's outcome failed", link.label)
            if ends_chain(outcome):
                logger.warning("%s pipeline: %s — the rest of this chain is not run", link.label, outcome.status)
                break
    finally:
        with _chains_lock:
            _chains.pop(chain_id, None)
    return outcomes
