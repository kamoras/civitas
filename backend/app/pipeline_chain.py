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

The one skip that ends a chain is a data reset holding the database: every
later link would be refused the same way. What this can't cover is the
process itself dying — a kill takes the rest of the chain with it;
ops_alerts.check_pipeline_staleness is the backstop for that.
"""

import itertools
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.pipeline.run_tracker import STALE_PIPELINE_TIMEOUT

logger = logging.getLogger(__name__)

# A link's outcome: its run's own status ("completed", "failed", "partial",
# "no_data", "skipped" …, from the dict it returned), or CRASHED (it raised).
CRASHED = "crashed"
# Outcomes that are a lost run of the pipeline, alerted as such.
FAILED = frozenset({CRASHED, "failed", "no_data"})


@dataclass(frozen=True)
class Link:
    label: str
    run: Callable[[], Awaitable[dict]]
    # After the link, whatever its outcome (a cache the link may have
    # changed).
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


def ends_chain(outcome: Outcome) -> bool:
    """A data reset refused it: every later link would be refused too."""
    from app.pipeline import lease

    return outcome.status == "skipped" and (outcome.result or {}).get("reason") == lease.REFUSED_BY_RESET


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
    links: list[Link], on_outcome: Callable[[Link, Outcome], None] | None = None,
) -> dict[str, Outcome]:
    """Each link in turn, whatever the one before it did."""
    chain_id = next(_ids)
    outcomes: dict[str, Outcome] = {}
    try:
        for link in links:
            _progress(chain_id)
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
                logger.info("A data reset holds the database — the rest of this chain is not run")
                break
    finally:
        with _chains_lock:
            _chains.pop(chain_id, None)
    return outcomes
