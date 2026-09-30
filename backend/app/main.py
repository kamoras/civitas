import asyncio
import os
import threading
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import logging
from app.config import settings

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api.cache_headers import ETagCacheMiddleware
from app.api.router import api_router
from app.database import init_db
from app.scheduler import start_scheduler, stop_scheduler
from app.background import WritesElsewhere, WritesHeld, start_writer, writing

# Configure logging level from PIPELINE_LOG_LEVEL env setting
_level_name = (settings.PIPELINE_LOG_LEVEL or "info").upper()
_level = getattr(logging, _level_name, logging.INFO)
# Add a StreamHandler to root so app.* loggers have somewhere to write
_root = logging.getLogger()
if not _root.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    _root.addHandler(_handler)
_root.setLevel(_level)
for _n in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi", "app"):
    logging.getLogger(_n).setLevel(_level)
# httpx logs every request URL at INFO — including api_key query params,
# which must not end up in docker logs. Keep third-party HTTP loggers at
# WARNING regardless of the app log level.
for _n in ("httpx", "httpcore"):
    logging.getLogger(_n).setLevel(logging.WARNING)


async def _bootstrap_explore() -> None:
    """Run explore pipeline once at startup if the document store is empty."""
    await asyncio.sleep(5)
    try:
        from app.database import SessionLocal
        from app.models import ExploreDocument

        db = SessionLocal()
        count = db.query(ExploreDocument.id).limit(1).first()
        db.close()

        if count is None:
            _logger = logging.getLogger("app.main")
            _logger.info("Explore document store is empty — running initial ingestion")
            from app.pipeline.explore_pipeline import run_explore_pipeline
            from app.pipeline import lease

            # Registered for the admin data reset: the pipeline hands its
            # writes to threads while this awaits. And a lease, so a reset in
            # another process sees it too.
            with writing("Explore bootstrap"):
                async with lease.job_async(lease.EXPLORE) as held:
                    if held:
                        await run_explore_pipeline(days_back=60)
    except WritesHeld as held:
        logging.getLogger("app.main").info("%s", held)
    except Exception as e:
        logging.getLogger("app.main").warning("Explore bootstrap failed: %s", e)


def _preload_models() -> None:
    """Load the similarity model Explore search encodes queries with
    (vector_store.search_explore_documents) at startup, so no read request
    loads one (AGENTS.md: "never load the embedding model or LLM on API read
    requests"). Only that one: the primary model served /api/qa's intent
    classification, which is gone, and every API worker would hold a copy
    no request uses."""
    from app.pipeline import vector_store

    try:
        vector_store.get_similarity_model()
    except Exception as e:
        logging.getLogger("app.main").warning("Embedding model preload failed: %s", e)


def _invalidate_orphaned_pipelines() -> None:
    """Mark stale the 'running' pipeline rows a restart left behind, for
    every pipeline (run_tracker.sweep_orphaned_runs).

    Pipelines run in threads of the backend process, so a restart kills
    them without letting them record it: any 'running' row is left over
    from a prior crash or deploy. (check-and-deploy.sh does not deploy
    while one runs, which is what keeps a start-first rollout's overlap
    from sweeping a live run.) Every table, not just the Senate's: an
    unswept row reads as "running" on the admin dashboard and blocks a
    manual trigger until STALE_PIPELINE_TIMEOUT ages it out. The one
    exception is a Senate row whose lease still holds — it may be a run
    live in the other task, the one case that can be seen.
    """
    from app.pipeline.fetch.district_pvi import release_orphaned_holds
    from app.pipeline.run_tracker import sweep_orphaned_runs

    sweep_orphaned_runs()
    # And the district lines' lease a killed House run, refresh or startup
    # rescore held (the House run's row is among those just swept).
    release_orphaned_holds()


# How often the API process checks that the pipeline service is alive, and
# how long it waits after its own start first. Short: the staleness is
# measured from the heartbeat file's own time (ops_alerts'
# PIPELINE_SERVICE_SILENT_AFTER), and a restarted pipeline beats at once, so
# the wait only covers the two services starting together. A long one would
# restart with every deploy — on a day of frequent deploys, the check would
# never run at all.
_LIVENESS_EVERY_S = 300
_LIVENESS_GRACE_S = 120


def _rescore_house_on_current_lines(session_factory) -> "tuple[list[str], object | None]":
    """One attempt at the House part of the startup Constituent Alignment
    rescore: (chambers rescored, None), or ([], the lease refusal) when the
    district lines are held elsewhere. Only when the House is stale.

    Under the DISTRICT_LINES lease (who=RESCORE_WHO) across the read and
    the commit: a House run (or a refresh) takes it before it switches the
    lines, and a triggered House run has no HousePipelineRun row yet while
    it does — the rescore's run_in_progress check can't see it, and would
    commit the old lines' scores over the run's fresh ones. A House run
    that finds the rescore holding it waits (district_pvi.WAITED_FOR)."""
    from app.pipeline import lease
    from app.pipeline.constituent_rescore import _stale_chambers, rescore_stale_constituent_alignment
    from app.pipeline.fetch.district_pvi import RESCORE_WHO, current_lines

    if "house" not in _stale_chambers():
        return [], None
    with lease.job(lease.DISTRICT_LINES, who=RESCORE_WHO) as granted:
        if not granted:
            return [], granted
        with current_lines() as lines:
            return rescore_stale_constituent_alignment(session_factory, house_lines=lines, chambers=("house",)), None


def rescore_constituent_alignment_on_current_lines(session_factory) -> list[str]:
    """The startup Constituent Alignment rescore (constituent_rescore.py) on
    one read of the district table, recording that read's Congress on each
    rescored representative in the same commit as their score. The file can
    be rewritten meanwhile (another backend, mid-rollout); the Congress
    recorded must be the lines the score used, so the breakdown — and the
    overlap check the rescore re-measures from it — recompute on the same
    ones. The Senate goes first and reads no lines; then one attempt at the
    House (_rescore_house_on_current_lines — _startup_rescore retries it
    while a refresh holds the lines). Never raises (the rescore's own
    contract)."""
    from app.pipeline.constituent_rescore import rescore_stale_constituent_alignment

    done = rescore_stale_constituent_alignment(session_factory, house_lines=None, chambers=("senate",))
    try:
        return done + _rescore_house_on_current_lines(session_factory)[0]
    except Exception:
        logging.getLogger("app.main").exception("Startup rescore (house): taking the district lines failed")
        return done


def _run_startup_rescore(
    session_factory, *, deadline: float, poll_s: float, house_only: bool = False,
) -> "threading.Timer | None":
    """One pass of the startup rescore, in a writer thread (start_writer),
    under the STARTUP_RESCORE lease so an admin data reset in another
    process sees it and it sees the reset: Legislative Effectiveness and
    the Senate's Constituent Alignment (first pass only), then the House.

    A District PVI refresh holding the district lines is waited for, as a
    House run waits (up to REFRESH_WAIT_S from the first pass): nothing
    else retries the rescore, and giving up would leave the House on the
    old scale until the next House run. The wait happens between passes,
    holding nothing — no lease, no writer registration — so a data reset
    is never refused for it: the next pass is a new writer (start_writer
    refuses while a reset holds the database, and the rescore is dropped:
    the reset rebuilds the data). A House run holding the lines is not
    waited for; it scores the House itself. Returns the timer of the next
    pass, when one is scheduled."""
    import time

    from app.background import WritesHeld, start_writer
    from app.pipeline import lease
    from app.pipeline.fetch.district_pvi import BUSY_RETRY_S, waits_for
    from app.pipeline.les_rescore import rescore_stale_legislative_effectiveness

    log = logging.getLogger("app.main")
    refused = None
    db = session_factory()
    try:
        with lease.holding(db, lease.STARTUP_RESCORE, yield_to=lease.DATA_RESET) as token:
            if token is None:
                log.info("Startup rescore skipped: %s", lease.refusal(db, lease.STARTUP_RESCORE))
                return None
            # One Congress for the pass: start_writer runs it inside
            # app.config.scoring_congress.
            if not house_only:
                rescore_stale_legislative_effectiveness(session_factory)
                from app.pipeline.constituent_rescore import rescore_stale_constituent_alignment

                rescore_stale_constituent_alignment(session_factory, house_lines=None, chambers=("senate",))
            try:
                refused = _rescore_house_on_current_lines(session_factory)[1]
            except Exception:
                log.exception("Startup rescore (house): taking the district lines failed")
    except Exception:
        # Each rescore logs its own failures; this is the lease's.
        log.exception("Startup rescore failed")
    finally:
        db.close()
    if refused is None:
        return None
    busy = refused.code == lease.REFUSED_BUSY and refused.holder is None
    # A refresh (or another startup rescore) is waited for, and so is a
    # leftover lease release_orphaned_holds is still re-checking: a House
    # run killed by the deploy that started this process will not score the
    # House, so it is not left to.
    if not (busy or waits_for(refused.holder)):
        log.info(
            "Startup rescore (house) left to the %s holding the district lines: it scores the House itself",
            refused.holder or "run",
        )
        return None
    if time.monotonic() >= deadline:
        log.warning(
            "Startup rescore (house) gave up: the %s held the district lines past the wait — the House "
            "stays on the old scale until the next House run", refused.holder or "database lock",
        )
        return None

    def next_pass() -> None:
        try:
            start_writer(
                lambda: _run_startup_rescore(session_factory, deadline=deadline, poll_s=poll_s, house_only=True),
                name="startup-rescore",
            )
        except WritesHeld as held:
            log.info("Startup rescore (house) dropped: %s — the reset rebuilds the data", held)

    timer = threading.Timer(min(poll_s, BUSY_RETRY_S) if busy else poll_s, next_pass)
    timer.daemon = True
    timer.start()
    return timer


async def _watch_pipeline_service() -> None:
    """Alert when the pipeline service stops (ops_alerts.check_pipeline_service_alive),
    and keep this process's CURRENT_CONGRESS up with the Congress in office
    (app.config.advance_current_congress): nothing here runs pipeline jobs,
    whose start advances it where they run."""
    from app.config import advance_current_congress
    from app.ops_alerts import check_pipeline_service_alive

    # Every API worker runs this; the alert's dedupe (send_ops_alert) is
    # what makes a stale heartbeat page once, not once per worker.
    await asyncio.sleep(_LIVENESS_GRACE_S)
    while True:
        try:
            advance_current_congress()
        except Exception:
            logging.getLogger("app.main").warning("Advancing CURRENT_CONGRESS failed", exc_info=True)
        try:
            await asyncio.to_thread(check_pipeline_service_alive)
        except Exception:
            logging.getLogger("app.main").warning("Pipeline liveness check failed", exc_info=True)
        await asyncio.sleep(_LIVENESS_EVERY_S)


def _start_pipeline_side_startup_jobs() -> None:
    """The startup work that writes: run where pipelines run, never in the
    read-only API process."""
    # Rebuild the sqlite-vec explore index when missing or built by a
    # different model (the 2026-07 chroma->sqlite-vec migration path, and
    # any future index-model change); search reports "not ready" until it
    # completes. The check itself on a thread too: it reads the vector store,
    # and waits out a lock another writer holds rather than guess.
    def _check_explore_index() -> None:
        try:
            from app.database import SessionLocal
            from app.pipeline.vector_store import ensure_explore_index
            ensure_explore_index(SessionLocal)
        except Exception:
            logging.getLogger(__name__).exception("Explore index check failed (non-fatal)")

    threading.Thread(target=_check_explore_index, daemon=True, name="explore-index-check").start()

    # A release that rescales Legislative Effectiveness or Constituent
    # Alignment leaves the stored scores and reference on the old scale until
    # the nightly run; bring them over now so pages and their breakdowns agree
    # (les_rescore.py, constituent_rescore.py). One thread, one after the
    # other, so the two never contend for SQLite's write lock.
    import time

    from app.database import SessionLocal as _rescore_session
    from app.pipeline.fetch.district_pvi import REFRESH_POLL_S, REFRESH_WAIT_S

    deadline = time.monotonic() + REFRESH_WAIT_S
    start_writer(
        lambda: _run_startup_rescore(_rescore_session, deadline=deadline, poll_s=REFRESH_POLL_S),
        name="startup-rescore",
    )


# The pipeline side must be one process per container: the admin status
# endpoint reports run flags held in its memory (and check-and-deploy.sh's
# busy check reads them), the data reset's writer registry is per process,
# and each process would run its own scheduler. Rather than infer the worker
# count from how uvicorn was launched, each such process takes this lock and
# a second one refuses to start. /dev/shm is per container, so a rolling
# update's old and new tasks never contend for it.
def _role_lock_path() -> str:
    """Per container (RAM_DIR) and per database: two processes on different
    databases — a second local dev server — share no run state to protect.
    The database is its resolved file, not the URL: the default URL is a
    relative path, the same string for every checkout's own ./data."""
    import hashlib

    from sqlalchemy.engine import make_url

    from app.api.throttle import RAM_DIR

    try:
        database = make_url(settings.DATABASE_URL).database or ""
    except Exception:
        database = settings.DATABASE_URL
    if database and database != ":memory:":
        database = os.path.realpath(database)
    digest = hashlib.sha256(database.encode()).hexdigest()[:12]
    return os.path.join(RAM_DIR, f"civitas_pipeline_process-{digest}.lock")


_ROLE_LOCK_PATH = _role_lock_path()


def _take_pipeline_role_lock() -> int:
    """The lock's file descriptor, held for the process's life; raises when
    another process in this container already runs the pipeline side."""
    import fcntl

    fd = os.open(_ROLE_LOCK_PATH, os.O_RDWR | os.O_CREAT, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise RuntimeError(
            f"PROCESS_ROLE={settings.PROCESS_ROLE}: another process in this container already runs the "
            "pipeline side, which must be a single process — run one worker (WEB_CONCURRENCY=1); "
            "only PROCESS_ROLE=api scales out"
        ) from None
    return fd


PROCESS_STARTED_AT: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global PROCESS_STARTED_AT
    from datetime import datetime, timezone
    PROCESS_STARTED_AT = datetime.now(timezone.utc).isoformat()
    role = settings.PROCESS_ROLE
    serves_reads = role in ("all", "api")
    runs_pipelines = role in ("all", "worker")
    logging.getLogger("app.main").info("Backend process role: %s", role)
    # Before init_db: in a pipeline-side role it already starts background
    # work (the keyword index backfill), which a second such process must
    # not start before it refuses.
    role_lock = _take_pipeline_role_lock() if runs_pipelines else None
    init_db()
    # Read member scoring's district table (a district_pvi.SeatLines) before
    # any scheduler job or request does, in every role: the API reads the
    # lines for score breakdowns and elections as the pipeline does for
    # scoring. A file that can't be read is logged here, at start, and
    # every later read re-reads it once the pipeline rewrites it (its stamp
    # moves — score_calculator._district_pvi).
    try:
        from app.pipeline.fetch.district_pvi import lines_congress
        lines_congress()
    except Exception:
        logging.getLogger(__name__).exception("District PVI table load failed (non-fatal)")

    if runs_pipelines:
        # Only the process that runs pipelines may sweep their rows: the
        # API process sweeping on its own restart would mark a run live in
        # the pipeline process as dead.
        _invalidate_orphaned_pipelines()
        start_scheduler()
    if serves_reads:
        # Pre-build the bills-in-flight collection cache on a background
        # thread so the first /api/bills request after a deploy is a cache
        # hit instead of paying the ~1.5s cold rebuild (see bill_service.py).
        from app.services.bill_service import warm_bill_collection_cache
        warm_bill_collection_cache()
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _preload_models)

    bootstrap_task = None
    if runs_pipelines:
        # Held for the lifespan: the event loop keeps only a weak reference
        # to a task, so an unreferenced one can be garbage-collected
        # mid-ingestion.
        bootstrap_task = asyncio.create_task(_bootstrap_explore())
        _start_pipeline_side_startup_jobs()

    from app.api.throttle import run_maintenance
    from app.api.visits import run_visit_consumer
    visit_consumer_task = asyncio.create_task(run_visit_consumer())
    # Drops the rate-limit store's salts on time, traffic or not.
    throttle_task = asyncio.create_task(run_maintenance())
    # Only a separate API process can notice the pipeline process is gone:
    # with both in one process, a dead scheduler means a dead site.
    liveness_task = asyncio.create_task(_watch_pipeline_service()) if role == "api" else None
    # The admin dashboard is served by the pipeline process, which sees only
    # its own container's network counters: the API records its own.
    net_task = None
    if role == "api":
        from app.net_stats import run_recorder

        net_task = asyncio.create_task(run_recorder())

    yield

    visit_consumer_task.cancel()
    throttle_task.cancel()
    if liveness_task is not None:
        liveness_task.cancel()
    if net_task is not None:
        net_task.cancel()
        from app.net_stats import forget_own_record

        forget_own_record()
    if bootstrap_task is not None:
        bootstrap_task.cancel()
    from app.services import explore_summary

    await explore_summary.stop()
    stop_scheduler()
    if role_lock is not None:
        os.close(role_lock)


app = FastAPI(
    title="Civitas API",
    description="Backend API for the Civitas senator representation tracker",
    version="0.1.0",
    lifespan=lifespan,
)



@app.exception_handler(WritesHeld)
async def _writes_held(_request, held: WritesHeld) -> JSONResponse:
    """An endpoint's writer refused while the admin data reset holds the
    database (app.background.writing)."""
    return JSONResponse(status_code=409, content={"detail": str(held)})


@app.exception_handler(WritesElsewhere)
async def _writes_elsewhere(_request, refused: WritesElsewhere) -> JSONResponse:
    """A trigger reached the read-only API process: nginx routes every
    trigger to the pipeline service, so this one is missing from that list
    (nginx/civitas.conf, "Background work")."""
    return JSONResponse(status_code=503, content={"detail": str(refused)})


# Added before GZip, so it runs *inside* it and hashes the uncompressed
# body. Outside it, the ETag hashed gzip output, whose header carries the
# time it was written: the same body got a new ETag every second, so a
# conditional request almost never matched. A 304 has no body, so the
# compressor passes it through untouched.
app.add_middleware(ETagCacheMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=500)
_cors_origins = [
    o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()
] if settings.CORS_ORIGINS else [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(api_router)
