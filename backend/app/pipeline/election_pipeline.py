"""Candidate roster + fundraising + coverage ingestion for federal
election cycles (2026-07, midterm-elections feature).

Independent pipeline: no data dependency on Senate/House/President's own
runs, same reasoning as supplementary_pipeline.py's own extraction from
senate_pipeline.py. Phases:

  1. Roster sync — every declared candidate for the cycle (bulk FEC fetch,
     not a per-race lookup — see fetch.fec.fetch_all_candidates) upserted
     into Race/Candidate rows, per-candidate fault isolation.
  2. Financial refresh — FEC's per-candidate totals endpoint is rate-
     limited to 1 request/4 sec and there are ~6,900 candidates in a
     midterm cycle, so this is prioritized (incumbents first, then active
     fundraisers, then everyone else) and watermarked (last_financials_
     sync), refreshing a bounded batch per run rather than blocking for
     hours on a single pass.
  3. Coverage ingestion — matches already-fetched RSS articles (Action
     Center's own news_feeds.py fetch, not re-fetched here) and new
     Bluesky search results to races by candidate-name string match.
  4. Bluesky posting — one grounded, source-backed sentence per notable
     coverage item, reusing bluesky_poster.py's existing LLM+grounding
     pattern.
  5. Snapshot — daily fundraising snapshot per candidate for trend charts,
     via the same shared ScoreSnapshot table senators/reps/presidents use.
"""

import hashlib
import json
import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app import ops_alerts
from app.config import settings
from app.database import SessionLocal
from app.election_calendar import (
    federal_states,
    seats_up_for_year,
)
from app.election_phase import active_election, election_is_held
from app.http_client import make_async_client
from app.live_results import bluesky as live_bluesky
from app.models import (
    BALLOT_ONLY_ID_PREFIX,
    BallotMeasure,
    Candidate,
    ElectionPipelineRun,
    MeasureCoverage,
    PipelineStatus,
    Race,
    RaceCoverageItem,
    ScoreSnapshot,
)
from app.pipeline.analyze import election_bluesky, election_coverage, race_relevance
from app.pipeline.analyze.score_calculator import get_district_pvi_map
from app.pipeline.fetch.fec import fetch_all_candidates, fetch_candidate_financials
from app.pipeline.fetch.state_candidates import (
    crawl_for_new_sources,
    sync_ballot_filings,
    sync_confirmed_candidates,
)
from app.pipeline.fetch.state_election_dates import senate_election_known
from app.pipeline.progress_tracker import ProgressTracker
from app.pipeline import lease
from app.pipeline.fetch import (
    ballot_lookup,
    ballot_measure_pdf_sources,
    ballot_measures_pdf,
    senate_classes,
)
from app.pipeline.fetch.ballot_measure_text import NotYetPublished
from app.pipeline.run_tracker import PipelineRunTracker, STALE_PIPELINE_TIMEOUT, acquire_tracked_run, skip_reason_text
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

def current_election_cycle(db: Session | None = None) -> int:
    """The election cycle the site is about: 2026 up through election
    night AND while its results are still coming in and on show, then
    2028 (election_phase.active_election). Computed from the calendar and
    the count, so a new cycle needs no code change. Keyed to the next
    election day alone, this flipped to 2028 the morning after — the
    pipeline would have started building a two-years-off roster while
    this one's ballots were still being counted."""
    return active_election(db).cycle

ELECTION_PIPELINE_STEPS = [
    ("roster_sync",          "roster",              "Sync candidate roster"),
    ("financial_refresh",    "financial",           "Refresh candidate financials"),
    ("confirmed_candidates", "confirmed_candidates", "Confirm general-election candidates"),
    ("ballot_measures",      "measures",             "Sync statewide ballot measures"),
    ("coverage_ingestion",   "coverage",             "Ingest race coverage"),
    ("bluesky_posting",      "posting",              "Post race coverage updates"),
    ("snapshot",             "snapshot",             "Snapshot candidate fundraising"),
]

# Candidates refreshed per run at FEC's 0.25 req/s rate limit — 500 candidates
# is ~33 minutes, a small slice of the cycle's total candidates, so the full
# set cycles through over multiple nightly runs rather than one multi-hour pass.
FINANCIALS_BATCH_SIZE = 500

# The states (election_calendar.federal_states: every state with Senate
# seats, read from the Senate's own list) are the only jurisdictions that
# hold federal Senate/House elections. FEC candidate files also include DC
# and territorial delegate filings (DC, PR, GU, VI, AS, MP); those are
# deliberately excluded from the roster: PR's Resident Commissioner isn't
# even elected in midterm years, and mixing non-voting delegate seats
# unlabeled into a "House races" directory misstates what's on the ballot.

# Coverage items older than this are pruned outright — the coverage feed is
# a live-coverage surface (race detail shows the latest 50), not an archive,
# and without pruning the table grows every 15 minutes all season (2026-07
# review: unbounded growth on a Pi's SQLite).
COVERAGE_RETENTION_DAYS = 90

_tracker = PipelineRunTracker()


def is_election_pipeline_running() -> bool:
    return _tracker.is_running


def election_pipeline_age():
    """Wall-clock age of the in-process election pipeline run, or None when idle."""
    return _tracker.age


# The election-season ballot sync (scheduler.py) runs the ballot step on its
# own between nightly runs; this is its in-process overlap guard, behind the
# BALLOT_SYNC lease (lease.tracked_job), shared by the sync and the nightly
# run's ballot step so each can see the other.
_ballot_tracker = PipelineRunTracker()


ELECTION_HELD = "election held; its ballot stands as read"


class _ElectionHeld(Exception):
    """Leaves a ballot phase's try block once it is marked skipped."""


def ballot_tracker() -> PipelineRunTracker:
    return _ballot_tracker


async def _sync_ballots(db: Session, client: httpx.AsyncClient, cycle: int) -> tuple[dict, dict]:
    """Who is on each state's ballot: its certified list or primary results
    (sync_confirmed_candidates), then its own filing list where it has one
    (sync_ballot_filings) — the only way to see a candidate who reaches
    November without running in a primary. Shared by the nightly run and
    the election-season ballot sync so the two can never drift apart."""
    confirm_result = await sync_confirmed_candidates(db, client, cycle)
    filing_result = await sync_ballot_filings(db, client, cycle)
    return confirm_result, filing_result


async def run_ballot_sync(cycle: int | None = None) -> dict:
    """The nightly run's ballot step, alone. Scheduled every few hours in
    election season (scheduler.py) so a withdrawal or replacement reaches
    the page the same day: the nightly run reaches this pipeline last,
    after Senate, House and stock trades, hours into the night. Reads only
    state election offices' published lists, a handful of requests each at
    one per second; the roster and financial refresh stay nightly."""
    db = SessionLocal()
    try:
        if election_is_held(active_election(db)):
            # The held election's ballot stands as read (election_is_held).
            return {
                "status": "skipped", "reason": ELECTION_HELD,
                "confirmed": 0, "statesOk": [], "statesFailed": [], "filings": {},
            }
        cycle = cycle if cycle is not None else current_election_cycle(db)
        async with make_async_client() as client:
            confirm_result, filing_result = await _sync_ballots(db, client, cycle)
    finally:
        db.close()
    return {
        "status": "ok",
        "confirmed": sum(r.get("confirmed", 0) for r in confirm_result.values()),
        "statesOk": sorted(s for s, r in confirm_result.items() if r.get("status") == "ok"),
        "statesFailed": sorted(s for s, r in confirm_result.items() if r.get("status") != "ok"),
        "filings": filing_result,
    }


def _race_id(cycle: int, office: str, state: str, district: int | None, is_special: bool = False) -> str:
    if office == "S":
        base = f"{cycle}-SEN-{state}"
        return f"{base}-SPECIAL" if is_special else base
    return f"{cycle}-HOUSE-{state}-{district if district is not None else 0}"


def _on_ballot_in(raw: dict, cycle: int) -> bool:
    """True only if this FEC candidate record confirms a `cycle` election.

    fetch_all_candidates already queries by election_year, but each record
    is re-validated here so a wrong upstream match can't mint a race for a
    state with no election that year (2026-07 review F1: the original
    `cycle=` query returned every candidate whose committee merely FILED
    in the period — sitting senators up in 2028/2030, early 2028
    declarers, prior-cycle committees winding down — which fabricated
    phantom Senate races in ~15 states). Same cycle-vs-election-year
    distinction financials_election_year (fec.py) documents for totals.
    """
    years = raw.get("election_years") or []
    return raw.get("candidate_election_year") == cycle or cycle in years


def _district_in(raw: dict, cycle: int) -> int | None:
    """The House district a candidate record names for `cycle`. FEC's
    `district_number` is the candidate's LATEST election's (H2TX35144:
    districts 35, 35, 37 for 2022, 2024, 2026 -- `district_number` 37), so
    a member who files for the next cycle in a new district would read as
    running there this cycle. `election_districts` pairs with
    `election_years`; the year's own entry is used when it is there."""
    years = raw.get("election_years") or []
    districts = raw.get("election_districts") or []
    if cycle in years and len(districts) == len(years):
        # Listed as zero-padded strings ("05", "00"); district_number is
        # the int the rest of the roster keys on.
        try:
            return int(districts[years.index(cycle)])
        except (TypeError, ValueError):
            pass
    return raw.get("district_number")


def _sync_roster(db: Session, cycle: int, candidates_raw: list[dict]) -> int:
    """Upsert Race + Candidate rows from raw FEC candidate records.

    Validation per record: must confirm an election in `cycle`
    (_on_ballot_in), must be in one of the 50 states
    (federal_states() — DC/territorial delegate filings excluded,
    see that constant's comment), and for House records, the district must
    exist in the real 435-seat apportionment (district_pvi.json's own
    "ST-N" keys, already the authoritative real-district map used
    elsewhere in scoring — see get_district_pvi_map). FEC's own district
    field carries paper-filer/placeholder noise straight through
    otherwise: 2026-08-26 audit found four phantom districts live
    (FL-59, GA-23, IL-51, NY-28 — none exist for those states) plus
    several states carrying a spurious null/0-district House row, each
    populated with garbage-looking filings (empty candidate name, party
    "UNK"). Senate records have no district to validate.

    Special elections: a Senate candidate on the `cycle` ballot in a state
    whose class seat is NOT up that year (election_calendar's rotation) can
    only be running in a special election, so the race is keyed
    "{cycle}-SEN-{ST}-SPECIAL" with is_special=True — e.g. 2026's FL and OH
    specials (both Class 3 seats vacated mid-term) get their own races
    instead of being conflated with a regular seat that doesn't exist. The
    one shape this can't distinguish is a special held in a state whose
    OTHER seat is also up regularly that year (GA 2020): FEC candidate
    records carry no per-seat field to split on, so both would key to the
    regular race — a genuine gap in the source data, not an unread field
    (none of 2026's known specials are in Class II states).

    Commits per candidate: a record that fails at flush time poisons the
    whole SQLAlchemy session for every later record if the failure
    surfaces mid-batch (the pre-fix per-record try/except caught the
    exception but left the session in PendingRollbackError, and the final
    batch commit then rolled back everything — the exact one-bad-row-
    blanks-the-table failure president_pipeline._sync_roster was fixed
    for). Per-record commit + rollback-on-error gives real isolation, same
    as _refresh_financials below.
    """
    synced = 0
    skipped_off_ballot = 0
    skipped_non_state = 0
    skipped_bad_district = 0
    skipped_no_senate_race = 0
    regular_senate_states = seats_up_for_year(cycle)
    states = federal_states()
    # Which seats exist, not their leans: the sitting lines' keys. Not the
    # cycle's table (district_pvi_for_congress), which leaves out a state
    # that redrew without a pinned table yet — every real filer there would
    # be dropped as a phantom district. The keys are the apportionment, the
    # same for every cycle up to the 2030 census; the 2032 cycle seats a new
    # one, and this check needs that cycle's apportionment then.
    real_districts = set(get_district_pvi_map())
    for raw in candidates_raw:
        try:
            candidate_id = raw.get("candidate_id")
            state = raw.get("state")
            office = raw.get("office")
            if not candidate_id or not state or office not in ("H", "S"):
                continue
            if state not in states:
                skipped_non_state += 1
                continue
            if not _on_ballot_in(raw, cycle):
                skipped_off_ballot += 1
                continue
            district = _district_in(raw, cycle) if office == "H" else None
            if office == "H" and f"{state}-{district}" not in real_districts:
                skipped_bad_district += 1
                continue
            # A Senate filer only has a race to be in if the FEC calendar
            # lists a Senate election here this cycle. Unknown calendar
            # (never read) falls back to the class rotation alone.
            if office == "S" and senate_election_known(state, cycle) is False:
                skipped_no_senate_race += 1
                continue
            is_special = office == "S" and state not in regular_senate_states
            race_id = _race_id(cycle, office, state, district, is_special)

            race = db.query(Race).filter(Race.id == race_id).first()
            if race is None:
                race = Race(
                    id=race_id, cycle_year=cycle, office=office,
                    state=state, district=district, is_special=is_special,
                )
                db.add(race)

            cand = db.query(Candidate).filter(Candidate.id == candidate_id).first()
            if cand is None:
                cand = Candidate(id=candidate_id, race_id=race_id)
                db.add(cand)
            cand.race_id = race_id
            cand.name = raw.get("name") or ""
            cand.party = raw.get("party") or "UNK"
            cand.incumbent_challenge = raw.get("incumbent_challenge")
            cand.has_raised_funds = bool(raw.get("has_raised_funds"))
            cand.candidate_status = raw.get("candidate_status")
            db.commit()
            synced += 1
        except Exception:
            db.rollback()
            logger.exception(
                "Failed to sync candidate %s — skipping", raw.get("candidate_id"),
            )
    removed = _remove_senate_races_nobody_holds(db, cycle)
    if skipped_no_senate_race or removed:
        logger.info(
            "Roster: skipped %d Senate filing(s) in states with no %d Senate election; "
            "removed %d such race(s) already on file",
            skipped_no_senate_race, cycle, removed,
        )
    if skipped_off_ballot or skipped_non_state or skipped_bad_district:
        logger.info(
            "Roster sync skipped %d records without a confirmed %d election, "
            "%d non-state (DC/territory) filings, and %d House records with "
            "a district outside the real 435-seat apportionment",
            skipped_off_ballot, cycle, skipped_non_state, skipped_bad_district,
        )
    return synced


def _remove_senate_races_nobody_holds(db: Session, cycle: int) -> int:
    """Delete Senate races the FEC calendar says are not being held — the
    "special elections" earlier rosters minted in NY and HI from serial
    filers. Only once the calendar has actually been read; its candidates
    and coverage go with it (ORM cascade)."""
    removed = 0
    for race in db.query(Race).filter(Race.cycle_year == cycle, Race.office == "S").all():
        if senate_election_known(race.state, cycle) is False:
            db.delete(race)
            removed += 1
    if removed:
        db.commit()
    return removed


def _prioritize_for_financial_refresh(db: Session, limit: int) -> list[Candidate]:
    """Never-synced candidates first, then oldest-synced first; within each
    group, incumbents before active fundraisers before everyone else.

    Candidates synced within the FEC cache TTL are excluded entirely
    (2026-07 review M3): fetch_candidate_financials serves from ApiCache
    inside that window, so re-selecting a fresh candidate consumes a batch
    slot to read back identical numbers. Without this floor, all ~470
    incumbents (priority 0) re-occupied the head of every nightly batch
    doing exactly that, leaving ~30 real slots for thousands of
    challengers — a ~2-month rotation. With it, freshly-synced candidates
    drop out of the pool and the batch is spent entirely on stale ones.
    """
    priority = case(
        (Candidate.incumbent_challenge == "I", 0),
        (Candidate.has_raised_funds.is_(True), 1),
        else_=2,
    )
    stale_before = utcnow() - timedelta(hours=settings.PIPELINE_CACHE_TTL_HOURS)
    return (
        db.query(Candidate)
        .filter(or_(
            Candidate.last_financials_sync.is_(None),
            Candidate.last_financials_sync < stale_before,
        ))
        # A ballot-only candidate has no FEC id to ask about.
        .filter(~Candidate.id.startswith(BALLOT_ONLY_ID_PREFIX))
        .order_by(
            Candidate.last_financials_sync.is_(None).desc(),
            priority,
            Candidate.last_financials_sync.asc(),
        )
        .limit(limit)
        .all()
    )


async def _refresh_financials(db: Session, client: httpx.AsyncClient, batch_size: int) -> int:
    candidates = _prioritize_for_financial_refresh(db, batch_size)
    refreshed = 0
    for cand in candidates:
        try:
            totals = await fetch_candidate_financials(client, db, cand.id)
            if totals:
                latest = totals[0]
                cand.contributions = latest.get("contributions")
                cand.disbursements = latest.get("disbursements")
                cand.cash_on_hand = latest.get("last_cash_on_hand_end_period")
                cand.individual_itemized_contributions = latest.get(
                    "individual_itemized_contributions",
                )
            cand.last_financials_sync = utcnow()
            refreshed += 1
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "Financial refresh failed for candidate %s — leaving existing values", cand.id,
            )
    return refreshed


def _snapshot_candidates(db: Session) -> int:
    """Fundraising snapshot per candidate, via the same shared
    ScoreSnapshot table senators/reps/presidents already use for trend
    charts. overall_score holds cash_on_hand — a fundraising figure, not
    an evaluative score; ScoreSnapshot's shape is reused as-is rather than
    adding a parallel table for one more "value over time per entity" case.

    Changed-only, not daily-unconditional (2026-07 review): thousands of
    candidates snapshotted every night regardless of change would add ~10x
    the platform's existing daily snapshot volume (millions of rows/year
    on the Pi's SQLite), while FEC totals for most candidates move only
    when a quarterly filing lands. A snapshot is written only when the
    candidate's figures differ from their latest existing snapshot —
    identical information for a trend chart (flat segments carry no data a
    start/end pair doesn't), at a small fraction of the rows.
    """
    today = utcnow().strftime("%Y-%m-%d")
    db.query(ScoreSnapshot).filter(
        ScoreSnapshot.entity_type == "candidate",
        ScoreSnapshot.date == today,
    ).delete()

    latest_date = (
        db.query(
            ScoreSnapshot.entity_id,
            func.max(ScoreSnapshot.date).label("latest"),
        )
        .filter(ScoreSnapshot.entity_type == "candidate")
        .group_by(ScoreSnapshot.entity_id)
        .subquery()
    )
    latest_rows = {
        row.entity_id: row
        for row in (
            db.query(ScoreSnapshot)
            .join(
                latest_date,
                (ScoreSnapshot.entity_id == latest_date.c.entity_id)
                & (ScoreSnapshot.date == latest_date.c.latest),
            )
            .filter(ScoreSnapshot.entity_type == "candidate")
            .all()
        )
    }

    written = 0
    candidates = db.query(Candidate).filter(Candidate.cash_on_hand.isnot(None)).all()
    for cand in candidates:
        prev = latest_rows.get(cand.id)
        values = (
            cand.cash_on_hand or 0.0,
            cand.contributions or 0.0,
            cand.disbursements or 0.0,
        )
        if prev is not None and (prev.overall_score, prev.score_1, prev.score_2) == values:
            continue
        db.add(ScoreSnapshot(
            entity_type="candidate",
            entity_id=cand.id,
            date=today,
            overall_score=values[0],
            score_1=values[1],
            score_2=values[2],
        ))
        written += 1
    db.commit()
    return written


# A measure that stops appearing in the upstream feed is marked `removed`
# and RENDERED as removed for this long before the row is deleted. Two
# reasons it is a grace period rather than an immediate delete: a voter
# who saw a measure last week needs to be told it was struck (a bare
# absence cannot say that), and one truncated upstream response must not
# silently blank a state's ballot. Same shape as member_lifecycle.py's
# roster-reconciliation grace window.
MEASURE_REMOVAL_GRACE_DAYS = 45

# If a sync returns fewer than this fraction of the measures we already
# had for a state, treat it as a bad response and keep the previous data
# instead of reconciling. Certification churn is real (four certified and
# seven removed across two states in a single July 2026 fortnight), but it
# does not look like "12 measures became 1".
MEASURE_SHRINK_FLOOR = 0.5

# ... but a shorter list that comes back unchanged this many runs in a row
# (the pipeline runs nightly, so three nights) is the state's real answer,
# not a bad response, and is written (_shrink_held_back). A truncated
# response is a one-off; a measure struck by a court is struck every night
# after. Without this, a floor that could never be passed would keep the
# struck measures on the page as current until election day.
MEASURE_SHRINK_CONFIRM_RUNS = 3

# How many days before the election a document a state publishes for
# EVERY general election (a voter guide, a sample ballot) must exist by.
# Until then its absence is "not yet published"; after, it is a failure
# that alerts (a moved file, a renamed link) rather than a quiet wait until
# November. A registry entry's own "expected_by_days_before" overrides this
# where a statute sets the date; this default is the fallback for the rest
# — two weeks, after every state's early voting has begun. Documents that
# exist only in a year with a measure (NotYetPublished(deadline_applies=
# False)) never hit it: their absence can be the real answer.
DEFAULT_EXPECTED_BY_DAYS_BEFORE = 14


def _iso_election_date(value) -> str | None:
    """An election date as the ISO "YYYY-MM-DD" every measure query keys on
    (the state page's election filter, reconciliation, pruning compare
    these as strings), or None when it can't be read as a date. Accepts ISO
    (optionally with a time after it) and US "MM/DD/YYYY" — every reader
    writes ISO today, and anything else is refused rather than stored under
    a date string no query would match."""

    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for fmt, part in (("%Y-%m-%d", text[:10]), ("%m/%d/%Y", text.split(" ")[0])):
        try:
            return datetime.strptime(part, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _upsert_measure(db: Session, raw: dict, detail: dict | None, source_name: str) -> None:
    """Insert or update one measure. Every text field is verbatim source."""

    detail = detail or {}
    given = detail.get("election_date") or raw.get("election_date")
    election_date = _iso_election_date(given)
    if not election_date:
        # No readable election date means we cannot say WHICH ballot this
        # is on, and a measure rendered under the wrong election is worse
        # than one not rendered at all (Ohio can run an "Issue 1" in both
        # a May primary and a November general).
        logger.warning("Skipping measure %s — no readable election date (%r)", raw.get("id"), given)
        return

    measure = db.query(BallotMeasure).filter(BallotMeasure.id == raw["id"]).first()
    if measure is None:
        measure = BallotMeasure(id=raw["id"])
        db.add(measure)

    measure.state = raw["state"]
    measure.election_date = election_date
    measure.number = raw.get("number") or ""
    measure.title = raw.get("title") or ""
    measure.official_title = detail.get("official_title")
    measure.official_summary = detail.get("official_summary")
    measure.fiscal_impact = detail.get("fiscal_impact")
    measure.yes_means = detail.get("yes_means")
    measure.no_means = detail.get("no_means")
    measure.measure_type = detail.get("measure_type")
    measure.origin = detail.get("origin")
    # Who drafted the title and fiscal statement. Every direct-from-state
    # strategy has always produced these, and the API and card render
    # them ("Drafted by ..."), but nothing wrote them to the row, so the
    # drafter never reached a reader.
    measure.title_authority = detail.get("title_authority")
    measure.fiscal_authority = detail.get("fiscal_authority")
    measure.source_url = detail.get("source_url")
    measure.republished_by = detail.get("republished_by")
    measure.source_name = source_name
    # A measure that had been marked removed and is now back in the feed
    # is certified again — the reconciliation below is the only writer of
    # `removed`, so re-appearing must clear it.
    measure.status = "certified"
    measure.last_seen_at = utcnow()
    measure.as_of = utcnow()


def _reconcile_state_measures(
    db: Session, state: str, election_dates: set[str], seen_ids: set[str],
) -> int:
    """Mark measures we no longer see as removed; delete long-gone ones."""

    stale = (
        db.query(BallotMeasure)
        .filter(
            BallotMeasure.state == state,
            BallotMeasure.election_date.in_(election_dates),
            BallotMeasure.id.notin_(seen_ids) if seen_ids else True,
        )
        .all()
    )
    marked = 0
    cutoff = utcnow() - timedelta(days=MEASURE_REMOVAL_GRACE_DAYS)
    for measure in stale:
        if measure.last_seen_at < cutoff:
            db.delete(measure)
            continue
        if measure.status != "removed":
            measure.status = "removed"
            measure.as_of = utcnow()
            marked += 1
    return marked


def _set_coverage(
    db: Session, state: str, election_date: str, status: str,
    count: int = 0, source_name: str | None = None, error: str | None = None,
) -> None:

    row = (
        db.query(MeasureCoverage)
        .filter(
            MeasureCoverage.state == state,
            MeasureCoverage.election_date == election_date,
        )
        .first()
    )
    if row is None:
        row = MeasureCoverage(state=state, election_date=election_date)
        db.add(row)
        # SessionLocal sets autoflush=False, so without this the query
        # above cannot see a row added earlier in the same run: a second
        # _set_coverage for the same state+date would add a SECOND row
        # and fail uq_measure_coverage_state_date at commit, taking the
        # whole election pipeline down with it. Seven call sites reach
        # this function, several of them on paths that can both run for
        # one state. Flushing makes the insert visible to the next
        # lookup without committing it.
        db.flush()
    now = utcnow()
    row.status = status
    row.measure_count = count
    row.source_name = source_name
    row.error_detail = error
    row.checked_at = now
    # Only a read that established an answer (covered, or confirmed none)
    # is a successful check. A failure, or a document not published, never
    # refreshes the date the page reports as "last read successfully" —
    # measures still on file after tonight's read are from that last
    # successful one, and the page says so.
    if status in (MeasureCoverage.COVERED, MeasureCoverage.CONFIRMED_NONE):
        row.last_success_at = now


def _coverage_row(db: Session, state: str, election_date: str):

    return (
        db.query(MeasureCoverage)
        .filter(MeasureCoverage.state == state, MeasureCoverage.election_date == election_date)
        .first()
    )


def _supersede_rows(db: Session, state: str, election_date: str, source_name: str, items: list[dict]) -> int:
    """Delete this election's rows that the read about to be written
    replaces. Returns rows deleted. Two kinds, both for `election_date`
    only — never another election (a primary's measures in the same year
    stay, which a same-year match once deleted):

    - rows from any OTHER source name (a state whose registry entry was
      renamed or re-pointed): superseded, not struck from the ballot —
      reconciling them would show "no longer on the ballot" beside the
      same measure's new card.
    - rows from this source whose non-empty number an incoming item
      carries under a different id (a re-keyed record). BallotMeasure is
      unique on (state, election_date, number), so leaving either kind in
      place makes the incoming insert fail.

    Called inside the same transaction as the upserts that replace them
    (_write_direct_answer): the delete and the inserts commit together or
    roll back together, so a failed insert can never leave the state with
    neither its old rows nor its new ones.
    """

    ids = {i["id"] for i in items}
    numbers = {i["number"] for i in items if i.get("number")}
    rows = (
        db.query(BallotMeasure)
        .filter(BallotMeasure.state == state, BallotMeasure.election_date == election_date)
        .all()
    )
    deleted = 0
    for row in rows:
        other_source = row.source_name != source_name
        rekeyed = row.number and row.number in numbers and row.id not in ids
        if other_source or rekeyed:
            db.delete(row)
            deleted += 1
    if deleted:
        db.flush()
    return deleted


def _live_measure_count(db: Session, state: str, source_name: str, election_dates: set[str]) -> int:
    """Measures currently shown as on the ballot for `state` from
    `source_name`, for these elections only — the baseline
    MEASURE_SHRINK_FLOOR compares against. Scoped to the elections the
    read covers: an earlier cycle's rows would inflate it and hold a real,
    shorter list back."""

    return (
        db.query(BallotMeasure)
        .filter(
            BallotMeasure.state == state,
            BallotMeasure.source_name == source_name,
            BallotMeasure.status != "removed",
            BallotMeasure.election_date.in_(election_dates),
        )
        .count()
    )


def _shrink_held_back(
    db: Session, state: str, election_day: str, listed_ids: list[str], listed: int, explained: int,
    existing: int, *, fresh: bool = True,
) -> bool:
    """Whether a read that returned fewer measures than are on file must
    be held back (True) rather than written.

    Not a shrink below MEASURE_SHRINK_FLOOR (after counting the drops the
    state itself reports, e.g. Florida's Status "Removed"): write it. A
    shrink the source explains in full: write it. Otherwise hold it back —
    until the SAME shorter list has come back on MEASURE_SHRINK_CONFIRM_RUNS
    consecutive runs, when it is the state's real answer and is written.
    A floor that could never be passed would keep struck measures on the
    page as current forever.

    Only a FRESH read counts toward that streak (`fresh`): a list served
    from the cache is the same one-off response again, and three nights of
    it is one truncated read, not three. The caller also drops a held-back
    list's cache entry, so the next night asks the state again.
    """

    row = _coverage_row(db, state, election_day)
    if not existing or listed + explained >= existing * MEASURE_SHRINK_FLOOR:
        if row is not None:
            row.pending_shrink, row.shrink_streak = None, 0
        return False
    fingerprint = json.dumps(sorted(listed_ids))
    if row is None:
        # Just somewhere to keep the streak: no status claim, and no
        # last_success_at — nothing has been read successfully.

        row = MeasureCoverage(state=state, election_date=election_day)
        db.add(row)
        db.flush()
    if not fresh:
        return True
    if row.pending_shrink == fingerprint:
        row.shrink_streak = (row.shrink_streak or 0) + 1
    else:
        row.pending_shrink, row.shrink_streak = fingerprint, 1
    if row.shrink_streak >= MEASURE_SHRINK_CONFIRM_RUNS:
        logger.warning(
            "Measure list for %s has shrunk to %d (from %d) on %d consecutive runs — accepting it",
            state, listed, existing, row.shrink_streak,
        )
        row.pending_shrink, row.shrink_streak = None, 0
        return False
    return True


def _live_ids(db: Session, state: str, source_name: str, election_dates: set[str]) -> set[str]:
    """Ids of the measures shown as on the ballot for these elections from
    `source_name`."""

    return {
        mid for (mid,) in db.query(BallotMeasure.id).filter(
            BallotMeasure.state == state,
            BallotMeasure.source_name == source_name,
            BallotMeasure.status != "removed",
            BallotMeasure.election_date.in_(election_dates),
        )
    }


def _struck_ids(state: str, election_day: str, removed: list[dict]) -> set[str]:

    return {ballot_measures_pdf._to_measure(state, parsed, election_day, "")["id"] for parsed in removed or []}


def _mark_removed(db: Session, state: str, election_day: str, source_name: str, ids: set[str]) -> int:
    """Mark these of `source_name`'s rows for the election removed (they
    render as removed through the grace window). Returns rows marked."""

    marked = 0
    for row in db.query(BallotMeasure).filter(
        BallotMeasure.state == state,
        BallotMeasure.election_date == election_day,
        BallotMeasure.source_name == source_name,
        BallotMeasure.id.in_(ids),
        BallotMeasure.status != "removed",
    ):
        row.status = "removed"
        row.as_of = utcnow()
        marked += 1
    return marked


class AbsenceRefused(Exception):
    """accept_state_absence declined without force (see its docstring)."""


def accept_state_absence(
    db: Session, state: str, election_day: str, source_name: str, note: str, *, force: bool = False,
) -> int:
    """The operator's path for a measure gone from a source that can only
    report its document missing (Michigan's November document disappears
    when its only proposal is struck): mark EVERY row for the state and
    election removed — any source's, so no row can go on rendering as
    current under an operator's "none" — and
    record confirmed none with the operator's note. The nightly sync
    restores that answer whenever the reader reports the document absent,
    and a real answer from the reader replaces it. Returns rows marked.

    Refused (AbsenceRefused) unless `force` while the latest read of this
    source covered the state for this election — that is a freshly read,
    certified list, and removing it is almost certainly the wrong state or
    election. Every action is logged at WARNING and appended to
    operator_actions, which nothing clears.
    """


    prior = _coverage_row(db, state, election_day)
    if (
        not force and prior is not None and prior.status == MeasureCoverage.COVERED
        and prior.source_name == source_name
    ):
        raise AbsenceRefused(
            f"{source_name}'s latest read covered {state} for {election_day}; pass force=true to override"
        )
    logger.warning(
        "Operator accepted the absence of %s's measures for %s (force=%s): %s",
        state, election_day, force, note,
    )
    marked = 0
    for row in db.query(BallotMeasure).filter(
        BallotMeasure.state == state,
        BallotMeasure.election_date == election_day,
        BallotMeasure.status != "removed",
    ):
        row.status = "removed"
        row.as_of = utcnow()
        marked += 1
    _set_coverage(
        db, state, election_day, MeasureCoverage.CONFIRMED_NONE,
        count=0, source_name=source_name, error=f"operator accepted absence: {note}",
    )
    row = _coverage_row(db, state, election_day)
    row.operator_note = note
    row.pending_shrink, row.shrink_streak = None, 0
    actions = json.loads(row.operator_actions) if row.operator_actions else []
    actions.append({"at": utcnow().isoformat(), "note": note, "force": force, "marked": marked})
    row.operator_actions = json.dumps(actions)
    db.commit()
    return marked


def _has_read_this_election(db: Session, prior, state: str, election_day: str, source_name: str) -> bool:
    """Whether THIS source has already given a real answer for this
    election — the durable facts, not last night's status: measures of its
    on file for the election, or a successful check it recorded
    (last_success_at is set only by covered / confirmed none). A document
    that was read and now reads as not published is a failure every night
    until it is back, not just the first. Coverage another source left
    (a renamed registry entry) says nothing about this one."""
    if _live_ids(db, state, source_name, {election_day}):
        return True
    return prior is not None and prior.source_name == source_name and prior.last_success_at is not None


def _late_cycle_notice(state: str, election_day: str, source_name: str, awaited) -> None:
    """A low-severity notice, once per state and election, that a document
    which exists only in a year with a measure still isn't published at
    the default expected-by date. Not a coverage change — "not yet
    covered" is still the honest status, and may be the real answer — but
    a reader that broke before it ever read anything this cycle would
    otherwise sit there silently until election day."""
    try:
        ops_alerts.send_ops_alert(
            f"[notice] {state} ballot measures still not published",
            f"{source_name}: {awaited} — still not published {DEFAULT_EXPECTED_BY_DAYS_BEFORE} days "
            f"before {election_day}. That can be the real answer (the document exists only in a year "
            f"with a measure); worth a manual look at the source in case the reader has broken.",
            dedupe_key=f"ballot-measure-late-{state}-{election_day}",
            condition=f"ballot-measure-late-{state}-{election_day}",
        )
    except Exception:
        logger.exception("Could not send late-cycle ballot-measure notice for %s", state)


def _past_expected_by(source: dict, election_day: str) -> bool:
    """Whether today is on or after the date this state's awaited document
    should exist by (see DEFAULT_EXPECTED_BY_DAYS_BEFORE and each registry
    entry's expected_by_days_before)."""

    days = source.get("expected_by_days_before", DEFAULT_EXPECTED_BY_DAYS_BEFORE)
    due = date.fromisoformat(election_day) - timedelta(days=days)
    return utcnow().date() >= due


class _ReaderReasons(logging.Handler):
    """What a state's reader said about why it failed, in its own log lines.

    A reader returns None for "could not read it" (36 of them, each for
    its own reasons, each logging why), and the coverage row used to say
    only "fetch failed" — the 403, the unrecognized page, the PDF that
    wasn't found were in the log and nowhere an operator looks. This
    keeps the reader's WARNING-and-above lines from app.pipeline.fetch
    while one state is being read, on this thread only: the hourly jobs
    log under the same loggers from threads of their own.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.thread = threading.get_ident()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread == self.thread:
            self.messages.append(record.getMessage().splitlines()[0][:300])


@contextmanager
def _reader_reasons() -> Iterator[list[str]]:
    handler = _ReaderReasons()
    fetch_logger = logging.getLogger("app.pipeline.fetch")
    fetch_logger.addHandler(handler)
    try:
        yield handler.messages
    finally:
        fetch_logger.removeHandler(handler)


async def _fetch_with_reasons(
    client: httpx.AsyncClient, db: Session, state: str, year: int, election_day: str,
) -> tuple[list[dict] | None, list[str]]:
    """fetch_state_measures_pdf, and the reasons its reader gave for a
    failure (_ReaderReasons). NotYetPublished propagates: that is an
    answer, not a failure."""
    with _reader_reasons() as reasons:
        try:
            return await ballot_measures_pdf.fetch_state_measures_pdf(client, db, state, year, election_day), reasons
        except NotYetPublished:
            raise
        except Exception as exc:
            logger.exception("PDF measure fetch raised for %s", state)
            return None, [*reasons, f"{type(exc).__name__}: {exc}"]


async def _sync_pdf_measures(
    db: Session, client: httpx.AsyncClient, election_day: str, failing: list[str] | None = None,
) -> tuple[int, int, int]:
    """Every state with a registered direct source
    (ballot_measure_pdf_sources.json; see ballot_measures_pdf.py) — the
    only way Civitas reads ballot measures. A state's registered source is
    its record of truth; a failure reads as ingest_failed. Returns
    (synced, failed_states, marked_removed).

    Outcomes per state:
    - NotYetPublished: not_yet_covered — unless this election was already
      covered or confirmed none (a document that was there and is now
      missing is a failure, not a wait), or the state's expected-by cutoff
      has passed for a document it publishes every election; both are
      ingest_failed.
    - None: ingest_failed; the rows on file are left as they are.
    - a list: guarded by MEASURE_SHRINK_FLOOR (_shrink_held_back), then
      written in one transaction (_write_direct_answer): superseded rows
      deleted, measures upserted, this source's unseen rows reconciled to
      removed, coverage set. A failure anywhere in it rolls the whole
      state back and reads as ingest_failed.
    """

    year = int(election_day[:4])
    synced = failed = marked_removed = 0
    if failing is None:
        failing = []
    for state in sorted(ballot_measure_pdf_sources.configured_states()):
        source = ballot_measure_pdf_sources.source_for_state(state)
        source_name = source["source_name"]
        fresh = ballot_measures_pdf.cached_answer(db, state, year) is None
        try:
            listed, reasons = await _fetch_with_reasons(client, db, state, year, election_day)
        except NotYetPublished as awaited:
            prior = _coverage_row(db, state, election_day)
            # Measures the source itself reports as no longer on this
            # ballot (Oklahoma: a State Question now dated otherwise) are
            # marked removed first. That is the source saying "measure
            # gone", not "document gone", so it is never the lost-document
            # alarm below.
            struck = _struck_ids(state, election_day, awaited.removed)
            if struck:
                marked_removed += _mark_removed(db, state, election_day, source_name, struck)
                db.commit()
            if prior is not None and prior.operator_note:
                # An operator accepted this absence (admin accept-absence),
                # and the reader again finds the document absent: that
                # answer stands — restored, whatever last night's status
                # was. operator_note is the durable fact; a night whose
                # fetch genuinely failed (ingest_failed, alerted) doesn't
                # clear it, only a real answer from the reader does.
                _set_coverage(
                    db, state, election_day, MeasureCoverage.CONFIRMED_NONE,
                    count=0, source_name=prior.source_name,
                    error=f"operator accepted absence: {prior.operator_note}",
                )
                db.commit()
                continue
            explained_all = bool(struck) and not _live_ids(db, state, source_name, {election_day})
            if not explained_all and _has_read_this_election(db, prior, state, election_day, source_name):
                reason = (
                    f"{source_name} was read successfully for this election before "
                    f"({prior.status if prior else 'rows on file'}), now reads as not published: {awaited}"
                )
            elif awaited.deadline_applies and _past_expected_by(source, election_day):
                reason = f"expected by now, still not published: {awaited}"
            else:
                # Not a failure and not an answer: the state has not
                # published the document yet. No alert, no claim.
                logger.info("Ballot measures for %s not published yet: %s", state, awaited)
                if not awaited.deadline_applies and _past_expected_by({}, election_day):
                    _late_cycle_notice(state, election_day, source_name, awaited)
                # Until this source answers, the measures on the page (if
                # any) are still the previous source's — a registry entry
                # renamed or re-pointed — so the coverage row keeps naming
                # that source rather than crediting its cards to this one.
                # Only while its rows are still on file: with none, the old
                # name would credit an empty page to it.
                keep_source = source_name
                if prior is not None and prior.source_name and prior.source_name != source_name:
                    still_on_file = (
                        db.query(BallotMeasure.id)
                        .filter(
                            BallotMeasure.state == state,
                            BallotMeasure.election_date == election_day,
                            BallotMeasure.source_name == prior.source_name,
                        )
                        .first()
                    )
                    if still_on_file is not None:
                        keep_source = prior.source_name
                _set_coverage(
                    db, state, election_day, MeasureCoverage.NOT_YET_COVERED,
                    source_name=keep_source, error=f"not yet published: {awaited}",
                )
                db.commit()
                continue
            logger.warning("Ballot measures for %s: %s", state, reason)
            _set_coverage(
                db, state, election_day, MeasureCoverage.INGEST_FAILED,
                count=prior.measure_count if prior else 0, source_name=source_name, error=reason,
            )
            failed += 1
            failing.append(state)
            db.commit()
            continue

        if listed is None:
            _set_coverage(
                db, state, election_day, MeasureCoverage.INGEST_FAILED,
                source_name=source_name,
                error="; ".join(dict.fromkeys(reasons)) or "fetch failed (the reader logged no reason)",
            )
            failed += 1
            failing.append(state)
            db.commit()
            continue

        active = [m for m in listed if not m.get("removed")]
        struck = [m for m in listed if m.get("removed")]
        dates = {election_day} | {m["election_date"] for m in active}
        existing = _live_measure_count(db, state, source_name, dates)
        # Only struck measures that are actually on file explain a drop: a
        # "Removed" row the state never listed for us removes nothing.
        explained = _live_ids(db, state, source_name, dates) & {m["id"] for m in struck}
        if _shrink_held_back(
            db, state, election_day, [m["id"] for m in active], len(active), len(explained), existing,
            fresh=fresh,
        ):
            # The next night must ask the state again, not replay this.
            ballot_measures_pdf.forget_cached(db, state, year)
            # Implausible shrink — keep what we have, say so loudly, and do
            # NOT reconcile. See MEASURE_SHRINK_FLOOR.
            logger.warning(
                "Measure sync for %s returned %d rows against %d on file — "
                "keeping existing data", state, len(active), existing,
            )
            _set_coverage(
                db, state, election_day, MeasureCoverage.INGEST_FAILED,
                count=existing, source_name=source_name,
                error=f"implausible shrink: {len(active)} vs {existing}",
            )
            failed += 1
            failing.append(state)
            db.commit()
            continue

        try:
            written, marked = _write_direct_answer(db, state, election_day, source_name, active, dates)
        except Exception:
            db.rollback()
            logger.exception("Writing %s's measures failed — nothing written", state)
            _set_coverage(
                db, state, election_day, MeasureCoverage.INGEST_FAILED,
                source_name=source_name, error="write failed",
            )
            failed += 1
            failing.append(state)
            db.commit()
            continue
        synced += written
        marked_removed += marked

    return synced, failed, marked_removed


def _write_direct_answer(
    db: Session, state: str, election_day: str, source_name: str, active: list[dict], dates: set[str],
) -> tuple[int, int]:
    """One state's successful read, as one transaction: superseded rows
    deleted, measures upserted, unseen rows reconciled, coverage set, then
    one commit. Raises (the caller rolls back) on any failure, so nothing
    is counted as synced or seen unless it was actually written."""

    retired = _supersede_rows(db, state, election_day, source_name, active)
    if retired:
        logger.info("Superseded %d %s measure row(s)", retired, state)
    for item in active:
        _upsert_measure(db, item, item, source_name)
    db.flush()
    seen_ids = {item["id"] for item in active}
    marked = _reconcile_state_measures(db, state, dates, seen_ids)
    _set_coverage(
        db, state, election_day,
        MeasureCoverage.COVERED if active else MeasureCoverage.CONFIRMED_NONE,
        count=len(seen_ids), source_name=source_name,
    )
    # The reader answered: an operator's accepted absence is superseded.
    _coverage_row(db, state, election_day).operator_note = None
    db.commit()
    return len(seen_ids), marked


def _resolve_answered_notices(db: Session, election_day: str) -> None:
    """Close each late-cycle notice whose state has now answered (measures
    covered, or confirmed none). Run after the sync commits: the alert
    store writes through its own session."""

    answered = db.query(MeasureCoverage.state).filter(
        MeasureCoverage.election_date == election_day,
        MeasureCoverage.status.in_((MeasureCoverage.COVERED, MeasureCoverage.CONFIRMED_NONE)),
    ).all()
    for (state,) in answered:
        ops_alerts.resolve_ops_alert(f"ballot-measure-late-{state}-{election_day}")


def _alert_ingest_failures(db: Session, failing: list[str], election_day: str) -> None:
    """One ops alert per night per set of failing states. The dedupe key
    carries the date and a digest of the states: send_ops_alert dedupes
    for as long as the key is remembered, so a constant key would make the
    first failure ever the last one anyone hears about — every later lost
    document, missed cutoff, held-back shrink or write failure silent.
    Same shape as state_candidates' recurring alerts."""


    if not failing:
        ops_alerts.resolve_ops_alert(f"ballot-measure-ingest-{election_day}")
        return
    states = sorted(set(failing))
    digest = hashlib.sha1("|".join(states).encode()).hexdigest()[:12]
    # Each state's own reason, as its coverage row recorded it — what the
    # reader said, so the alert answers "why" as well as "which".
    reasons = dict(
        db.query(MeasureCoverage.state, MeasureCoverage.error_detail)
        .filter(MeasureCoverage.election_date == election_day, MeasureCoverage.state.in_(states))
        .all()
    )
    why = "\n".join(f"- {state}: {reasons.get(state) or 'no reason recorded'}" for state in states)
    # Fail loud: a silently-broken adapter and a quiet week look identical
    # from the outside, and this is the one dataset where that ambiguity
    # costs a vote.
    try:
        ops_alerts.send_ops_alert(
            "Ballot measure ingest failed",
            f"{len(states)} state(s) failed to ingest statewide ballot measures "
            f"for {election_day}: {', '.join(states)}. Those states render as 'not yet covered' "
            f"rather than 'no measures' until this clears.\n{why}",
            dedupe_key=f"ballot-measure-ingest-{election_day}-{utcnow().date().isoformat()}-{digest}",
            condition=f"ballot-measure-ingest-{election_day}",
        )
    except Exception:
        logger.exception("Could not send ballot-measure ops alert")


def _prune_past_measures(db: Session) -> int:
    """Delete measures whose election is more than the removal grace
    window in the past. The state page shows only its current election
    (api/elections.state_ballot), and nothing else reads them; leaving
    them would let every past cycle's rows pile up under the state.

    Never the election the site is still on: after election day the state
    page stays on the election just held while its count is moving, up to
    January 3 (election_phase.active_election) — longer than the grace
    window in a slow count."""

    cutoff = min(
        (utcnow().date() - timedelta(days=MEASURE_REMOVAL_GRACE_DAYS)).isoformat(),
        active_election(db).election_day.isoformat(),
    )
    deleted = (
        db.query(BallotMeasure)
        .filter(BallotMeasure.election_date < cutoff)
        .delete(synchronize_session=False)
    )
    if deleted:
        db.commit()
        logger.info("Pruned %d ballot measures from past elections", deleted)
    return deleted


# The source name every row from the retired Vote Smart integration carries
# (its ids were "vs-<measureId>"). Civitas has no Vote Smart key and reads
# measures only from each state's own office now; _purge_retired_source
# deletes any such row still in a database, so none can render.
RETIRED_SOURCE_NAME = "Vote Smart"


def _purge_retired_source(db: Session) -> int:
    """Delete every ballot measure left by the retired Vote Smart
    integration. Idempotent (a no-op once they're gone). Such a row was
    never re-checked against the state and must not render as current —
    or as "removed", which would claim the state struck it."""

    deleted = (
        db.query(BallotMeasure)
        .filter(or_(BallotMeasure.source_name == RETIRED_SOURCE_NAME, BallotMeasure.id.like("vs-%")))
        .delete(synchronize_session=False)
    )
    # Its coverage rows go back to "nothing checked": a "last successful
    # read" date, a shrink streak or an operator's "none" recorded against
    # it describe reads this site no longer makes. The status itself is
    # rewritten by tonight's sync for every state.
    reset = 0
    for row in db.query(MeasureCoverage).filter(MeasureCoverage.source_name == RETIRED_SOURCE_NAME):
        if row.status in (MeasureCoverage.COVERED, MeasureCoverage.CONFIRMED_NONE):
            row.status = MeasureCoverage.NOT_YET_COVERED
            row.measure_count = 0
        row.source_name = None
        row.last_success_at = None
        row.pending_shrink = None
        row.shrink_streak = 0
        row.operator_note = None
        reset += 1
    if deleted or reset:
        db.commit()
        logger.info(
            "Retired Vote Smart source: deleted %d ballot measure rows, reset %d coverage rows",
            deleted, reset,
        )
    return deleted


def _record_unread_state(db: Session, state: str, election_day: str) -> None:
    """Record a state no reader covers as NOT_YET_COVERED.

    Anything the row carried from an earlier source's read is cleared: no
    reader is running, so a shrink streak can't continue and an operator's
    "none" (accept-absence needs a registered source) can't stand. The
    "last successful read" date (and the source it names) is kept only
    while measures from that read are still on file — it is what dates them; with none on file (the
    retired source's rows are purged) it would date a check this site no
    longer makes, beside a status saying nothing was checked.
    """


    reason = ballot_measure_pdf_sources.unread_reason(state) or "Civitas does not read this state's official measure list automatically yet."
    prior = _coverage_row(db, state, election_day)
    prior_source = prior.source_name if prior is not None else None
    _set_coverage(
        db, state, election_day, MeasureCoverage.NOT_YET_COVERED, error=f"no direct source: {reason}",
    )
    row = _coverage_row(db, state, election_day)
    row.pending_shrink = None
    row.shrink_streak = 0
    row.operator_note = None
    has_rows = (
        db.query(BallotMeasure.id)
        .filter(BallotMeasure.state == state, BallotMeasure.election_date == election_day)
        .first()
        is not None
    )
    if has_rows:
        # A reader de-registered mid-cycle: its rows stay as they are,
        # certified — marking them removed would claim the state struck
        # them, which nobody checked. The coverage row keeps naming their
        # source so the page can date them as that source's last read, and
        # the page words them from unreadReason ("no longer read"), never
        # as a check that failed. They are pruned with the election.
        row.source_name = prior_source
    else:
        row.last_success_at = None


async def _sync_ballot_measures(db: Session, client: httpx.AsyncClient, cycle: int) -> dict:
    """Sync statewide ballot measures: every state is read from its own
    office (ballot_measure_pdf_sources.json, _sync_pdf_measures), or not at
    all.

    The distinction this function exists to preserve: a state with no
    measures and a state we don't know about must never render the same
    way. A state with no registered direct source — Civitas does not
    read its official list automatically yet, or it publishes no list of
    what is certified — is recorded NOT_YET_COVERED, never CONFIRMED_NONE (nothing
    was checked), and raises no alert (nothing broke): its page says so and
    links the official lookup.
    """

    election_day = active_election(db).election_day.isoformat()
    _prune_past_measures(db)
    _purge_retired_source(db)

    failing: list[str] = []
    synced, failed, marked_removed = await _sync_pdf_measures(db, client, election_day, failing)

    unread = sorted((federal_states() | {"DC"}) - ballot_measure_pdf_sources.configured_states())
    for state in unread:
        _record_unread_state(db, state, election_day)
    db.commit()

    _alert_ingest_failures(db, failing, election_day)
    _resolve_answered_notices(db, election_day)

    return {
        "synced": synced,
        "failed_states": failed,
        "marked_removed": marked_removed,
        "not_read_states": len(unread),
    }


def _prune_stale_coverage(db: Session) -> int:
    """Delete coverage items older than COVERAGE_RETENTION_DAYS — see that
    constant's comment. Returns rows deleted."""
    cutoff = utcnow() - timedelta(days=COVERAGE_RETENTION_DAYS)
    deleted = (
        db.query(RaceCoverageItem)
        .filter(RaceCoverageItem.fetched_at < cutoff)
        .delete()
    )
    if deleted:
        db.commit()
        logger.info("Pruned %d coverage items older than %d days", deleted, COVERAGE_RETENTION_DAYS)
    return deleted


# What in a state's crawl outcome is worth the dashboard's line: a source
# or filing list adopted, a source failing or forgotten, a crawl that raised
# or couldn't save.
_CRAWL_NEWS = ("adopted", "failing since", "forgotten", "error", "save failed")


def _adopted_detail(changes: dict[str, str]) -> str:
    """The crawl's part of the phase's dashboard detail: what tonight's
    crawl changed or failed at — a source adopted or forgotten, a state
    whose crawl raised or couldn't save."""
    if not changes:
        return ""
    return "; crawl: " + ", ".join(f"{s} {changes[s]}" for s in sorted(changes))


async def _confirmed_candidates_phase(db: Session, client, cycle: int) -> str:
    """The nightly run's ballot sync, run holding the ballot sync's guards;
    returns the dashboard's detail line."""
    confirm_result, filing_result = await _sync_ballots(db, client, cycle)
    confirmed_total = sum(r["confirmed"] for r in confirm_result.values())
    logger.info("Confirmed candidates: %s", confirm_result)
    if filing_result:
        logger.info("Ballot filings: %s", filing_result)

    # The admin dashboard's only window into this phase beyond
    # a bare total — which states are actually configured, and
    # whether tonight's crawl found anything new — was
    # previously log-only (2026-09 gap: an admin
    # reading the dashboard had no way to tell "16 states
    # confirmed" from "every state failed but one").
    configured_states = sorted(
        s for s, r in confirm_result.items() if r["status"] == "ok"
    )
    detail = f"{confirmed_total} confirmed across {len(configured_states)} states"
    # Non-federal nominees are stored, not "confirmed"
    # against an FEC row, so they are invisible in the count
    # above — and there can be a lot of them (Rhode Island
    # alone stores 9 executive and 133 legislative). Reporting
    # only the federal number would let a run that did most of
    # its work off-ballot look like a quiet one, which is the
    # same misreading this detail line was added to prevent.
    non_federal = sum(
        r.get("statewide", 0) + r.get("stateLeg", 0) for r in confirm_result.values()
    )
    if non_federal:
        detail += f"; {non_federal} state-office nominees"
    return detail


async def run_election_pipeline(cycle: int | None = None) -> dict:
    """Sync candidate rosters, refresh a prioritized batch of financials,
    ingest race coverage, post grounded Bluesky updates, and snapshot
    fundraising. Returns a summary dict with counts."""
    db = SessionLocal()
    try:
        cycle = cycle if cycle is not None else current_election_cycle(db)
    except Exception:
        db.close()
        raise
    _run_token = None  # no run of ours for the finally to stop until start() below

    run, _run_token, refused = acquire_tracked_run(db, ElectionPipelineRun, STALE_PIPELINE_TIMEOUT, _tracker)
    if run is None:
        logger.warning("Election pipeline not started: %s", skip_reason_text(refused))
        db.close()
        return {"status": "skipped", "reason": refused}

    start_time = time.time()
    progress = ProgressTracker(run, ELECTION_PIPELINE_STEPS, db, start_time)

    try:
        logger.info("=== ELECTION PIPELINE START (cycle %d) ===", cycle)

        async with make_async_client() as client:
            # After election day the held election's ballot is final
            # (election_is_held): no phase that places candidates on it
            # re-reads its sources, which have moved on and would unwrite
            # what was certified. FEC's roster in particular gives each
            # candidate's LATEST district and incumbency, so a nominee who
            # files for the next cycle elsewhere would move races.
            # Financials still refresh: post-general reports are this
            # election's money.
            election_held = election_is_held(active_election(db))

            run.current_phase = "roster"
            db.commit()
            logger.info("--- Election: ROSTER SYNC ---")
            progress.begin("roster_sync")
            # Which states hold which Senate class, from the Senate's own
            # list, before the roster is filtered by it. Best-effort: a
            # failed read keeps what is stored (senate_classes.py).
            await senate_classes.refresh_senate_classes(client)
            try:
                if election_held:
                    progress.skip("roster_sync", detail=f"skipped: {ELECTION_HELD}")
                    raise _ElectionHeld
                house_raw = await fetch_all_candidates(client, db, cycle, "H")
                senate_raw = await fetch_all_candidates(client, db, cycle, "S")
                synced = _sync_roster(db, cycle, house_raw + senate_raw)
                run.candidates_synced = synced
                logger.info("Synced %d candidates", synced)
                progress.complete("roster_sync", detail=f"{synced} candidates")
            except _ElectionHeld:
                pass
            except Exception:
                db.rollback()
                logger.exception("Roster sync failed — continuing")
                progress.fail("roster_sync")

            run.current_phase = "financial"
            db.commit()
            logger.info("--- Election: FINANCIAL REFRESH ---")
            progress.begin("financial_refresh")
            try:
                refreshed = await _refresh_financials(db, client, FINANCIALS_BATCH_SIZE)
                run.financials_refreshed = refreshed
                logger.info("Refreshed financials for %d candidates", refreshed)
                progress.complete("financial_refresh", detail=f"{refreshed} refreshed")
            except Exception:
                db.rollback()
                logger.exception("Financial refresh phase failed — continuing")
                progress.fail("financial_refresh")

            run.current_phase = "confirmed_candidates"
            db.commit()
            logger.info("--- Election: CONFIRMED CANDIDATES ---")
            progress.begin("confirmed_candidates")
            confirmed_open = True  # until the phase is marked done or skipped
            try:
                if election_held:
                    progress.skip("confirmed_candidates", detail=f"skipped: {ELECTION_HELD}")
                    confirmed_open = False
                    raise _ElectionHeld
                # Each state is crawled weekly — what the crawl looks for,
                # a state standing up a results portal or a new cycle's file
                # appearing, moves on the scale of weeks — but the crawl
                # runs nightly over whichever states are due, so one that
                # failed is retried the next night (crawl_for_new_sources).
                # Runs BEFORE the sync so anything it proves out contributes
                # the same night, and in its own try: a crawl that raises
                # must not cost that night's sync.
                adopted: dict[str, str] = {}
                try:
                    leads = await crawl_for_new_sources(db, client, cycle)
                    adopted = {
                        s: r for s, r in leads.items()
                        if any(mark in r for mark in _CRAWL_NEWS)
                    }
                    logger.info("Source crawl: %s", leads or "no state due")
                except Exception:
                    db.rollback()
                    logger.exception("Source crawl failed — the sync runs anyway")
                # The election-season ballot sync may be mid-pass; two
                # passes writing the same Candidate rows at once is the one
                # thing to avoid, and that pass is doing this step anyway.
                # Holding the sync's tracker and lease (lease.tracked_job, as
                # the scheduled sync does), so a sync in this process or
                # another can't start beside this pass.
                async with lease.tracked_job_async(
                    lease.BALLOT_SYNC, _ballot_tracker, who="Election pipeline's confirmed-candidate phase",
                ) as granted:
                    if not granted:
                        progress.skip(
                            "confirmed_candidates", detail=f"skipped: {granted.why}{_adopted_detail(adopted)}",
                        )
                    else:
                        detail = await _confirmed_candidates_phase(db, client, cycle)
                        progress.complete("confirmed_candidates", detail=detail + _adopted_detail(adopted))
                    confirmed_open = False
            except _ElectionHeld:
                pass
            except lease.CutOff as cut:
                db.rollback()
                logger.warning("Confirmed-candidate phase: %s — continuing", cut)
                if confirmed_open:
                    progress.fail("confirmed_candidates")
            except Exception:
                db.rollback()
                logger.exception("Confirmed-candidate sync failed — continuing")
                if confirmed_open:
                    progress.fail("confirmed_candidates")

            run.current_phase = "measures"
            db.commit()
            logger.info("--- Election: BALLOT MEASURES ---")
            progress.begin("ballot_measures")
            try:
                if election_held:
                    # Earlier elections' rows still age out.
                    _prune_past_measures(db)
                    progress.skip("ballot_measures", detail=f"skipped: {ELECTION_HELD}")
                    raise _ElectionHeld
                measure_result = await _sync_ballot_measures(db, client, cycle)
                if measure_result.get("skipped"):
                    progress.complete("ballot_measures", detail="skipped (no API key)")
                else:
                    detail = (
                        f"{measure_result['synced']} measures, "
                        f"{measure_result['failed_states']} states failed"
                    )
                    logger.info("Ballot measures: %s", detail)
                    progress.complete("ballot_measures", detail=detail)
            except _ElectionHeld:
                pass
            except Exception:
                db.rollback()
                logger.exception("Ballot measure sync failed — continuing")
                progress.fail("ballot_measures")

            # Verify the official-ballot links we hand users. Cheap, and
            # the one link on the page whose failure strands the visitor.
            try:
                link_result = await ballot_lookup.refresh_link_verification(client)
                if link_result["failed"]:
                    logger.warning(
                        "%d state ballot-lookup links failed verification and are "
                        "now hidden", link_result["failed"],
                    )
            except Exception:
                logger.exception("Ballot lookup link verification failed — continuing")

            # Coverage + posting are also run by the 15-minute
            # election-season refresh (scheduler.py); concurrent passes would
            # double-ingest and double-post (2026-07 review B3). Holding the
            # refresh's lease and tracker (lease.tracked_job, as the refresh
            # does), so a refresh in this process or another can't run
            # beside this pass; if one is mid-flight, these two phases are
            # skipped and the in-season cadence re-covers them within 15
            # minutes. A failure here (taking the guards included) fails
            # whichever of these phases hadn't finished, not the rest of
            # the run.

            coverage_open = ["coverage_ingestion", "bluesky_posting"]  # phases not yet finished
            try:
                async with lease.tracked_job_async(
                    lease.COVERAGE_REFRESH, election_coverage.coverage_tracker(), who="Election pipeline's coverage/posting phases",
                ) as granted:
                    if not granted:
                        for phase in coverage_open:
                            progress.skip(phase, detail=f"skipped: {granted.why}")
                        coverage_open.clear()
                    else:
                        # The cut-off fires only at an await; the posting
                        # loop, which doesn't await, stops here itself.
                        coverage_deadline = lease.deadline(lease.COVERAGE_REFRESH)
                        run.current_phase = "coverage"
                        db.commit()
                        logger.info("--- Election: COVERAGE INGESTION ---")
                        progress.begin("coverage_ingestion")
                        try:
                            ingested = await election_coverage.ingest_race_coverage(db)
                            run.coverage_items_ingested = ingested
                            logger.info("Ingested %d coverage items", ingested)
                            progress.complete("coverage_ingestion", detail=f"{ingested} items")
                        except Exception:
                            db.rollback()
                            logger.exception("Coverage ingestion failed — continuing")
                            progress.fail("coverage_ingestion")
                        coverage_open.remove("coverage_ingestion")

                        run.current_phase = "posting"
                        db.commit()
                        logger.info("--- Election: BLUESKY POSTING ---")
                        progress.begin("bluesky_posting")
                        try:
                            if time.monotonic() >= coverage_deadline:
                                # The calibration and the posting loop don't
                                # await, so the cut-off can't stop them; not
                                # starting them past the deadline keeps them
                                # inside the guards (each is minutes at most,
                                # within the lease's stale window).
                                raise lease.CutOff("Election pipeline's posting phase reached its deadline")
                            # Re-derive the relevance cut from the corpus this
                            # run just ingested, before it gates that corpus —
                            # same order and same stale-beats-nothing failure
                            # mode as explore_ranking.calibrate_and_store.
                            race_relevance.calibrate_and_store(db)


                            if live_bluesky.counting_is_live(db):
                                # Election night: the live count's own posts
                                # have the account while totals move.
                                progress.complete("bluesky_posting", detail="stood down: the live count is posting")
                            else:
                                posted = election_bluesky.post_race_coverage_updates(db, deadline=coverage_deadline)
                                logger.info("Posted %d race coverage updates", posted)
                                progress.complete("bluesky_posting", detail=f"{posted} posted")
                        except lease.CutOff as cut:
                            logger.warning("%s — its items wait for the next run", cut)
                            progress.skip("bluesky_posting", detail="skipped: reached its deadline")
                        except Exception:
                            db.rollback()
                            logger.exception("Bluesky posting failed — continuing")
                            progress.fail("bluesky_posting")
                        coverage_open.remove("bluesky_posting")
            except lease.CutOff as cut:
                db.rollback()
                logger.warning("Election coverage/posting phases: %s — continuing", cut)
                for phase in coverage_open:
                    progress.fail(phase)
            except Exception:
                db.rollback()
                logger.exception("Election coverage/posting phases failed — continuing")
                for phase in coverage_open:
                    progress.fail(phase)

            run.current_phase = "snapshot"
            db.commit()
            logger.info("--- Election: SNAPSHOT ---")
            progress.begin("snapshot")
            try:
                snapshotted = _snapshot_candidates(db)
                _prune_stale_coverage(db)
                logger.info("Snapshotted %d candidates", snapshotted)
                progress.complete("snapshot", detail=f"{snapshotted} snapshotted")
            except Exception:
                db.rollback()
                logger.exception("Snapshot phase failed — continuing")
                progress.fail("snapshot")

        run.current_phase = "finalize"
        run.status = PipelineStatus.COMPLETED
        run.completed_at = utcnow()
        run.elapsed_seconds = round(time.time() - start_time, 1)
        db.commit()
        logger.info("=== ELECTION PIPELINE COMPLETE ===")

        return {
            "status": PipelineStatus.COMPLETED,
            "candidates_synced": run.candidates_synced,
            "financials_refreshed": run.financials_refreshed,
            "coverage_items_ingested": run.coverage_items_ingested,
            "elapsed_seconds": run.elapsed_seconds,
        }
    except Exception as e:
        logger.exception("Election pipeline failed: %s", e)
        summary = "election pipeline failed — see server logs"
        try:
            db.rollback()
            run.status = PipelineStatus.FAILED
            run.completed_at = utcnow()
            run.elapsed_seconds = round(time.time() - start_time, 1)
            run.error_message = summary
            db.commit()
        except Exception:
            logger.exception("Failed to record election pipeline failure")
        return {"status": PipelineStatus.FAILED, "error": summary}
    finally:
        _tracker.stop(_run_token)
        db.close()
