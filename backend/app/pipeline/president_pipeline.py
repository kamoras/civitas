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
  - Public Mandate now covers every president who ever won a
    presidential election (presidential_approval.py for Truman-33
    onward, presidential_elections.py's historical margins before that).
  - Jobs data (BLS, 1939 onward) remains genuinely limited to its real
    window — not stale caps, real
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
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.http_client import make_async_client
from app.models import President, ScoreSnapshot
from app.pipeline.analyze.president_scorer import (
    PRESIDENT_ALGORITHM_VERSION,
    compute_president_overall_score,
    approval_by_group,
    compute_president_reference,
    congress_of_year,
    macro_reference,
    macro_window,
    peer_comparable,
    recalculate_president_scores,
    stored_approval_groups,
    stored_macro,
    FULL_TERM_DAYS,
    term_days,
    term_polarization,
    window_reference,
)
from app.pipeline.fetch.cspan_historians_survey import fetch_cspan_historians_survey
from app.pipeline.fetch.economic_data import fetch_jobs_for_president
from app.pipeline.fetch.historical_executive_orders import eo_entry, fetch_historical_eo_counts
from app.pipeline.fetch.historical_gdp import compute_term_gdp_growth, fetch_historical_real_gdp
from app.pipeline.fetch.peer_gdp import (
    bundled_last_year,
    bundled_per_capita,
    convergence_rate,
    fetch_world_bank_per_capita,
    peer_relative_growth,
)
from app.pipeline.fetch.presidential_approval import (
    approval_slugs,
    dated_approvals,
    dated_by_party,
    fetch_president_approval_history,
    recent_polls,
)
from app.pipeline.fetch.macro_series import CONSUMER_PRICES, UNEMPLOYMENT, fetch_annual_series, inflation_by_year
from app.pipeline.fetch.presidential_elections import fetch_election_margins
from app.pipeline.fetch.voteview import fetch_house_party_distance
from app.pipeline.fetch.presidential_roster import fetch_presidential_roster
from app.time_utils import utcnow

from app.pipeline.analyze.population_reference import PRESIDENT_REFERENCE

logger = logging.getLogger(__name__)

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

    Per-entry commit, not one commit after the whole loop (2026-07
    incident): `party` is NOT NULL on a brand-new row, and used to be
    silently left unset whenever the EO-table name match missed for
    that entry (`if party:` guarded the assignment with no fallback).
    A single miss — one EO-fetch hiccup, one unmatched name — raised
    IntegrityError on the shared session's *next* autoflush, which
    aborted the entire batch and rolled back every president already
    queued in this run, not just the one that failed. Confirmed live:
    this left the presidents table (and therefore /leaderboard) fully
    empty, the same failure class the score-computation loop below
    already isolates per-president for (#218 review S5) — this
    function was the one place still missing that isolation.
    """
    synced = 0
    for entry in roster:
        try:
            p = db.query(President).filter(President.id == entry.id).first()
            party = eo_entry(eo_data, entry.id, entry.name).get("party")
            is_current = entry.term_end is None
            if p is None:
                if not party:
                    # A brand-new row has no existing party to fall back
                    # on — inserting with party=None would violate the
                    # NOT NULL constraint. Skip until a later run's EO
                    # fetch resolves the name match rather than guess.
                    logger.warning(
                        "Skipping new president %s (%s): no party match from EO table",
                        entry.id, entry.name,
                    )
                    continue
                p = President(id=entry.id, party=party)
                db.add(p)
            elif party:
                p.party = party
            p.name = entry.name
            p.number = entry.number
            p.term_start = entry.term_start
            p.term_end = entry.term_end
            p.is_current = is_current
            db.commit()
            synced += 1
        except Exception:
            logger.exception("Failed to sync president %s (%s) — skipping", entry.id, entry.name)
            db.rollback()
    return synced


async def run_president_pipeline(db: Session) -> dict:
    """Fetch live/historical data and recalculate every dimension for
    every president.

    Returns summary dict with counts.
    """
    logger.info("Starting president pipeline...")

    async with make_async_client() as client:
        logger.info("Fetching executive-order counts (UCSB, all presidents)...")
        eo_data = await fetch_historical_eo_counts(db)
        logger.info("EO data fetched for %d presidents", len(eo_data))

        logger.info("Fetching presidential roster (UCSB)...")
        roster = await fetch_presidential_roster(db)
        synced = _sync_roster(db, roster, eo_data)
        logger.info("Roster synced for %d presidents", synced)

        presidents = db.query(President).all()
        if not presidents:
            logger.warning("No presidents in database and roster fetch found none — nothing to do")
            return {"updated": 0}

        logger.info("Fetching real GDP series 1790-present (MeasuringWorth)...")
        current_year = utcnow().year
        gdp_by_year = await fetch_historical_real_gdp(client, db, 1790, current_year)
        logger.info("GDP data fetched for %d years", len(gdp_by_year))

        logger.info("Fetching unemployment and consumer prices (FRED/BLS)...")
        unemployment_by_year = await fetch_annual_series(client, db, UNEMPLOYMENT)
        cpi_by_year = await fetch_annual_series(client, db, CONSUMER_PRICES)
        inflation_series = inflation_by_year(cpi_by_year) if cpi_by_year else None

        logger.info("Fetching real GDP per person, US and peer economies (World Bank)...")
        peer_bundled = bundled_per_capita()
        world_bank_gdp = await fetch_world_bank_per_capita(
            client, db, bundled_last_year(peer_bundled), current_year,
        )
        convergence = (
            convergence_rate(world_bank_gdp, peer_bundled, current_year) if world_bank_gdp is not None else None
        )
        logger.info("Peer catch-up growth: %s points per log income gap", convergence)

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
        # (poll date, {"D", "I", "R"} approve %) per presidency: Public
        # Mandate's by-party comparison within an era (president v9).
        party_series: dict[str, list] = {}
        approval_start_data: dict[str, float] = {}
        # (poll date, approve %) in date order, per presidency: the sitting
        # president's elapsed-time comparison reads predecessors' polls.
        approval_series: dict[str, list[tuple[date, float]]] = {}
        recent_avg_approval_data: dict[str, float] = {}
        slugs = await approval_slugs(db, presidents)
        for pid, slug in slugs.items():
            polls = await fetch_president_approval_history(db, pid, slug)
            if not polls:
                continue
            values = [poll.approving for poll in polls if poll.approving is not None]
            if values:
                approval_avg_data[pid] = sum(values) / len(values)
                # Last-quartile-minus-first-quartile average approval —
                # see calc_public_mandate's docstring for why this (not a
                # linear regression slope) and why it's compared against
                # the population's own average trend rather than zero.
                q = max(1, len(values) // 4)
                approval_start_data[pid] = sum(values[:q]) / q
                approval_trend_data[pid] = (sum(values[-q:]) / q) - approval_start_data[pid]
            approval_series[pid] = dated_approvals(polls)
            party_series[pid] = dated_by_party(polls)

            recent = recent_polls(polls)
            recent_values = [poll.approving for poll in recent if poll.approving is not None]
            if recent_values:
                recent_avg_approval_data[pid] = sum(recent_values) / len(recent_values)
        logger.info("Approval data fetched for %d presidents", len(approval_avg_data))

        # Polarization over every polling-era Congress (Truman's first, the
        # 79th, to the sitting one), for the by-party comparison.
        logger.info("Fetching House party polarization per Congress (Voteview)...")
        sitting = congress_of_year(current_year)
        polarization_by_congress = await fetch_house_party_distance(db, list(range(79, sitting + 1)), sitting)
        logger.info("Polarization read for %d Congresses", len(polarization_by_congress))

        logger.info("Fetching historical election-margin data (UCSB)...")
        election_margin_data = await fetch_election_margins(db)
        logger.info("Election-margin data fetched for %d presidents", len(election_margin_data))

        logger.info("Fetching C-SPAN Presidential Historians Survey (2021 cycle)...")
        historical_legacy_data = await fetch_cspan_historians_survey(client, db)
        logger.info("Historians-survey data fetched for %d presidents", len(historical_legacy_data))

    updated = 0
    failed = 0
    # Two passes: store every president's inputs first, then measure the
    # population statistics the z-scored dimensions compare against
    # (compute_president_reference), then score. Scoring inside the fetch
    # loop is what forced those statistics to be hand-typed constants.
    to_score: list[tuple] = []
    for president in presidents:
        try:
            term_years = _term_years(president.term_start, president.term_end)
            term_start_year = int(president.term_start[:4])
            term_end_year = int(president.term_end[:4]) if president.term_end else utcnow().year

            live: dict = {"term_start_year": term_start_year}

            # eo_count is informational only (2026-07) — Competence, the
            # dimension it used to feed, was removed entirely (see
            # PRESIDENT_SCORE_WEIGHTS's comment in config_definitions.py), so
            # this no longer goes into `live`, just the profile's raw stat.
            eo = eo_entry(eo_data, president.id, president.name) or None
            if eo is not None:
                president.eo_count = eo["total_orders"]

            # Every stored field below is only OVERWRITTEN when this run's
            # fetch actually returned something for this president; `live`
            # is then built from the (possibly just-updated, possibly
            # untouched) stored value. 2026-07 fix (#218 review B2): the
            # previous version built `live` straight from this run's fetch
            # results, so a single-night source outage (UCSB/Federal
            # Register/C-SPAN down, cache past its TTL) wrote None over a
            # real previously-computed score instead of just leaving it as
            # last night's value — "couldn't fetch this run" must mean "keep
            # what we had," never "score reads as inapplicable now."
            gdp_growth = compute_term_gdp_growth(gdp_by_year, term_start_year, term_end_year)
            if gdp_growth is not None:
                president.gdp_growth_avg = gdp_growth
            if president.gdp_growth_avg is not None:
                live["gdp_growth_avg"] = president.gdp_growth_avg

            # Kept as stored when the World Bank couldn't be fetched this run.
            peer = (
                peer_relative_growth(term_start_year, term_end_year, world_bank_gdp, peer_bundled, convergence)
                if convergence is not None and peer_comparable(term_start_year)
                else None
            )
            if peer is not None:
                president.gdp_growth_per_person = peer["us"]
                president.gdp_growth_peer_median = peer["peers"]
                president.gdp_growth_relative = peer["relative"]
            live["gdp_growth_per_person"] = president.gdp_growth_per_person
            live["gdp_growth_peer_median"] = president.gdp_growth_peer_median
            live["gdp_growth_relative"] = president.gdp_growth_relative

            # Unemployment and inflation over the credited years, for a term
            # from 1947 on; kept as stored when this run couldn't read them.
            if unemployment_by_year and inflation_series and peer_comparable(term_start_year):
                last = term_end_year if president.term_end else min(max(unemployment_by_year), max(inflation_series))
                window = macro_window(unemployment_by_year, inflation_series, term_start_year, last)
                if window:
                    president.unemployment_start = window["unemp_start"]
                    president.unemployment_change = window["unemp_change"]
                    president.inflation_start = window["infl_start"]
                    president.inflation_average = window["infl_avg"]
                    president.economy_years = window["years"]
            live["macro"] = stored_macro(president)

            if president.id in jobs_data:
                president.jobs_created_millions = jobs_data[president.id]
            if president.jobs_created_millions is not None:
                live["jobs_created_millions"] = president.jobs_created_millions

            if president.id in approval_avg_data:
                president.avg_approval = approval_avg_data[president.id]
                president.approval_trend = approval_trend_data.get(president.id)
                president.approval_start = approval_start_data.get(president.id)
            elif president.id not in slugs and president.id in election_margin_data:
                # Election margin is only ever the Public Mandate basis for
                # a president with no approval-polling source at all
                # (pre-Truman) — never a stand-in for a modern president
                # whose approval fetch merely failed this run, which would
                # silently flip that president's scoring basis night to
                # night (#218 review B2).
                president.election_margin = election_margin_data[president.id]

            # Approval by party and the term's polarization; each kept as
            # stored when this run couldn't read it.
            groups = approval_by_group(party_series.get(president.id) or [], president.party)
            if groups:
                president.approval_own_party = groups["own"]
                president.approval_other_party = groups["opp"]
                president.approval_independents = groups["ind"]
            polarization = term_polarization(
                term_start_year, term_end_year if president.term_end else current_year + 1, polarization_by_congress,
            )
            if polarization is not None:
                president.term_polarization = polarization

            if president.avg_approval is not None:
                live["avg_approval"] = president.avg_approval
                live["approval_trend"] = president.approval_trend
                live["approval_start"] = president.approval_start
                live["is_current"] = president.is_current
                live["approval_groups"] = stored_approval_groups(president)
                live["polarization"] = president.term_polarization
            elif president.election_margin is not None:
                live["election_margin"] = president.election_margin

            # Informational only — not part of any scored dimension. NULL
            # (not stale) once a president leaves office and the recent-90-
            # day window has no new polls to populate it.
            president.recent_avg_approval = recent_avg_approval_data.get(president.id)

            if president.id in historical_legacy_data:
                president.historical_legacy_score = historical_legacy_data[president.id]
            if president.historical_legacy_score is not None:
                live["historical_legacy_score"] = president.historical_legacy_score

            db.commit()
            to_score.append((president, live, term_years))
        except Exception:
            # Per-president isolation, same rationale as the scoring pass.
            logger.exception("President pipeline failed to update %s's inputs", president.id)
            db.rollback()
            failed += 1

    # Margins come from this run's fetch for EVERY elected presidency (the
    # population the 2026-07 constants were measured over) — the stored
    # election_margin column only holds the pre-polling-era ones that are
    # scored on it. A stat this run couldn't measure (a source down) keeps
    # its last persisted value rather than disappearing from the reference.
    scored_inputs = {p.id: (live, term_years) for p, live, term_years in to_score}
    measured = compute_president_reference([
        {
            "id": p.id, "name": p.name, "avg_approval": p.avg_approval,
            "approval_trend": p.approval_trend,
            "approval_start": p.approval_start,
            "approval_groups": stored_approval_groups(p),
            "macro": stored_macro(p),
            "polarization": p.term_polarization,
            "is_current": p.is_current,
            "election_margin": election_margin_data.get(p.id),
            "historical_legacy_score": p.historical_legacy_score,
            "gdp_growth_avg": p.gdp_growth_avg,
            "gdp_growth_per_person": p.gdp_growth_per_person,
            "gdp_growth_peer_median": p.gdp_growth_peer_median,
            "gdp_growth_relative": p.gdp_growth_relative,
            "term_start_year": int(p.term_start[:4]) if p.term_start else None,
            "jobs_created_millions": p.jobs_created_millions,
            "term_years": scored_inputs.get(p.id, ({}, 0.0))[1],
            "term_days": term_days(p.term_start, p.term_end),
        }
        for p in presidents
    ])
    # A presidency shorter than a full term (the sitting one, or one cut
    # short) is compared with every other completed presidency over its own
    # number of days.
    completed_series = {
        p.id: approval_series[p.id] for p in presidents
        if not p.is_current and approval_series.get(p.id)
    }
    measured["term_windows"] = {
        p.id: window
        for p in presidents
        if approval_series.get(p.id)
        and (p.is_current or (term_days(p.term_start, p.term_end) or FULL_TERM_DAYS) < FULL_TERM_DAYS)
        and (window := window_reference(
            approval_series[p.id], [s for pid, s in completed_series.items() if pid != p.id],
            [
                (party_series[q.id], q.party, q.term_polarization)
                for q in presidents
                if q.id != p.id and not q.is_current and party_series.get(q.id)
                and q.term_polarization is not None
            ],
        ))
    }
    # A presidency shorter than a full term is judged on unemployment and
    # inflation against other postwar presidencies over the same number of
    # credited years.
    if unemployment_by_year and inflation_series:
        others = [
            q for q in presidents
            if not q.is_current and q.term_end and peer_comparable(int(q.term_start[:4]))
        ]
        measured["macro_windows"] = {
            p.id: block
            for p in presidents
            if (macro := stored_macro(p))
            and (p.is_current or (term_days(p.term_start, p.term_end) or FULL_TERM_DAYS) < FULL_TERM_DAYS)
            and (block := macro_reference([
                macro_window(
                    unemployment_by_year, inflation_series, int(q.term_start[:4]), int(q.term_end[:4]),
                    years=macro["years"],
                )
                for q in others
                if q.id != p.id and int(q.term_end[:4]) - int(q.term_start[:4]) >= macro["years"]
            ]))
        }
    previous = PRESIDENT_REFERENCE.load().get("presidents") or {}
    reference = PRESIDENT_REFERENCE.with_live("presidents", {**previous, **measured}).get("presidents")
    logger.info("President reference: %s", reference)

    for president, live, term_years in to_score:
        try:
            new_scores = recalculate_president_scores(president.id, live, term_years, reference)
            president.score_public_mandate = new_scores["score_public_mandate"]
            president.score_effectiveness = new_scores["score_effectiveness"]
            # Agency Alignment was removed in president v7; the column is
            # dropped in a later release (expand, then contract).
            president.score_agency_alignment = None
            president.score_historical_legacy = new_scores["score_historical_legacy"]
            president.updated_at = utcnow()
            db.commit()
            updated += 1

            logger.info(
                "  %s: mandate=%s effectiveness=%s legacy=%s",
                president.id,
                new_scores["score_public_mandate"],
                new_scores["score_effectiveness"],
                new_scores["score_historical_legacy"],
            )
        except Exception:
            # Per-president isolation (matches senate_pipeline.py's
            # per-member pattern, #218 review S5): a single bad roster row
            # (e.g. an unparseable term_start) used to raise out of this
            # loop entirely, leaving every later president unrecalculated
            # for the night. Committing per-president above (rather than
            # once after the whole loop) means this rollback only reverts
            # the ONE president that failed, not everyone processed so far.
            logger.exception("President pipeline failed for %s — leaving existing scores unchanged", president.id)
            db.rollback()
            failed += 1

    logger.info("President pipeline complete: %d presidents updated, %d failed", updated, failed)

    _record_president_snapshots(db)

    return {
        "updated": updated,
        "failed": failed,
        "eo_data_count": len(eo_data),
        "gdp_years_count": len(gdp_by_year),
        "jobs_data_count": len(jobs_data),
        "approval_data_count": len(approval_avg_data),
        "election_margin_data_count": len(election_margin_data),
        "historical_legacy_data_count": len(historical_legacy_data),
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

    ScoreSnapshot's score_1..score_5 columns are NOT nullable (shared
    schema with senators/reps) — a President dimension that's None
    (genuinely inapplicable for that president, see president_scorer.py)
    is stored as 0.0 in the snapshot specifically, same as how compute_
    president_overall_score's own renormalization already treats it as
    absent from the weighted average. The authoritative "does this apply"
    answer always lives on the President row's own nullable column, never
    on the snapshot. score_5 = Historical Legacy (added 2026-07). score_3
    = Competence until it was removed entirely (2026-07, see
    PRESIDENT_SCORE_WEIGHTS's comment in config_definitions.py) — always
    0.0 from that point on, the slot is simply retired rather than
    reindexing every other dimension's historical snapshot data.
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
            existing.score_3 = 0.0
            existing.score_4 = 0.0
            existing.score_5 = p.score_historical_legacy or 0.0
        else:
            db.add(ScoreSnapshot(
                entity_type="president",
                entity_id=p.id,
                date=today,
                overall_score=overall,
                score_1=p.score_public_mandate or 0.0,
                score_2=p.score_effectiveness or 0.0,
                score_3=0.0,
                score_4=0.0,
                score_5=p.score_historical_legacy or 0.0,
                algorithm_version=PRESIDENT_ALGORITHM_VERSION,
            ))
            count += 1
    db.commit()
    logger.info("Recorded %d new president score snapshots (%d total presidents)", count, len(presidents))
