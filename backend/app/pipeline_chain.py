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

One chain runs at a time (claim): a trigger is refused while one is going,
and a nightly run that finds one going leaves the night to it. A link
skipped because another run holds the machine — another run of the same
pipeline, or, for Stock trades, a member pipeline — would otherwise have
the chain move straight on and put two heavy pipelines side by side. So the
chain waits until no pipeline is running, then tries that link once more
and reports what that attempt did: the other run may have been a single
senator, or have failed, so it never stands in for this one. The wait ends
because what it waits on does — every "running" it reads stops counting
once its run is stale (scheduler.pipelines_running, run_tracker.row_is_stale),
and the retry's lock takes over a hung run's.

The one skip that ends a chain is a data reset holding the database: every
later link would be refused the same way.

The process itself dying takes the running link and the rest of the chain
with it: a pipeline is a thread of the process. So a named chain records
the link it is on in a file on the data volume (CHAIN_FILE), removed when
the chain ends; one still there when the pipeline process starts was cut
short, and is resumed from that link once (scheduler.
resume_interrupted_chain). On 2026-10-17 a host `apt upgrade` restarted
the Docker daemon mid-House-run, killing every container: the House run
was left with 175 of 433 members rescored on the new method and Stock
trades and Election didn't run that day. Once, because a link that itself
kills the process (memory, a health-check kill) would kill it again;
ops_alerts.check_pipeline_staleness stays the backstop past that.
"""

import asyncio
import itertools
import json
import logging
import os
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from app.atomic_write import runtime_data_path, write_text_atomic
from app.pipeline import run_tracker
from app.pipeline.run_tracker import ALREADY_RUNNING, MEMBER_PIPELINE_RUNNING, QUIET_PIPELINE_TIMEOUT
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

# A link's outcome: its run's own status ("completed", "failed", "partial",
# "no_data", "skipped" …, from the dict it returned), or CRASHED (it raised).
CRASHED = "crashed"
# Returned no status: a bug in the pipeline, alerted rather than taken for
# a success.
UNKNOWN = "unknown"
# Outcomes that are a lost run of the pipeline, alerted as such.
FAILED = frozenset({CRASHED, UNKNOWN, "failed", "no_data"})
# How often a link held off by another run looks whether it has finished.
WAIT_POLL_S = 30
# The link a named chain is on (run_chain's `name`), on the data volume so
# it outlives the process; read at the pipeline process's start.
CHAIN_FILE = "pipeline_chain.json"


@dataclass(frozen=True)
class Link:
    label: str
    run: Callable[[], Awaitable[dict]]
    # After the link, unless it was skipped (a cache the link may have
    # changed — a crash may have changed it partway).
    after: Callable[[], None] | None = None


@dataclass(frozen=True)
class Cut:
    """A chain the process died in: its name ("Nightly", "Triggered"), when
    it started, the link it was on, and whether it was already a resumption
    of an earlier cut (resumed only once)."""
    name: str
    started_at: datetime
    link: str
    resumed: bool


@dataclass(frozen=True)
class Outcome:
    status: str
    result: dict | None = None
    error: BaseException | None = None


# Chains in progress, by id: the monotonic time of their last progress (a
# link starting or ending, or a look while waiting). What check-and-deploy.sh
# reads (pipelineChainIsRunning), so a restart between two links doesn't
# drop the rest. One with no progress for QUIET_PIPELINE_TIMEOUT — its own,
# or any run's progress write (run_tracker.last_progress), so a link that
# works for longer than that, as a House run on a cache warm-up night does,
# stays live — is wedged, not busy: reported busy it would hold every deploy
# off, the one that fixes it included, and it no longer holds the slot.
_chains: dict[int, float] = {}
_chains_lock = threading.Lock()
_ids = itertools.count(1)


def _any_live(now: float) -> bool:
    window = QUIET_PIPELINE_TIMEOUT.total_seconds()
    # 0.0 is "no run has written progress", not a time: monotonic counts
    # from boot, so read as one it would keep a wedged chain live for the
    # first hours after a restart.
    runs = run_tracker.last_progress() or None
    return any(now - (progress if runs is None else max(progress, runs)) < window for progress in _chains.values())


def chain_running() -> bool:
    now = time.monotonic()
    with _chains_lock:
        return _any_live(now)


def claim() -> int | None:
    """The chain slot: an id to run a chain under (run_chain), or None while
    another chain is running. Taken where the chain is asked for — a trigger
    claims before it answers — so two requests can't both find it free.
    Hand it back with release() if the chain never starts."""
    now = time.monotonic()
    with _chains_lock:
        if _any_live(now):
            return None
        chain_id = next(_ids)
        _chains[chain_id] = now
        return chain_id


def release(chain_id: int) -> None:
    with _chains_lock:
        _chains.pop(chain_id, None)


def _progress(chain_id: int) -> None:
    with _chains_lock:
        _chains[chain_id] = time.monotonic()


def _record(cut: Cut | None) -> None:
    """Write the link a named chain is on, or (None) clear it. A failure is
    logged, never raised: the chain still runs, unrecorded."""
    path = runtime_data_path(CHAIN_FILE)
    try:
        if cut is None:
            if os.path.exists(path):
                os.remove(path)
        else:
            write_text_atomic(path, json.dumps({
                "name": cut.name, "startedAt": cut.started_at.isoformat(), "link": cut.link,
                "resumed": cut.resumed,
            }))
    except OSError:
        logger.exception("Recording the pipeline chain's link failed")


def take_cut() -> Cut | None:
    """The chain a dead process left mid-link, cleared as it is read: what
    the pipeline process reads at its start, before any chain can begin. A
    file that can't be read is logged and cleared — no chain is resumed
    from a guess."""
    path = runtime_data_path(CHAIN_FILE)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        return Cut(str(doc["name"]), datetime.fromisoformat(doc["startedAt"]), str(doc["link"]),
                   bool(doc["resumed"]))
    except (OSError, ValueError, KeyError, TypeError):
        logger.exception("The interrupted pipeline chain's record can't be read — not resumed")
        return None
    finally:
        _record(None)


def _skip_reason(outcome: Outcome) -> str | None:
    return (outcome.result or {}).get("reason") if outcome.status == "skipped" else None


def ends_chain(outcome: Outcome) -> bool:
    """A data reset refused it: every later link would be refused too."""
    from app.pipeline import lease

    return _skip_reason(outcome) == lease.REFUSED_BY_RESET


def held_off(outcome: Outcome) -> bool:
    """Skipped because another run holds the machine: another run of the same
    pipeline (its run lock, ALREADY_RUNNING, or its job's lease,
    REFUSED_HELD), or a member pipeline holding Stock trades off. Not a
    House run that already waited out a refresh holding the district lines
    (run_tracker.LINES_HELD_TOO_LONG): that wait was the link's, and this
    chain's (no pipeline running) wouldn't see the refresh anyway."""
    from app.pipeline import lease

    return _skip_reason(outcome) in (ALREADY_RUNNING, lease.REFUSED_HELD, MEMBER_PIPELINE_RUNNING)


async def _wait_out(chain_id: int, busy: Callable[[], bool]) -> None:
    """Until busy() is false — each run it reads stops counting once stale
    (run_tracker.row_is_stale), so this ends. The chain's progress meanwhile:
    it is alive, only waiting."""
    while True:
        try:
            if not busy():
                return
        except Exception:
            # Can't tell: take it as free — the retry's own lock refuses it
            # if not.
            logger.exception("Checking for a running pipeline failed")
            return
        _progress(chain_id)
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
    busy: Callable[[], bool] | None = None, chain_id: int | None = None,
    name: str | None = None, started_at: datetime | None = None, resumed: bool = False,
) -> dict[str, Outcome] | None:
    """Each link in turn, whatever the one before it did, under `chain_id`
    (claimed already, or claimed here — None, and nothing runs, while
    another chain holds the slot). `busy`: whether any pipeline is running —
    what a link held off by another run waits on before its one retry
    (without it, the chain moves straight on). `name`: record the link the
    chain is on (CHAIN_FILE, with `started_at`, by default now, and
    `resumed`), so a chain the process dies in is resumed at its start."""
    if chain_id is None:
        chain_id = claim()
        if chain_id is None:
            logger.info("Another pipeline chain is running — this one is not started")
            return None
    outcomes: dict[str, Outcome] = {}
    started_at = started_at or utcnow()
    try:
        for link in links:
            if name is not None:
                _record(Cut(name, started_at, link.label, resumed))
            _progress(chain_id)
            outcome = await _run_link(link)
            if busy is not None and held_off(outcome):
                logger.info("%s pipeline held off by another run — waiting it out, then trying again",
                            link.label)
                await _wait_out(chain_id, busy)
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
                logger.warning("A data reset holds the database — the rest of this chain is not run")
                break
    finally:
        # A chain that ends here, however, was not cut short. (A process
        # that dies runs no finally in its daemon threads: the record stays.)
        if name is not None:
            _record(None)
        release(chain_id)
    return outcomes
