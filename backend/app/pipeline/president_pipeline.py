"""President data pipeline — fetches live/historical data and computes
every scored dimension for every president in one unified pass.

2026-07: rewritten from a two-cohort (DYNAMIC_PRESIDENTS/ECONOMICS_ONLY_
PRESIDENTS) design with a hand-set seed fallback for anyone outside those
cohorts. Both the seed fallback and the narrow cohorts are gone:
  - Effectiveness's GDP component now covers the full presidency
    (historical_gdp.py, MeasuringWorth's 1790-present real-GDP series)
    layered under BEA/FRED's live 1930/1947-onward series for the modern
    era — same "average annual growth over the term" figure regardless
    of which source computed it.
  - Competence's EO-activity-rate component now covers the full
    presidency too (historical_executive_orders.py, UCSB's own EO
    statistics table, actively updated through the current term) rather
    than being capped at Federal Register's 1994-onward machine-readable
    window.
  - Public Mandate now covers every president who ever won a
    presidential election (presidential_approval.py for Truman-33
    onward, presidential_elections.py's historical margins before that).
  - Jobs data (BLS, 1939 onward) and Agency Alignment (Federal Register
    rulemaking, 1994 onward — the regulatory record-keeping mechanism it
    measures didn't exist before Clinton's era in this platform's data)
    remain genuinely limited to their real windows — not stale caps, real
    data-availability walls. A dimension or component missing for a given
    president is never defaulted; see president_scorer.py's
    _blend_live_components and compute_president_overall_score.

Every president gets every dimension recalculated on every run (not just
a live-eligible subset) — the fetchers above make that correct now,
where it wasn't before this rewrite.

Identity data (name/party/term dates/number) is no longer hand-typed
either: `_sync_roster` creates/updates every President row from
presidential_roster.py's live UCSB fetch on every run, so a fresh/empty
database gets fully populated by this pipeline's first pass rather than
by a separate seed step (database.py's init_db creates zero president
rows now).
"""

import logging
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from app.models import President, ScoreSnapshot
from app.pipeline.analyze.president_scorer import (
    PRESIDENT_ALGORITHM_VERSION,
    compute_president_overall_score,
    recalculate_president_scores,
)
from app.pipeline.fetch.economic_data import fetch_jobs_for_president
from app.pipeline.fetch.federal_register import fetch_all_rulemaking_stats
from app.pipeline.fetch.historical_executive_orders import fetch_historical_eo_counts
from app.pipeline.fetch.historical_gdp import compute_term_gdp_growth, fetch_historical_real_gdp
from app.pipeline.fetch.presidential_approval import (
    PRESIDENT_APPROVAL_SLUGS,
    fetch_president_approval_history,
)
from app.pipeline.fetch.presidential_elections import fetch_election_margins
from app.pipeline.fetch.presidential_roster import fetch_presidential_roster
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

# Rulemaking data (Agency Alignment) has no historical-proxy source (see
# _agency_alignment_core's docstring on why this is a genuine conceptual
# absence, not an unfetched dataset) — still gated to the presidents
# Federal Register actually covers.
RULEMAKING_ELIGIBLE_PRESIDENTS = [
    "clinton-42", "gwbush-43", "obama-44", "trump-45", "biden-46", "trump-47",
]

# BLS payroll data starts 1939 — presidents whose full term falls after
# that get a real jobs-created figure; earlier presidents' Effectiveness
# is computed from GDP growth alone (see _effectiveness_core).
_BLS_COVERAGE_START_YEAR = 1939


def _term_years(start: str, end: str | None) -> float:
    """Calculate term length in years."""
    s = datetime.strptime(start, "%Y-%m-%d")
    if end:
        e = datetime.strptime(end, "%Y-%m-%d")
    else:
        e = utcnow()
    return max((e - s).days / 365.25, 0.1)


def _sync_roster(db: Session, roster, eo_data: dict) -> int:
    """Create/update each President row's identity fields (name, party,
    term dates, number, is_current) from the live UCSB roster fetch —
    replaces what used to be a hand-typed SEED_PRESIDENTS list (2026-07).
    Party comes from historical_executive_orders.py's EO-table fetch
    (already run this pipeline pass) rather than a second roster-page
    scrape, since that table already lists it per-president.

    Runs before the score-computation loop below so a fresh/empty DB
    gets its president rows populated in the same pass that first
    computes their scores — no separate seed step, no startup-time
    network fetch (see database.py's init_db, which now creates zero
    president rows and simply waits for this pipeline's first run).
    """
    synced = 0
    for entry in roster:
        p = db.query(President).filter(President.id == entry.id).first()
        party = eo_data.get(entry.id, {}).get("party")
        is_current = entry.term_end is None
        if p is None:
            p = President(id=entry.id)
            db.add(p)
        p.name = entry.name
        if party:
            p.party = party
        p.number = entry.number
        p.term_start = entry.term_start
        p.term_end = entry.term_end
        p.is_current = is_current
        synced += 1
    if synced:
        db.commit()
    return synced


async def run_president_pipeline(db: Session) -> dict:
    """Fetch live/historical data and recalculate every dimension for
    every president.

    Returns summary dict with counts.
    """
    logger.info("Starting president pipeline...")

    async with httpx.AsyncClient() as client:
        logger.info("Fetching executive-order counts (UCSB, all presidents)...")
        eo_data = await fetch_historical_eo_counts(client, db)
        logger.info("EO data fetched for %d presidents", len(eo_data))

        logger.info("Fetching presidential roster (UCSB)...")
        roster = await fetch_presidential_roster(client, db)
        synced = _sync_roster(db, roster, eo_data)
        logger.info("Roster synced for %d presidents", synced)

        presidents = db.query(President).all()
        if not presidents:
            logger.warning("No presidents in database and roster fetch found none — nothing to do")
            return {"updated": 0}

        logger.info("Fetching agency rulemaking data from Federal Register...")
        rulemaking_data = await fetch_all_rulemaking_stats(client)
        logger.info("Rulemaking data fetched for %d presidents", len(rulemaking_data))

        logger.info("Fetching real GDP series 1790-present (MeasuringWorth)...")
        current_year = utcnow().year
        gdp_by_year = await fetch_historical_real_gdp(client, db, 1790, current_year)
        logger.info("GDP data fetched for %d years", len(gdp_by_year))

        logger.info("Fetching BLS employment data (1939 onward)...")
        jobs_data: dict[str, float] = {}
        for p in presidents:
            term_start_year = int(p.term_start[:4])
            if term_start_year < _BLS_COVERAGE_START_YEAR:
                continue
            jobs = await fetch_jobs_for_president(client, p.id)
            if jobs is not None:
                jobs_data[p.id] = jobs

        logger.info("Fetching approval-poll history from UCSB American Presidency Project...")
        approval_avg_data: dict[str, float] = {}
        approval_trend_data: dict[str, float] = {}
        for pid in PRESIDENT_APPROVAL_SLUGS:
            polls = await fetch_president_approval_history(client, db, pid)
            if not polls:
                continue
            values = [poll.approving for poll in polls if poll.approving is not None]
            if not values:
                continue
            approval_avg_data[pid] = sum(values) / len(values)
            # Last-quartile-minus-first-quartile average approval — see
            # calc_public_mandate's docstring for why this (not a linear
            # regression slope) and why it's compared against the
            # population's own average trend rather than zero.
            q = max(1, len(values) // 4)
            approval_trend_data[pid] = (sum(values[-q:]) / q) - (sum(values[:q]) / q)
        logger.info("Approval data fetched for %d presidents", len(approval_avg_data))

        logger.info("Fetching historical election-margin data (UCSB)...")
        election_margin_data = await fetch_election_margins(client, db)
        logger.info("Election-margin data fetched for %d presidents", len(election_margin_data))

    updated = 0
    for president in presidents:
        term_years = _term_years(president.term_start, president.term_end)
        term_start_year = int(president.term_start[:4])
        term_end_year = int(president.term_end[:4]) if president.term_end else utcnow().year

        live: dict = {}

        eo = eo_data.get(president.id)
        if eo is not None:
            live["eo_count"] = eo["total_orders"]
            president.eo_count = eo["total_orders"]
        # eo_court_success_pct and cabinet_turnover_pct are never included
        # here: no fetch source anywhere (live or historical) populates
        # either one (see _competence_core's docstring on why —
        # CourtListener has no structured EO-to-litigation mapping;
        # Wikidata cabinet-tenure hasn't been built yet). Omitting them
        # lets calc_competence correctly compute Competence from
        # EO-activity-rate alone via _blend_live_components' renormalization.

        if president.id in rulemaking_data:
            live["rulemaking_count"] = rulemaking_data[president.id]["rulemaking_count"]
            live["rulemaking_finalized_pct"] = rulemaking_data[president.id]["rulemaking_finalized_pct"]
            president.rulemaking_count = live["rulemaking_count"]
            president.rulemaking_finalized_pct = live["rulemaking_finalized_pct"]

        gdp_growth = compute_term_gdp_growth(gdp_by_year, term_start_year, term_end_year)
        if gdp_growth is not None:
            live["gdp_growth_avg"] = gdp_growth
            president.gdp_growth_avg = gdp_growth

        if president.id in jobs_data:
            live["jobs_created_millions"] = jobs_data[president.id]
            president.jobs_created_millions = jobs_data[president.id]

        if president.id in approval_avg_data:
            live["avg_approval"] = approval_avg_data[president.id]
            live["approval_trend"] = approval_trend_data.get(president.id)
            president.avg_approval = live["avg_approval"]
            president.approval_trend = live["approval_trend"]
        elif president.id in election_margin_data:
            live["election_margin"] = election_margin_data[president.id]
            president.election_margin = live["election_margin"]

        new_scores = recalculate_president_scores(president.id, live, term_years, term_start_year)
        president.score_public_mandate = new_scores["score_public_mandate"]
        president.score_competence = new_scores["score_competence"]
        president.score_effectiveness = new_scores["score_effectiveness"]
        president.score_agency_alignment = new_scores["score_agency_alignment"]
        president.updated_at = utcnow()
        updated += 1

        logger.info(
            "  %s: mandate=%s competence=%s effectiveness=%s agency=%s",
            president.id,
            new_scores["score_public_mandate"],
            new_scores["score_competence"],
            new_scores["score_effectiveness"],
            new_scores["score_agency_alignment"],
        )

    db.commit()
    logger.info("President pipeline complete: %d presidents updated", updated)

    _record_president_snapshots(db)

    return {
        "updated": updated,
        "eo_data_count": len(eo_data),
        "rulemaking_data_count": len(rulemaking_data),
        "gdp_years_count": len(gdp_by_year),
        "jobs_data_count": len(jobs_data),
        "approval_data_count": len(approval_avg_data),
        "election_margin_data_count": len(election_margin_data),
    }


def _record_president_snapshots(db: Session) -> None:
    """Snapshot today's scores for every president so we can compute trends.

    ScoreSnapshot (models.py) is a generic table already shared by
    senators ("senator") and representatives ("representative") — this is
    the first writer for "president". Runs for every president, not just
    a live-eligible subset: even a historical president whose scores
    rarely change still gets a daily row, same as how senators/reps are
    snapshotted regardless of whether their score happened to move that
    day — the trend chart needs a continuous line, not gaps.

    Per-row upsert (not delete-then-insert): mirrors house_pipeline.py's
    _record_rep_snapshots rather than senate_pipeline.py's
    _record_score_snapshots, which briefly deletes the day's rows before
    reinserting — an upsert never leaves the table without today's data
    mid-write.

    ScoreSnapshot's score_1..score_4 columns are NOT nullable (shared
    schema with senators/reps, whose 5 dimensions are always all
    present) — a President dimension that's None (genuinely inapplicable
    for that president, see president_scorer.py) is stored as 0.0 in the
    snapshot specifically, same as how compute_president_overall_score's
    own renormalization already treats it as absent from the weighted
    average. The authoritative "does this apply" answer always lives on
    the President row's own nullable column, never on the snapshot.
    """
    today = utcnow().date().isoformat()
    presidents = db.query(President).all()
    count = 0
    for p in presidents:
        overall = compute_president_overall_score(p)
        existing = (
            db.query(ScoreSnapshot)
            .filter(
                ScoreSnapshot.entity_type == "president",
                ScoreSnapshot.entity_id == p.id,
                ScoreSnapshot.date == today,
            )
            .first()
        )
        if existing:
            existing.overall_score = overall
            existing.score_1 = p.score_public_mandate or 0.0
            existing.score_2 = p.score_effectiveness or 0.0
            existing.score_3 = p.score_competence or 0.0
            existing.score_4 = p.score_agency_alignment or 0.0
        else:
            db.add(ScoreSnapshot(
                entity_type="president",
                entity_id=p.id,
                date=today,
                overall_score=overall,
                score_1=p.score_public_mandate or 0.0,
                score_2=p.score_effectiveness or 0.0,
                score_3=p.score_competence or 0.0,
                score_4=p.score_agency_alignment or 0.0,
                algorithm_version=PRESIDENT_ALGORITHM_VERSION,
            ))
            count += 1
    db.commit()
    logger.info("Recorded %d new president score snapshots (%d total presidents)", count, len(presidents))
