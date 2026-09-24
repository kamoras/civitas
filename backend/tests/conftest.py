"""Shared test fixtures."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

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
# Pinned to the 2026-07-23 production audit values: test scaffolding, not a
# proposal for production values.
TEST_LES_REFERENCE = {
    "senate": {
        "congress": 119, "majority": "R", "n": 101, "median_credit": 289.0,
        "mean_credit": 324.95, "stdev_credit": 178.37, "avg_baseline": 0.0305,
        "advancement_rates": {"majority": 0.036, "minority": 0.024, "pooled": 0.030},
    },
    "house": {
        "congress": 119, "majority": "R", "n": 427, "median_credit": 129.0,
        "mean_credit": 143.8, "stdev_credit": 88.12, "avg_baseline": 0.0444,
        "advancement_rates": {"majority": 0.064, "minority": 0.024, "pooled": 0.030},
    },
}


# Funding Independence's PAC-share reference, pinned the same way: the
# 2026-07 audit medians that were hand-typed as the multipliers 3.2 / 1.35.
TEST_FUNDING_REFERENCE = {
    "senate": {
        "n": 100, "pac_ratio_median": 0.157, "pac_dollars_median": 662750.0,
        "concentration_p10": 0.212, "concentration_median": 0.305, "concentration_p90": 0.383,
        "small_donor_p10": 8.0, "small_donor_median": 18.62, "small_donor_p90": 29.2,
    },
    "house": {
        "n": 435, "pac_ratio_median": 0.371, "pac_dollars_median": 662750.0,
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
# opposed one (-1); saturation at a 20-point deviation.
_TEST_EXPECTED = {"a": 0.10, "b": -0.05, "b_opposed": -0.15, "n": 50}
TEST_CONSTITUENT_REFERENCE = {
    chamber: {"expected": {"D": _TEST_EXPECTED, "R": _TEST_EXPECTED}, "deviation_p90": 0.20, "n": 100}
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
