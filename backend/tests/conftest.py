"""Shared test fixtures."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
# The per-container RAM directory (api/throttle.RAM_DIR: the throttle store,
# the pipeline-process lock) — one per test run, so a local dev server's, or
# a parallel run's, never meets this one's.
os.environ.setdefault("CIVITAS_RAM_DIR", __import__("tempfile").mkdtemp(prefix="civitas-tests-"))

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
    session.close()
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


@pytest.fixture()
def bluesky_configured(monkeypatch, bluesky_outbox):
    """Bluesky credentials set, sends captured in `bluesky_outbox`."""
    from app.config import settings

    monkeypatch.setattr(settings, "BSKY_HANDLE", "civitas.test", raising=False)
    monkeypatch.setattr(settings, "BSKY_APP_PASSWORD", "pw", raising=False)
    return bluesky_outbox
