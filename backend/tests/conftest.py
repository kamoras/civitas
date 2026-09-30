"""Shared test fixtures."""

import functools
import os
import threading

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
# The per-container RAM directory (api/throttle.RAM_DIR: the throttle store,
# the pipeline-process lock) — one per test run, so a local dev server's, or
# a parallel run's, never meets this one's.
os.environ.setdefault("CIVITAS_RAM_DIR", __import__("tempfile").mkdtemp(prefix="civitas-tests-"))
# The data volume (/data) is the running site's, where one is mounted — a
# test run must never write it (_data_volume_untouched below). The vector
# store's path is read once, at import: set before anything imports it.
os.environ.setdefault(
    "VECTOR_DB_PATH",
    os.path.join(__import__("tempfile").mkdtemp(prefix="civitas-tests-vectors-"), "vectors.db"),
)

import pathlib
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, VisitsBase


@pytest.fixture()
def db_session():
    """In-memory SQLite session for testing the learning store.

    StaticPool + check_same_thread=False: SQLAlchemy's default SQLite pool
    hands each thread its own connection, which for a `:memory:` database
    means each thread sees a separate, empty database. classify_donors_hybrid
    now runs its sync body via asyncio.to_thread (see donor_classifier_ai.py),
    so a session used across that boundary needs the single shared
    connection StaticPool provides — the same fix production doesn't need
    since a file-backed SQLite DB is the same database regardless of which
    thread opens the connection.

    Production splits SiteVisit/PageView onto their own database file
    (see database.py's VisitsBase) so track-visit's writes can't contend
    with the nightly pipeline's — but tests exercise both through this
    one session/engine either way, so both bases are created here rather
    than standing up a second in-memory engine tests don't need.
    """
    before = set(threading.enumerate())
    engine = create_engine(
        "sqlite:///:memory:", echo=False,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    VisitsBase.metadata.create_all(bind=engine)
    # autoflush=False to match app.database.SessionLocal. Not cosmetic:
    # under the default autoflush=True a query SEES rows added earlier in
    # the same transaction, so a read-then-add dedupe check passes in
    # tests and fails in production. That exact divergence let an
    # IntegrityError on uq_race_coverage_race_url reach production and
    # abort every election-coverage refresh (fixed in election_coverage
    # ._store_if_new). A fixture that is easier to satisfy than production
    # is not a test of production.
    Session = sessionmaker(bind=engine, autoflush=False)
    session = Session()
    yield session
    _join_app_threads_started_since(before)
    session.close()
    engine.dispose()


# How long teardown waits for one background thread the test left running.
_THREAD_JOIN_S = 10


def _join_app_threads_started_since(before: set) -> None:
    """Wait for the app's own background threads the test started (the
    bills-cache rebuild admin_reset_data kicks off, a start_writer job):
    one still querying the shared in-memory connection when the engine is
    disposed crashes SQLite outright (a segfault that killed a CI run).
    Only threads running app code (by their target, or by a Thread
    subclass the app defines): a library's long-lived monitor thread
    started along the way would never finish. One that outlives the join
    fails the test by name — a test that takes a lease must release it, or
    its heartbeat (lease._keep) is exactly such a thread. Executor workers
    (asyncio.to_thread, ThreadPoolExecutor) run no app target of their
    own and aren't joined: work sent there must be awaited in the test."""
    stuck = []
    for thread in set(threading.enumerate()) - before:
        target = getattr(thread, "_target", None)
        while isinstance(target, functools.partial):
            target = target.func
        if thread is threading.current_thread():
            continue
        modules = (getattr(target, "__module__", "") or "", type(thread).__module__)
        if any(m.startswith("app.") for m in modules):
            thread.join(_THREAD_JOIN_S)
            if thread.is_alive():
                stuck.append(thread.name)
    # Disposing under a live thread is the crash this exists to prevent:
    # say which thread, rather than carry on into it.
    assert not stuck, f"background threads still running at teardown: {stuck}"


@pytest.fixture()
def file_sessionmaker(tmp_path):
    """A file-backed SQLite database in tmp_path, configured as production's
    (app.database: busy timeout, WAL and the same pragmas), and a
    sessionmaker bound to it — for code that opens its own sessions from
    several threads (asyncio.to_thread, threading.Timer). Each session gets
    its own connection, as in production. db_session's single in-memory
    connection (StaticPool) handed to every thread is not that: two threads
    using one Session at once raise IllegalStateChangeError."""
    from sqlalchemy import event

    from app.database import SQLITE_BUSY_TIMEOUT_S, _set_sqlite_pragmas

    before = set(threading.enumerate())
    engine = create_engine(
        f"sqlite:///{tmp_path / 'civitas-test.db'}", echo=False,
        connect_args={"check_same_thread": False, "timeout": SQLITE_BUSY_TIMEOUT_S},
    )
    event.listens_for(engine, "connect")(_set_sqlite_pragmas)
    Base.metadata.create_all(bind=engine)
    VisitsBase.metadata.create_all(bind=engine)
    yield sessionmaker(bind=engine, autocommit=False, autoflush=False)
    _join_app_threads_started_since(before)
    engine.dispose()


@pytest.fixture(scope="session")
def _throttle_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("throttle")


@pytest.fixture(autouse=True)
def throttle_store(_throttle_dir):
    """A fresh store for api/throttle.py for every test, in a file of its
    own; yields its path. Autouse: the default lives in /dev/shm, where
    limits counted by one test (20 writes a minute) would refuse the next
    test's requests. Nothing is created until a test uses it."""
    import uuid

    from app.api import throttle

    previous = throttle._path
    path = str(_throttle_dir / f"{uuid.uuid4().hex}.db")
    throttle.use_path(path)
    yield path
    throttle.use_path(previous)


# Explore search's ranking parameters are generated data — measured against
# whatever corpus the pipeline last ingested (see
# app/pipeline/calibrate_ranking.py). Tests of the ranking *mechanism* must
# not silently inherit those, or their meaning changes every time someone
# recalibrates: three ranking tests did exactly that the first time the
# calibration became real, asserting behaviour that only held under the
# hand-written values they were written against.
#
# This is a fixed, explicit calibration chosen so every mechanism under test
# is observable — both priors active, title outweighing body, a diversity
# cap that fires, deduplication that can collapse something. It is test
# scaffolding, not a proposal for production values.
TEST_RANKING_CALIBRATION = {
    "field_weights": {"title": 8.0, "summary": 3.0, "body": 1.0},
    "prior_weights": {"freshness": 0.4, "authority": 0.3},
    "candidate_pool": {"default": 200, "max": 600},
    "source_diversity_cap": 3,
    "fingerprint": {"prefix_chars": 400, "min_chars": 80},
    "text_shape": {"snippet_tokens": 32, "min_term_length": 2},
}


@pytest.fixture()
def fixed_ranking():
    """Pin the explore ranking calibration for a test."""
    from app.pipeline import explore_ranking

    with explore_ranking.override(TEST_RANKING_CALIBRATION):
        yield TEST_RANKING_CALIBRATION


# Legislative Effectiveness scores each member against a population
# reference the pipeline measures every run (/data/les_reference.json, with
# a bundled fallback in app/data/). Tests must not inherit whichever of
# those happens to be on disk — a dev machine's /data, or the next
# regeneration of the bundled file, would silently change what they assert.
# Test scaffolding, not a proposal for production values: the v6.17
# five-stage, stage-normalized scale (a chamber averages 1.0), measured by
# compute_les_reference over the 118th Congress's per-member stage counts in
# Volden & Wiseman's published data (scripts/research_les_stage_weighting.py,
# members_from_counts), majority members labelled R and relabelled congress
# 119 to match the test bills. Advancement rates and average baselines are
# the 2026-07-23 production audit figures.
TEST_LES_REFERENCE = {
    "senate": {
        "congress": 119, "majority": "R", "n": 102, "median_credit": 0.7484,
        "mean_credit": 1.0, "stdev_credit": 1.2481, "avg_baseline": 0.0305,
        "advancement_rates": {"majority": 0.036, "minority": 0.024, "pooled": 0.030},
        "stage_totals": [28855.0, 3695.0, 3215.0, 1415.0, 455.0], "n_members": 102,
        "status_median": {"majority": 0.8114, "minority": 0.5562},
    },
    "house": {
        "congress": 119, "majority": "R", "n": 446, "median_credit": 0.7974,
        "mean_credit": 1.0045, "stdev_credit": 0.892, "avg_baseline": 0.0444,
        "advancement_rates": {"majority": 0.064, "minority": 0.024, "pooled": 0.030},
        "stage_totals": [51325.0, 7055.0, 6155.0, 3250.0, 870.0], "n_members": 448,
        "status_median": {"majority": 1.2324, "minority": 0.3715},
    },
}


# Funding Independence's PAC-share reference, pinned the same way: the
# 2026-07 audit medians that were hand-typed as the multipliers 3.2 / 1.35.
TEST_FUNDING_REFERENCE = {
    "senate": {
        "n": 100, "pac_ratio_median": 0.157,
        "concentration_p10": 0.212, "concentration_median": 0.305, "concentration_p90": 0.383,
        "small_donor_p10": 8.0, "small_donor_median": 18.62, "small_donor_p90": 29.2,
    },
    "house": {
        "n": 435, "pac_ratio_median": 0.371,
        "concentration_p10": 0.212, "concentration_median": 0.275, "concentration_p90": 0.383,
        "small_donor_p10": 8.0, "small_donor_median": 18.62, "small_donor_p90": 29.2,
    },
}


# Presidential z-score population stats, pinned to the 2026-07 values that
# were hand-typed in president_scorer.py.
TEST_PRESIDENT_REFERENCE = {
    "presidents": {
        "avg_approval": {"mean": 50.93, "stdev": 9.06, "n": 15},
        "approval_trend": {"mean": -13.72, "stdev": 14.65, "n": 15},
        "election_margin": {"mean": 8.39, "stdev": 7.51, "n": 42},
        "historical_legacy": {"mean": 549.14, "stdev": 157.61, "n": 44},
        # president v5 (research_president_scores.py fallback values; the
        # finalization rate has no fallback, so tests pin a round one).
        "gdp_growth_prewar": {"mean": 3.317, "stdev": 2.8748, "n": 25},
        "gdp_growth_postwar": {"mean": 2.8371, "stdev": 1.1273, "n": 9},
        "jobs_per_year": {"mean": 1.2351, "stdev": 0.978, "n": 12},
        "rulemaking_finalized_pct": {"mean": 60.0, "stdev": 10.0, "n": 6},
    },
}


# Constituent Alignment's seat expectation, pinned to round numbers so test
# arithmetic is readable: expected break rate 10% in a swing seat
# (alignment 0), 5% in a maximally safe one (+1), 30% in a maximally
# opposed one (-1), a linear fit (link unset, as the bundled prior); the
# scale 0.3 standard deviations per vote — 9 points at the swing seat's
# 10%, where the binomial spread is sqrt(0.1 * 0.9) = 0.3.
from app.pipeline.analyze.score_calculator import CONSTITUENT_REFERENCE_STATISTIC  # noqa: E402

_TEST_EXPECTED = {"a": 0.10, "b": -0.05, "b_opposed": -0.15, "n": 50}
TEST_CONSTITUENT_REFERENCE = {
    chamber: {"expected": {"D": _TEST_EXPECTED, "R": _TEST_EXPECTED}, "deviation_p90": 0.30, "n": 100,
              "statistic": CONSTITUENT_REFERENCE_STATISTIC}
    for chamber in ("senate", "house")
}


@pytest.fixture(autouse=True)
def pinned_population_references(tmp_path, monkeypatch):
    """Point every per-chamber reference at test-controlled files: no live
    /data file, and a bundled file holding the pinned values above."""
    import json

    from app.pipeline.analyze import population_reference

    for ref, values in (
        (population_reference.LES_REFERENCE, TEST_LES_REFERENCE),
        (population_reference.FUNDING_REFERENCE, TEST_FUNDING_REFERENCE),
        (population_reference.PRESIDENT_REFERENCE, TEST_PRESIDENT_REFERENCE),
        (population_reference.CONSTITUENT_REFERENCE, TEST_CONSTITUENT_REFERENCE),
    ):
        bundled = tmp_path / f"{ref.name}_bundled.json"
        bundled.write_text(json.dumps(values))
        monkeypatch.setattr(ref, "bundled_path", bundled)
        monkeypatch.setattr(ref, "live_path", tmp_path / f"{ref.name}_live.json")
        monkeypatch.setattr(ref, "_cache", None)
    # The last run's overlap reading (/data/signal_overlap.json), written by
    # every member pipeline and the startup rescore (record_signal_overlap):
    # its live file here too, over the real bundled one.
    from app.pipeline.analyze.signal_overlap import SIGNAL_OVERLAP

    monkeypatch.setattr(SIGNAL_OVERLAP, "live_path", tmp_path / "signal_overlap_live.json")
    monkeypatch.setattr(SIGNAL_OVERLAP, "_cache", None)
    yield


@pytest.fixture()
def pinned_les_reference(pinned_population_references):
    return TEST_LES_REFERENCE


@pytest.fixture()
def pinned_funding_reference(pinned_population_references):
    return TEST_FUNDING_REFERENCE


def start_senate_run_then_stop_beating(db, beat_ago=None):
    """A Senate run started the way the pipeline starts one — its lease
    taken, then its RUNNING row inserted and the lease tagged with the row's
    id in the same transaction — whose heartbeat then stopped `beat_ago`
    ago (None: still beating). Returns the run's row."""
    from app import models
    from app.pipeline import lease, senate_pipeline
    from app.time_utils import utcnow

    token = lease.acquire(db, lease.SENATE_RUN)
    assert token is not None
    run, refused = senate_pipeline._acquire_pipeline_lock(db, lease_token=token)
    assert refused is None, refused
    if beat_ago is not None:
        db.query(models.ApiCache).filter(models.ApiCache.tier == lease.SENATE_RUN).update(
            {"cached_at": utcnow() - beat_ago},
        )
        db.commit()
    return run


class BlueskyOutbox(list):
    """What would have been sent to Bluesky, as (text, url) pairs. Set
    `ok = False` to have Bluesky refuse every post."""
    ok = True


@pytest.fixture(autouse=True)
def bluesky_outbox(monkeypatch):
    """No test ever reaches Bluesky: app.broadcast is the one place that
    sends a post, and its sender is replaced here for every test. A test
    that sets BSKY_* credentials to exercise delivery reads what was sent
    from this list."""
    from app import broadcast

    outbox = BlueskyOutbox()

    def send(text, url, **_kw):
        if outbox.ok:
            outbox.append((text, url))
        return outbox.ok

    monkeypatch.setattr(broadcast, "publish_post", send)
    return outbox


@pytest.fixture(autouse=True)
def link_cards(monkeypatch):
    """No test reads a live page for a post's card (broadcast.capture_card).
    Pages answer from this dict by URL; any other URL reads as unreachable,
    so the card is left for the hourly pass, as for a page that is down."""
    from app import broadcast

    cards: dict[str, dict[str, str]] = {}
    monkeypatch.setattr(broadcast, "fetch_og_card", lambda url: cards.get(url))
    return cards


@pytest.fixture()
def bluesky_configured(monkeypatch, bluesky_outbox):
    """Bluesky credentials set, sends captured in `bluesky_outbox`."""
    from app.config import settings

    monkeypatch.setattr(settings, "BSKY_HANDLE", "civitas.test", raising=False)
    monkeypatch.setattr(settings, "BSKY_APP_PASSWORD", "pw", raising=False)
    return bluesky_outbox


@pytest.fixture(autouse=True)
def _no_running_pipeline_chains(monkeypatch):
    """Every test starts with no pipeline chain recorded as running
    (app.pipeline_chain keeps that in the process)."""
    from app import pipeline_chain

    monkeypatch.setattr(pipeline_chain, "_chains", {})


# --- The data volume -------------------------------------------------------
#
# Where /data exists (the backend container, a dev machine that mounts it),
# it is the site's: the live references, the vector store, the heartbeat the
# API process reads to decide the pipeline service is alive. Tests used to
# write it — the rescore tests replaced signal_overlap.json, the app-startup
# test the heartbeat and vectors.db, the election tests senate_classes.json.
# Every runtime path is pointed into the test's tmp_path below, and an audit
# hook refuses (PermissionError, as a read-only volume would) and records any
# write that still reaches /data, failing the test that made it.

_DATA_DIR = "/data"
_data_writes: list[str] = []
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_PATH_EVENTS = {"os.rename", "os.remove", "os.rmdir", "os.truncate", "os.utime", "os.link", "os.symlink",
                "os.chmod", "shutil.rmtree", "shutil.copyfile", "shutil.move"}


def _on_data_volume(path) -> str | None:
    if isinstance(path, int):
        return None
    try:
        resolved = os.path.abspath(os.fsdecode(path))
    except (TypeError, ValueError):
        return None
    return resolved if resolved == _DATA_DIR or resolved.startswith(_DATA_DIR + os.sep) else None


def _refuse_data_writes(event: str, args: tuple) -> None:
    hit = None
    if event == "open":
        path, mode, flags = args
        writes = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and flags & _WRITE_FLAGS)
        if writes:
            hit = _on_data_volume(path)
    elif event == "os.mkdir":
        hit = _on_data_volume(args[0])
        if hit and os.path.isdir(hit):
            hit = None  # makedirs(exist_ok=True) of a directory already there
    elif event in _PATH_EVENTS:
        hit = next((h for h in map(_on_data_volume, args[:2]) if h), None)
    elif event == "sqlite3.connect":
        hit = _on_data_volume(args[0]) if isinstance(args[0], (str, bytes, os.PathLike)) else None
    if hit:
        _data_writes.append(f"{event} {hit}")
        raise PermissionError(f"test run wrote the data volume: {event} {hit}")


sys.addaudithook(_refuse_data_writes)


def redirect_data_volume(monkeypatch, data) -> None:
    """Point the runtime data paths (what production keeps on /data) into
    the directory `data`. A fixture with a wider scope than a test — one
    that starts the real app's lifespan, whose scheduler writes its
    heartbeat — calls this with its own MonkeyPatch."""
    from app import election_calendar
    from app.pipeline.fetch import senate_classes

    data = pathlib.Path(data)
    data.mkdir(parents=True, exist_ok=True)

    def runtime_data_path(name: str) -> str:
        return str(data / name)

    monkeypatch.setattr("app.atomic_write.runtime_data_path", runtime_data_path)
    for module in ("app.pipeline.fetch.state_candidate_sources", "app.pipeline.fetch.state_election_dates"):
        if module in sys.modules:  # bound by name at their import
            monkeypatch.setattr(f"{module}.runtime_data_path", runtime_data_path)
    monkeypatch.setattr(senate_classes, "_PERSISTENT_PATH", str(data / "senate_classes.json"))
    monkeypatch.setattr(
        election_calendar, "_CLASS_FILES", (data / "senate_classes.json", *election_calendar._CLASS_FILES[1:]),
    )
    # Heavy to import (sentence-transformers): patched only once loaded — a
    # write before then is refused by the hook, and the store logs it.
    if "app.pipeline.vector_store" in sys.modules:
        monkeypatch.setattr("app.pipeline.vector_store._VERSION_FILE", str(data / "classification_model_version"))


@pytest.fixture(autouse=True)
def _data_volume_untouched(tmp_path, monkeypatch):
    """Point the runtime data paths into tmp_path, and fail a test that still
    wrote /data (the audit hook above refused it)."""
    redirect_data_volume(monkeypatch, tmp_path / "data-volume")
    before = len(_data_writes)
    yield
    wrote = _data_writes[before:]
    del _data_writes[before:]
    if wrote:
        pytest.fail("wrote the data volume: " + "; ".join(sorted(set(wrote))), pytrace=False)


def pytest_sessionfinish(session, exitstatus):
    """A write refused outside any test (a module fixture's background
    thread, between tests) still fails the run."""
    if _data_writes:
        print("\nThe test run tried to write the data volume outside a test: " + "; ".join(sorted(set(_data_writes))))
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
