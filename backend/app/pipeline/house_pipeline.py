"""
House pipeline — fetch, transform, analyze, and persist representative data.

Runs as a separate pipeline from the Senate orchestrator. Shares analysis
modules (bill classification, donor classification, score calculation) but
uses House-specific data sources (clerk.house.gov for votes, office=H for FEC).

Design choice: skip LLM narrative generation on the first pass because
435 reps at ~2 min/call = ~14.5 hours. Scores, funding, and vote data are
computed deterministically and don't need LLM calls.
"""


import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.error_utils import record_exception
from app.http_client import make_async_client
from app.member_ids import assign_member_ids
from app.models import HousePipelineRun, PipelineStatus, Representative, ScoreSnapshot
from app.pipeline.analyze.bill_analyzer import (
    classify_all_bills,
    classify_policy_areas_multi,
)
from app.pipeline.analyze.bill_learning import stamp_motion_type
from app.pipeline.analyze.bill_stage import classify_bill_stage_from_actions, is_enacted
from app.pipeline.analyze.commemorative import mark_commemorative
from app.pipeline.analyze.cross_reference import detect_lobbying_matches
from app.pipeline.analyze.ground_truth import (
    check_ground_truth,
    check_score_distribution,
)
from app.pipeline.analyze.party_line_record import party_line_records
from app.pipeline.analyze.party_platform import (
    analyze_partisan_depth,
    classify_party_alignment_multi,
    clear_platform_cache,
    initialize_platform_embeddings,
    refine_with_vote_data,
)
from app.pipeline.analyze.policy_alignment import clear_alignment_cache
from app.pipeline.analyze.score_calculator import (
    calculate_confidence,
    calculate_scores,
    compute_overall_score,
    explain_scores,
    scoring_version,
)
from app.pipeline.analyze.signal_overlap import record_signal_overlap
from app.pipeline.analyze.sponsorship_analysis import (
    compute_bipartisanship_scores,
    compute_ideology_scores,
    compute_leadership_scores,
    describe_senator_position,
    party_ideology_bounds,
)
from app.pipeline.fetch.congress import (
    CongressUnavailable,
    congress_first_year,
    extract_official_title,
    fetch_bill_actions,
    fetch_bill_cosponsors,
    fetch_bill_summaries,
    fetch_bill_titles_or_none,
    fetch_house_roll_call_vote,
    fetch_member_detail,
    fetch_member_sponsored,
    fetch_recent_house_roll_calls,
    fetch_representatives,
    fetch_significant_bills,
)
from app.pipeline.fetch.fec import (
    committee_id_of,
    committee_master_cycles,
    compute_recent_election_cycles,
    fetch_candidate_committees,
    fetch_candidate_financials,
    fetch_committee_contributions,
    fetch_committee_master,
    fetch_committee_receipts,
    fetch_contribution_detail,
    fetch_pac_receipts,
    find_candidate,
    resolve_committee_meta,
    with_seat_election,
)
from app.pipeline.fetch.floor_logs import bill_id_from_number
from app.pipeline.fetch.house_clerk import fetch_house_caucuses, fetch_house_sworn_dates
from app.pipeline.fetch.lda import alert_if_lda_down, enrich_lobbying_matches_with_lda
from app.pipeline.fetch.voteview import refresh_member_ideal_points
from app.pipeline.live_references import (
    bills_source,
    live_constituent_reference_measured,
    live_funding_reference,
    live_les_reference,
)
from app.pipeline.member_lifecycle import (
    CHAMBER_HOUSE,
    purge_departed_members,
    reconcile_roster,
)
from app.pipeline.partisan_depth_store import finalize_stored_partisan_depth
from app.pipeline.progress_tracker import ProgressTracker
from app.pipeline.run_checks import (
    alert_member_failures,
    alert_unread_sponsorship,
    persist_ground_truth_failures,
    record_member_holds,
    run_calibration_check,
)
from app.pipeline.run_tracker import (
    STALE_PIPELINE_TIMEOUT,
    PipelineRunTracker,
    acquire_tracked_run,
    skip_reason_text,
)
from app.pipeline.senate_pipeline import invalidate_stale_analysis
from app.pipeline.sponsorship_backfill import (
    backfill_withheld_sponsorship_scores,
    carry_forward_sponsored_bills,
)
from app.pipeline.transform.committee_data import load_leadership_tenures
from app.pipeline.transform.normalize_finance import normalize_finance
from app.pipeline.transform.normalize_members import normalize_house_members
from app.pipeline.transform.normalize_votes import (
    compute_party_split,
    compute_party_vote_split,
    extract_representative_vote,
    find_house_roll_call,
    house_roll_call_id,
    is_housekeeping,
    majority_leader_spans,
    member_vote_entry,
    normalize_votes,
    stamp_roll_call_outcome,
)
from app.services.representative_service import upsert_representative
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

HOUSE_PIPELINE_STEPS = [
    ("fetch_members",       "fetch",     "Fetch House members"),
    ("normalize",           "transform", "Normalize members"),
    ("fetch_bills_votes",   "fetch",     "Fetch bills & votes"),
    ("classify_bills",      "analyze",   "Classify bills & recent votes"),
    ("sponsorship",         "analyze",   "Sponsorship leadership & ideology (SVD/PageRank)"),
    ("fec_scoring",         "analyze",   "FEC data & scoring"),
    ("snapshots",           "finalize",  "Snapshots & finalize"),
]

# Module-level tracker so the hourly action-center refresh can skip while
# the house pipeline is running (prevents concurrent SQLite write
# conflicts). See PipelineRunTracker's docstring for why this exists
# alongside HousePipelineRun's DB-persisted status.
_tracker = PipelineRunTracker()

# How many sponsored bills the sponsorship step reads between log lines: a
# cache warm-up night reads about 19,000 (2026-10-16), so this is a line
# every ten minutes or so at 3,000 reads an hour.
SPONSORSHIP_LOG_EVERY = 500


def is_house_pipeline_running() -> bool:
    return _tracker.is_running


def house_pipeline_hung(quiet_after: "timedelta | None" = None) -> bool:
    """Whether the in-process House run has gone quiet (no progress for
    `quiet_after`, run_tracker.QUIET_PIPELINE_TIMEOUT by default) or is past
    the age ceiling: presumed hung, so a wait on it ends. False when idle.

    Lets callers distinguish a live run from a hung one — a run wedged
    silently mid-phase would otherwise hold the running flag indefinitely
    and block every hourly action-center refresh behind it.
    """
    return _tracker.hung(quiet_after) if quiet_after is not None else _tracker.hung()


def recent_not_covered_by_key_bills(
    classified_recent: list[dict], house_roll_calls: dict[str, dict],
) -> list[dict]:
    """Recent House roll calls that aren't already a key bill's roll call.

    A key bill's floor vote is often also one of the 120 recent roll calls.
    Counting it through both paths gave that vote double weight in the
    member's record; the key-bill entry wins because it carries the bill's
    real name and content classification. Senate twin:
    senate_pipeline._recent_not_covered_by_key_bills.
    """
    covered = {house_roll_call_id(rc) for rc in house_roll_calls.values()}
    return [b for b in classified_recent if b.get("billId", "") not in covered]


def roll_call_year(congress: int, now) -> tuple[int, bool]:
    """(the Clerk year to read `congress`'s recent roll calls from, whether
    that year is young — the first half of the year running now). The
    clock's year, clamped to `congress`'s last: a job still holding the
    outgoing Congress after the new one's first votes reads its own
    Congress's final year, not the new one's. A clamped year is complete,
    never young."""
    year = min(now.year, congress_first_year(congress) + 1)
    return year, year == now.year and now.month <= 6


def seated_at_convening(sworn_date: str | None, congress: int) -> bool:
    """Sworn in when `congress` convened (January 3 of its first year), so
    the general election before it is the one that seated the member. A
    special-election winner, or a member whose date isn't known, is not."""
    try:
        sworn = date.fromisoformat((sworn_date or "")[:10])
    except ValueError:
        return False
    return sworn <= date(congress_first_year(congress), 1, 3)


def in_congress(roll_calls: list[dict], congress: int) -> list[dict]:
    """The roll calls of `congress`: any whose Clerk XML names another
    Congress is dropped, whichever year's folder it was read from. One with
    no Congress in its metadata (0) is kept — its year already bounds it."""
    return [rc for rc in roll_calls if (rc.get("congress") or congress) == congress]


@dataclass
class _HouseRun:
    """One House run: its fixed context, and what each step of
    run_house_pipeline hands the steps after it (each step's docstring says
    which fields it reads and which it sets)."""

    db: Session
    progress: ProgressTracker
    house_run: HousePipelineRun
    start_time: float
    # _fetch_members
    raw_members: list[dict] = field(default_factory=list)
    member_details: dict[str, dict] = field(default_factory=dict)
    # _normalize
    reps: list[dict] = field(default_factory=list)
    # _fetch_bills_and_votes (unread_bills grows through the run: every
    # record that couldn't be read, named in one alert after scoring)
    congress: int = 0
    bills_data: list[dict] = field(default_factory=list)
    bill_actions_map: dict[str, list] = field(default_factory=dict)
    unread_bills: list[str] = field(default_factory=list)
    house_roll_calls: dict[str, dict] = field(default_factory=dict)
    recent_rc_map: dict[str, dict] = field(default_factory=dict)
    # _classify
    classified_bills: list[dict] = field(default_factory=list)
    classified_recent: list[dict] = field(default_factory=list)
    # _sponsorship
    leadership_scores: dict[str, float] = field(default_factory=dict)
    ideology_scores: dict[str, float] = field(default_factory=dict)
    ideology_bounds_by_party: dict[str, tuple[float, float]] = field(default_factory=dict)
    bipartisanship_scores: dict[str, float] = field(default_factory=dict)
    attracted_bipartisanship_scores: dict[str, float] = field(default_factory=dict)
    carried_members: list[str] = field(default_factory=list)
    # _prepare_reps: the FEC committee master and every committee-to-
    # candidate contribution, loaded on the first member with receipts
    committee_master: dict[str, dict] | None = None
    committee_contributions: dict | None = None
    les_reference: dict | None = None
    prepared_reps: list[tuple[dict, str]] = field(default_factory=list)
    success_count: int = 0
    fail_count: int = 0
    # (member id, exception) for each member that failed: one ops alert
    # after the scoring pass (alert_member_failures)
    member_failures: list[tuple[str, BaseException]] = field(default_factory=list)
    # _score_reps: the Constituent Alignment reference the scores used,
    # and whether this run measured it (the ground-truth gate reads both)
    constituent_reference: dict | None = None
    constituent_reference_measured: bool = False


async def _fetch_members(run: _HouseRun, client: httpx.AsyncClient) -> bool:
    """Phase 1: the House roster and each member's Congress.gov detail
    record. Sets raw_members and member_details; False when the roster came
    back empty (the run ends, no_data)."""
    logger.info("--- House Phase 1: FETCH MEMBERS ---")
    run.progress.begin("fetch_members")
    run.raw_members = await fetch_representatives(client, run.db)
    logger.info("Fetched %d raw House members", len(run.raw_members))
    if not run.raw_members:
        return False

    for i, m in enumerate(run.raw_members):
        bio_id = m.get("bioguideId", "")
        if bio_id:
            detail = await fetch_member_detail(client, run.db, bio_id)
            if detail:
                run.member_details[bio_id] = detail
        if (i + 1) % 50 == 0:
            logger.info("Member details: %d/%d", i + 1, len(run.raw_members))
        run.progress.heartbeat("fetch_members", detail=f"member details {i + 1} of {len(run.raw_members)}")

    logger.info("Fetched details for %d members", len(run.member_details))
    run.progress.complete("fetch_members", detail=f"{len(run.raw_members)} found, {len(run.member_details)} detailed")
    return True


async def _normalize(run: _HouseRun, client: httpx.AsyncClient) -> None:
    """Phase 2: the roster normalised, ids settled, departures reconciled, each member's sworn date and recorded caucus. Sets reps."""
    logger.info("--- House Phase 2: NORMALIZE ---")
    run.progress.begin("normalize")
    run.reps = normalize_house_members(run.raw_members, run.member_details)
    logger.info("Normalized %d representatives", len(run.reps))
    # Settle ids against the stored members by bioguide id, renaming
    # any whose id changed, before anything below writes by id.
    assign_member_ids(run.db, CHAMBER_HOUSE, run.reps)
    run.db.commit()
    run.progress.complete("normalize", detail=f"{len(run.reps)} representatives")

    # Reconcile the roster: anyone in the database but not in
    # tonight's fetch has left office, and anyone who left long
    # enough ago gets removed. See member_lifecycle.py — the House
    # is where this matters most, since special elections leave
    # seats open for months at a time.
    reconcile_roster(run.db, CHAMBER_HOUSE, {m.get("bioguideId", "") for m in run.raw_members})
    purge_departed_members(run.db, CHAMBER_HOUSE)
    run.db.commit()

    # When each member took the seat, for Legislative Effectiveness's
    # mid-Congress proration (v6.23). The Clerk's list when it can be
    # read; otherwise the dates stored last, so one failed request
    # doesn't score a special-election arrival against a full term.
    sworn_dates = await fetch_house_sworn_dates(client, run.db)
    if sworn_dates:
        for r in run.reps:
            r["swornDate"] = sworn_dates.get(r.get("bioguideId", ""))
    else:
        stored = dict(
            run.db.query(Representative.bioguide_id, Representative.sworn_date)
            .filter(Representative.sworn_date.isnot(None))
            .all()
        )
        for r in run.reps:
            if r.get("bioguideId") in stored:
                r["swornDate"] = stored[r["bioguideId"]]

    # The caucus the Clerk records, which decides an independent's
    # party-line reading (normalize_votes' declared_caucus).
    caucuses = await fetch_house_caucuses(client, run.db)
    if caucuses is None:
        # The Clerk's list couldn't be read and none is cached: each
        # member's stored caucus, not "none recorded".
        caucuses = dict(
            run.db.query(Representative.bioguide_id, Representative.caucus_party)
            .filter(Representative.caucus_party.isnot(None))
            .all()
        )
    for r in run.reps:
        r["declaredCaucus"] = caucuses.get(r.get("bioguideId", ""))


async def _fetch_bills_and_votes(run: _HouseRun, client: httpx.AsyncClient) -> None:
    """Phase 3: the key bills with their records, the roll calls their actions name, and the Congress's recent roll calls from the Clerk (which must be read: saving without them would erase every member's votes). Sets bills_data, unread_bills, house_roll_calls and recent_rc_map (and congress, the one this job holds)."""
    logger.info("--- House Phase 3: FETCH BILLS & VOTES ---")
    run.progress.begin("fetch_bills_votes")

    run.bills_data = await fetch_significant_bills(client, run.db, max_bills=40)
    logger.info("Discovered %d significant bills", len(run.bills_data))

    # Each bill's actions, for House vote discovery
    run.bill_actions_map = {}

    # Bills whose actions Congress.gov couldn't give this run: each
    # degrades only itself (one alert after the scoring pass names
    # them), never the run — one failed request used to stop the
    # whole step it sat in.
    run.unread_bills = []
    for b in run.bills_data:
        bill_key = f"{b['type'].upper()}.{b['number']}"
        try:
            actions = await fetch_bill_actions(client, run.db, b["congress"], b["type"], b["number"])
        except CongressUnavailable:
            # No roll call is discovered from this bill this run.
            run.unread_bills.append(f"{bill_key} ({b['congress']}) actions")
            continue
        if actions:
            run.bill_actions_map[bill_key] = actions

    # Discover House roll calls from bill actions
    run.house_roll_calls = {}
    for bill_key, actions in run.bill_actions_map.items():
        rc_info = find_house_roll_call(actions)
        if rc_info:
            rc_data = await fetch_house_roll_call_vote(
                client, run.db,
                rc_info["year"],
                rc_info["rollCallNumber"],
            )
            if rc_data:
                run.house_roll_calls[bill_key] = rc_data

    logger.info("Found %d House roll calls from bill actions", len(run.house_roll_calls))

    # Fetch recent House roll calls — 120, up from the original 15.
    # Rep IV was resting on ~28 votes each; after bipartisan votes
    # are excluded from party-loyalty counting, a thin sample left
    # many reps near the >=3 divided-vote floor. clerk.house.gov
    # roll-call XML is cheap and cached; classification of each
    # vote is also cached, so the marginal cost after the first
    # run is zero. Pull the prior year too when the current year
    # is young (fewer than 60 roll calls yet) — but only if that
    # prior year is still within the current congress (2 calendar
    # years/term; see AGENTS.md "current term"), otherwise it would
    # silently pull in the previous congress's votes.
    # The year is the clock's, clamped to the Congress this job
    # holds (app.config.scoring_congress): a job that started before
    # noon ET on Jan 3 and reaches this step after the new
    # Congress's first votes still reads its own Congress's last
    # year. And any roll call whose XML names another Congress is
    # dropped, whatever folder it came from.
    run.congress = settings.CURRENT_CONGRESS
    current_year, year_is_young = roll_call_year(run.congress, utcnow())
    recent_rcs = await fetch_recent_house_roll_calls(client, run.db, year=current_year, count=120)
    same_congress_prior_year = current_year > congress_first_year(run.congress)
    if recent_rcs is not None and len(recent_rcs) < 60 and year_is_young and same_congress_prior_year:
        earlier = await fetch_recent_house_roll_calls(
            client, run.db, year=current_year - 1, count=120 - len(recent_rcs),
        )
        recent_rcs = None if earlier is None else recent_rcs + earlier
    if recent_rcs is None:
        # The Clerk could not be read. Saving now would replace
        # every member's stored votes with none, so the run fails
        # here, before any member is written.
        raise RuntimeError("House roll calls could not be read from clerk.house.gov")
    recent_rcs = in_congress(recent_rcs, run.congress)
    logger.info("Fetched %d recent House roll calls", len(recent_rcs))

    # Map recent roll calls by a synthetic billId
    run.recent_rc_map = {}
    for rc in recent_rcs:
        bill_id = house_roll_call_id(rc)
        run.recent_rc_map[bill_id] = rc

    run.progress.complete(
        "fetch_bills_votes",
        detail=f"{len(run.bills_data)} bills, {len(run.house_roll_calls)} roll calls, {len(recent_rcs)} recent",
    )


async def _classify(run: _HouseRun, client: httpx.AsyncClient) -> None:
    """Phase 4: the key bills and recent roll calls classified, each party leaning settled by its roll call's split. Sets classified_bills and classified_recent."""
    logger.info("--- House Phase 4: CLASSIFY BILLS ---")
    run.progress.begin("classify_bills")


    bills_for_classification = []
    for b in run.bills_data:
        bill_key = f"{b['type'].upper()}.{b['number']}"
        summaries = await fetch_bill_summaries(client, run.db, b["congress"], b["type"], b["number"])
        if summaries is None:
            # Classified from its title this run; named in the alert.
            run.unread_bills.append(f"{bill_key} ({b['congress']}) summary")
        summary_text = ""
        if summaries:
            summary_text = summaries[0].get("text", "")

        titles = await fetch_bill_titles_or_none(client, run.db, b["congress"], b["type"], b["number"])
        if titles is None:
            run.unread_bills.append(f"{bill_key} ({b['congress']}) titles")
        official_title = extract_official_title(titles or [])

        bills_for_classification.append({
            "billId": bill_key,
            "billName": b["name"],
            "officialTitle": official_title,
            "congress": b["congress"],
            "type": b["type"],
            "summary": summary_text,
            "actions": run.bill_actions_map.get(bill_key, []),
        })

    run.classified_bills = await classify_all_bills(bills_for_classification, run.db)
    logger.info("Classified %d bills", len(run.classified_bills))

    # Also classify recent roll calls
    recent_for_classification = []
    for bill_id, rc in run.recent_rc_map.items():
        recent_for_classification.append({
            "billId": bill_id,
            "billName": rc.get("documentTitle") or rc.get("voteTitle", ""),
            "congress": rc.get("congress", settings.CURRENT_CONGRESS),
            "summary": "",
            "actions": [],
        })

    run.classified_recent = await classify_all_bills(recent_for_classification, run.db)

    logger.info("Classified %d recent House votes", len(run.classified_recent))

    # Refine LLM party leanings with actual roll-call splits.
    # The real member-vote split is authoritative for party-loyalty
    # measurement (see refine_with_vote_data) — previously it was
    # only used when the LLM produced no label, so bipartisan-passed
    # bills kept partisan content labels and half the chamber was
    # marked as voting "against party" on near-unanimous bills.

    for bill in run.classified_bills:
        bill_id = bill.get("billId", "")
        rc = run.house_roll_calls.get(bill_id)
        if rc:
            stamp_roll_call_outcome(bill, rc)
            stamp_motion_type(bill, rc)
            if bill.get("housekeeping"):
                continue
            vote_split = compute_party_vote_split(rc)
            split = vote_split["label"] if vote_split else None
            bill["partyLeaning"] = refine_with_vote_data(
                bill.get("partyLeaning", "bipartisan"), split,
            )

    for bill in run.classified_recent:
        bill_id = bill.get("billId", "")
        rc = run.recent_rc_map.get(bill_id)
        if rc:
            stamp_roll_call_outcome(bill, rc)
            stamp_motion_type(bill, rc)
            if bill.get("housekeeping"):
                continue
            vote_split = compute_party_vote_split(rc)
            split = vote_split["label"] if vote_split else None
            bill["partyLeaning"] = refine_with_vote_data(
                bill.get("partyLeaning", "bipartisan"), split,
            )

    run.progress.complete(
        "classify_bills",
        detail=f"{len(run.classified_bills)} bills, {len(run.classified_recent)} recent votes",
    )


async def _analyze_cosponsorship(run: _HouseRun, all_bills_for_analysis: list[dict], cosponsors_map: dict[str, list[dict]]) -> None:
    """The chamber's leadership (PageRank), bipartisanship and ideology (SVD) scores from the key and sponsored bills' cosponsorship, and Voteview's ideal points refreshed. Sets leadership_scores, bipartisanship_scores, attracted_bipartisanship_scores, ideology_scores and ideology_bounds_by_party."""
    total_cosponsors = sum(len(v) for v in cosponsors_map.values())
    logger.info(
        "Cosponsorship data: %d bills with cosponsors (%d total cosponsorships)",
        len(cosponsors_map), total_cosponsors,
    )

    rep_bio_ids = {
        r.get("bioguideId", "")
        for r in run.reps
        if r.get("bioguideId")
    }
    rep_party_map = {
        r.get("bioguideId", ""): r.get("party", "")
        for r in run.reps
        if r.get("bioguideId")
    }
    run.leadership_scores = compute_leadership_scores(
        all_bills_for_analysis, cosponsors_map, rep_bio_ids, rep_party_map, chamber="house",
    )
    run.bipartisanship_scores = compute_bipartisanship_scores(
        all_bills_for_analysis, cosponsors_map, rep_party_map,
    )
    # Receive-only variant for Legislative Effectiveness's
    # coalition-attraction component (score_calculator v6.11) —
    # see senate_pipeline's identical call for the rationale.
    run.attracted_bipartisanship_scores = compute_bipartisanship_scores(
        all_bills_for_analysis, cosponsors_map, rep_party_map,
        direction="receive",
    )
    run.ideology_scores = compute_ideology_scores(
        all_bills_for_analysis, cosponsors_map, rep_bio_ids, rep_party_map,
    )
    # Party-relative ideology label thresholds over the full
    # cohort (see party_ideology_bounds) so progressive/moderate/
    # centrist reflects position WITHIN a party, not party identity.
    run.ideology_bounds_by_party = party_ideology_bounds(
        [(run.ideology_scores.get(bio), rep_party_map.get(bio)) for bio in rep_bio_ids]
    )
    # Refresh this chamber's roll-call ideal points from
    # Voteview (position-congruence component, score_calculator
    # v6.11). Best-effort: never raises; a fetch/gate failure
    # keeps the last good /data/member_ideal_points.json section.
    await refresh_member_ideal_points("house", settings.CURRENT_CONGRESS)
    logger.info(
        "Sponsorship analysis: %d leadership scores, %d ideology scores",
        len(run.leadership_scores), len(run.ideology_scores),
    )
    run.progress.complete(
        "sponsorship",
        detail=f"{len(run.leadership_scores)} leadership, {len(run.ideology_scores)} ideology",
    )


def _sponsored_entry(run: _HouseRun, sp: dict, sp_key: str, sp_title: str, latest_action_text: str, sp_congress: int, bill_actions: list[dict] | None) -> dict:
    """One sponsored bill as the scorecard keeps it: law or not, its stage from its actions (None when they weren't read), its policy areas from its title and CRS area, and its party leaning, refined by its roll call when it had one."""
    is_law = is_enacted(latest_action_text, bill_actions)
    # Sponsored bills previously got no policy/party
    # classification at all here (always None/[]) —
    # unlike Senate, which classifies every sponsored
    # bill. Title-only text is thinner than the
    # official-title+summary text key bills get
    # (fast-follow, not fixed here), but it's real
    # signal where there was none before. Refine with
    # the real roll-call split when this bill also
    # got a floor vote, same as classified_bills/
    # classified_recent above — a bill that's both
    # sponsored and voted on should show one
    # consistent label, not content-only here and
    # vote-refined elsewhere on the same scorecard.
    policy_areas_raw: list[dict] = []
    party_leaning = None
    # The CRS policy area Congress.gov assigns, as the
    # Senate reads it: shown as the bill's area, and in
    # the classified text beside the title. The short
    # title alone filed about half of a sample wrong
    # (2,072 House bills under LABOR, 2026-10-08).
    crs_area = (sp.get("policyArea") or {}).get("name", "")
    classify_text = " ".join(t for t in (sp_title, crs_area) if t)
    if classify_text and len(classify_text) > 10:
        policy_areas_raw = classify_policy_areas_multi(classify_text, db_session=run.db)
        if policy_areas_raw:
            alignment = classify_party_alignment_multi(
                classify_text, policy_areas_raw, "pro",
            )
            party_leaning = alignment.get("overall", "bipartisan")
            rc = run.house_roll_calls.get(sp_key)
            if rc and not is_housekeeping(rc.get("question"), rc.get("chamber")):
                split = compute_party_split(rc)
                party_leaning = refine_with_vote_data(party_leaning, split)
    return {
        "billId": sp_key,
        "title": sp_title,
        "introducedDate": sp.get("introducedDate", ""),
        "latestAction": latest_action_text,
        "latestActionDate": (sp.get("latestAction") or {}).get("actionDate", ""),
        "policyArea": (
            crs_area.upper().replace(" ", "_") if crs_area
            else policy_areas_raw[0]["area"] if policy_areas_raw else ""
        ),
        "policyAreas": [
            {
                "area": a["area"],
                "confidence": a["confidence"],
                "party": {
                    pa["area"]: pa["party"]
                    for pa in alignment.get("areas", [])
                }.get(a["area"], "bipartisan"),
            }
            for a in policy_areas_raw
        ] if policy_areas_raw else [],
        "partyLeaning": party_leaning,
        "congress": sp_congress,
        "billType": sp.get("type", ""),
        "isLaw": is_law,
        "stage": (
            classify_bill_stage_from_actions(bill_actions, is_law, sp.get("type"))
            if bill_actions is not None else None
        ),
    }


async def _sponsorship(run: _HouseRun, client: httpx.AsyncClient) -> None:
    """Phase 4b: each member's sponsored bills of this Congress, and from them and the key bills' cosponsors the leadership, ideology and bipartisanship scores (a failure leaves them empty and the run goes on). Then the bills no run read carried forward, commemoratives marked and withheld scores backfilled. Sets each rep's sponsoredBills, leadership_scores, ideology_scores, ideology_bounds_by_party, bipartisanship_scores, attracted_bipartisanship_scores and carried_members."""
    logger.info("--- House Phase 4b: SPONSORSHIP ANALYSIS ---")
    # Its total is the members, the loop that dominates the step:
    # each member's sponsored bills are known only once read, so
    # the bills are counted as they go (heartbeat below).
    run.progress.begin("sponsorship", total=len(run.reps))
    # The scores start empty (_HouseRun), so a failure below leaves the run
    # with empty maps: no sponsorship figures, and the ideology label on the
    # fixed global cutoffs.
    cosponsors_map: dict[str, list[dict]] = {}
    all_bills_for_analysis: list[dict] = []

    try:
        # Fetch cosponsors for significant bills to build the
        # rep-rep cosponsorship graph. This reuses bills already
        # fetched in Phase 3, keeping API calls manageable (~40).
        for b in run.bills_data:
            bill_key = f"{b['type'].upper()}.{b['number']}"
            cosponsors = await fetch_bill_cosponsors(
                client, run.db, b["congress"], b["type"], b["number"],
            )
            if cosponsors is None:
                # Unread, and no earlier read to stand in: the bill
                # is left out of the cosponsorship graphs rather
                # than read as having no cosponsors.
                run.unread_bills.append(f"{bill_key} ({b['congress']}) cosponsors")
                continue
            if cosponsors:
                cosponsors_map[bill_key] = cosponsors
            all_bills_for_analysis.append({
                "billId": bill_key,
                "congress": b["congress"],
                "sponsorBioguide": b.get("sponsorBioguide", ""),
                "sponsorParty": b.get("sponsorParty", ""),
            })

        # Enrich with per-rep sponsored bills (up to 5 per rep,
        # current Congress only — fetch_member_sponsored already
        # filters this via _recent_congresses_only) to make the
        # cosponsorship matrix denser.
        min_congress = settings.CURRENT_CONGRESS
        sponsored_for_cosponsor: list[dict] = []
        bills_read = 0

        def sponsorship_heartbeat(members_done: int) -> None:
            # A night that misses the cache reads every sponsored
            # bill's actions and cosponsors: hours of rate-limited
            # requests that must not look like a hang.
            run.progress.heartbeat(
                "sponsorship", done=members_done,
                detail=f"member {members_done} of {len(run.reps)} · {bills_read:,} sponsored bills read",
            )

        for rep_idx, r in enumerate(run.reps):
            bio_id = r.get("bioguideId", "")
            party = r.get("party", "")
            if not bio_id:
                continue
            sponsored = await fetch_member_sponsored(client, run.db, bio_id)
            # None: the request failed with nothing cached — scored
            # neutral on Legislative Effectiveness, not as zero bills.
            r["sponsoredBillsUnavailable"] = sponsored is None
            sp_list = []
            for sp in (sponsored or []):
                if sp.get("congress", 0) >= min_congress:
                    sp_type = (sp.get("type") or "hr").upper()
                    sp_num = sp.get("number", "")
                    if not sp_num:
                        continue
                    sp_key = f"{sp_type}.{sp_num}"
                    sp_title = sp.get("title", "")
                    latest_action_text = (sp.get("latestAction") or {}).get("text", "")
                    sp_congress = sp.get("congress", 0)
                    try:
                        bill_actions = await fetch_bill_actions(
                            client, run.db, sp_congress, sp_type.lower(), int(sp_num),
                        )
                    except CongressUnavailable:
                        # This bill alone goes without its history:
                        # is_law from its latest action, and no stage
                        # (Legislative Effectiveness reads the latest
                        # action instead, _les_bill_stage).
                        bill_actions = None
                        run.unread_bills.append(f"{sp_key} ({sp_congress}) actions")
                    bills_read += 1
                    if bills_read % SPONSORSHIP_LOG_EVERY == 0:
                        logger.info(
                            "Sponsorship: %d sponsored bills read (member %d of %d)",
                            bills_read, rep_idx + 1, len(run.reps),
                        )
                    sponsorship_heartbeat(rep_idx)
                    sp_list.append(_sponsored_entry(run, sp, sp_key, sp_title, latest_action_text, sp_congress, bill_actions))
            # sp_list is already bounded to the current + previous
            # congress (min_congress filter above), so this is a
            # generous safety net, not a real-world limit. A tighter
            # cap here would silently truncate the scoring input —
            # corrupting both Legislative Effectiveness's volume AND
            # advancement components (advancement is computed over
            # this same list), worse for longer-tenured, more
            # prolific sponsors. Embedding the extra titles in
            # positions_from_sponsored_bills is cheap (no LLM), so
            # there's no real cost to a generous cap.
            r["sponsoredBills"] = sp_list[:500]
            # Every current-Congress sponsored bill feeds ideology
            # (SVD) and leadership (PageRank), as in the Senate: the
            # first five per member were 18% of the House's 12,287
            # (2026-10-08), so a prolific sponsor was read from a
            # sliver of their own record.
            for sp_data in sp_list:
                if sp_data.get("congress", settings.CURRENT_CONGRESS) != settings.CURRENT_CONGRESS:
                    continue
                sp_bill_id = sp_data["billId"]
                if sp_bill_id in cosponsors_map:
                    continue
                parts = sp_bill_id.split(".")
                if len(parts) == 2 and parts[1].isdigit():
                    cosponsors = await fetch_bill_cosponsors(
                        client, run.db,
                        sp_data.get("congress", settings.CURRENT_CONGRESS),
                        parts[0].lower(),
                        int(parts[1]),
                    )
                    sponsorship_heartbeat(rep_idx)
                    if cosponsors is None:
                        # Left out of the graphs, not read as none.
                        run.unread_bills.append(f"{sp_bill_id} ({sp_data.get('congress')}) cosponsors")
                        continue
                    if cosponsors:
                        cosponsors_map[sp_bill_id] = cosponsors
                sponsored_for_cosponsor.append({
                    "billId": sp_bill_id,
                    "congress": sp_data.get("congress", settings.CURRENT_CONGRESS),
                    "sponsorBioguide": bio_id,
                    "sponsorParty": party,
                    "isLaw": sp_data.get("isLaw", False),
                    "latestAction": sp_data.get("latestAction", ""),
                })
            sponsorship_heartbeat(rep_idx + 1)
        run.progress.update(
            "sponsorship", done=len(run.reps), detail=f"{bills_read:,} sponsored bills read; analyzing",
        )

        all_bills_for_analysis.extend(sponsored_for_cosponsor)
        await _analyze_cosponsorship(run, all_bills_for_analysis, cosponsors_map)
    except Exception as phase4b_err:
        logger.error(
            "Phase 4b failed (%s) — continuing with empty sponsorship scores",
            phase4b_err,
        )
        run.progress.fail("sponsorship", detail="failed — continuing with empty scores")

    # A member whose sponsored legislation wasn't read this run (the
    # request failed, or the step above stopped before reaching
    # them) is scored on the bills an earlier run stored, never as
    # having none; one stored by no run leaves the bill part
    # unmeasured.
    run.carried_members = carry_forward_sponsored_bills(run.db, Representative, run.reps)

    # Commemorative bills (V&W's 1x tier) — before the LES reference
    # is measured, since its stage totals are significance-weighted.
    mark_commemorative([sp for r in run.reps for sp in r.get("sponsoredBills") or []])

    # A withheld or failed analysis leaves members out of these
    # dicts; score them with last run's values, as the Senate does.

    backfill_withheld_sponsorship_scores(
        run.db, Representative, {r["bioguideId"] for r in run.reps if r.get("bioguideId")},
        run.leadership_scores, run.ideology_scores,
        run.bipartisanship_scores, run.attracted_bipartisanship_scores,
    )


async def _prepare_one(run: _HouseRun, client: httpx.AsyncClient, idx: int, rep: dict, recent_only: list[dict], lda_totals: dict[str, int]) -> None:
    """One member's votes on the key bills and recent roll calls, FEC funding and donor-vote matches, added to prepared_reps (and the LDA lookups counted into lda_totals). Raises when a read fails; _prepare_reps records the member as failed."""
    bio_id = rep.get("bioguideId", "")
    rep_name = rep.get("name", "")
    rep_state = rep.get("state", "")
    rep_district = rep.get("district", 0)

    if (idx + 1) % 25 == 0:
        logger.info("Processing representative %d/%d: %s", idx + 1, len(run.reps), rep_name)
        run.progress.update("fec_scoring", done=idx + 1)

    # Extract votes from key bills
    rep_votes: dict[str, str] = {}
    for bill in run.classified_bills:
        bill_id = bill.get("billId", "")
        rc = run.house_roll_calls.get(bill_id)
        if rc:
            vote = extract_representative_vote(
                rc, bio_id,
                rep.get("lastNameForVoteMatch"),
                rep_state,
            )
            if vote:
                rep_votes[bill_id] = vote

    # Extract recent votes
    recent_votes_list = []
    for bill in recent_only:
        bill_id = bill.get("billId", "")
        rc = run.recent_rc_map.get(bill_id)
        if rc:
            vote = extract_representative_vote(
                rc, bio_id,
                rep.get("lastNameForVoteMatch"),
                rep_state,
            )
            if vote:
                recent_votes_list.append({
                    **bill,
                    "vote": vote,
                })

    # Normalize votes. The majority leader's Nay on a
    # failing motion is the reconsider switch, not a break
    # (MAJORITY_LEADER_TITLES).
    leader_spans = majority_leader_spans(
        rep.get("leadershipTitle"),
        load_leadership_tenures().get(bio_id),
    )
    voting_data = normalize_votes(
        bio_id,
        run.classified_bills,
        rep_votes,
        rep.get("party", "I"),
        leader_spans=leader_spans,
        declared_caucus=rep.get("declaredCaucus"),
    )

    # Add recent votes
    rep_party = rep.get("party", "I")
    effective_party = voting_data.get("effectiveParty", rep_party)
    for rv in recent_votes_list:
        entry = member_vote_entry(
            rv, rv["vote"], effective_party, leader_spans,
        )
        # House recent roll calls are keyed by billId.
        entry["rcKey"] = rv.get("billId", "")
        # The measure the roll call was on ("H R 1492"
        # -> "HR.1492"): billId here is synthetic, and
        # the LDA bill links match on the measure.
        entry["measureId"] = bill_id_from_number(
            (run.recent_rc_map.get(rv.get("billId", "")) or {}).get("documentName"),
        )
        voting_data["recentVotes"].append(entry)

    rep["votingRecord"] = voting_data

    # FEC data
    district_str = str(rep_district).zfill(2) if rep_district else None
    fec_candidate = await find_candidate(
        client, run.db, rep_name, rep_state,
        office="H", district=district_str, bioguide_id=bio_id,
    )

    if fec_candidate:
        cand_id = fec_candidate.get("candidate_id", "")
        financials = await fetch_candidate_financials(client, run.db, cand_id)
        committees = await fetch_candidate_committees(client, run.db, cand_id)
        if seated_at_convening(rep.get("swornDate"), run.congress):
            financials = await with_seat_election(
                client, run.db, financials, committees, congress_first_year(run.congress) - 1,
            )

        recent_cycles = compute_recent_election_cycles(financials, "H")

        raw_receipts = []
        raw_pac_receipts = []

        for comm in committees:
            comm_id = comm.get("committee_id", "")
            if comm_id:
                raw_receipts.extend(await fetch_committee_receipts(client, run.db, comm_id, cycles=recent_cycles))
                raw_pac_receipts.extend(await fetch_pac_receipts(client, run.db, comm_id, cycles=recent_cycles))

        # Resolve PAC committee type, designation and
        # connected organization: the tier-1
        # political-committee rule and lobbying
        # client name in normalize_finance. The FEC's bulk
        # committee master (loaded once, on the first member
        # with receipts) answers almost every PAC; the
        # per-committee API covers only what it lacks.
        if run.committee_master is None:
            run.committee_master = await fetch_committee_master(
                client, run.db, committee_master_cycles(),
            )
            # Every committee-to-candidate contribution, from
            # the same bulk downloads, once per run.
            run.committee_contributions = await fetch_committee_contributions(
                client, run.db, committee_master_cycles(),
            )
        pac_committee_ids = {
            cid for r in raw_pac_receipts if (cid := committee_id_of(r))
        }
        # A giver whose registration couldn't be read raises
        # (FecUnavailable): this member keeps the stored
        # record, as for any unreadable FEC read.
        committee_meta_map = await resolve_committee_meta(
            client, run.db, pac_committee_ids, run.committee_master,
        )
        detail = await fetch_contribution_detail(
            client, run.db, cand_id,
            [c["committee_id"] for c in committees if c.get("committee_id")],
            recent_cycles, run.committee_contributions, run.committee_master,
        )

        # The chamber bounds the election window
        # (seat_winning_floor); a crosswalk match carries
        # only the id.
        finance_data = normalize_finance(
            {**fec_candidate, "office": "H"}, financials, raw_receipts, raw_pac_receipts,
            db_session=run.db,
            committee_meta_map=committee_meta_map,
            detail=detail,
        )
        rep["funding"] = finance_data
    else:
        rep["funding"] = {
            "totalRaised": 0,
            "totalFromPACs": 0,
            "smallDonorPercentage": 0,
            "topDonors": [],
            "industryBreakdown": [],
        }

    # Sponsored bills are already populated in Phase 4b.
    # If not (no bioguideId, or Phase 4b failed before this
    # member), nothing is known about them — not zero bills.
    if "sponsoredBills" not in rep:
        rep["sponsoredBills"] = []
        rep["sponsoredBillsUnavailable"] = True

    vr = rep.get("votingRecord") or {}
    all_votes = (vr.get("keyVotes") or []) + (vr.get("recentVotes") or [])
    # Campaign-promise tracking was removed entirely — see
    # policy_alignment.py's module docstring.
    rep["campaignPromises"] = []

    # Donor-industry-vs-vote connections for the scorecard
    # (embeddings only, zero LLM — see
    # cross_reference.detect_lobbying_matches). Display only:
    # no score reads them.
    lobbying_matches = detect_lobbying_matches(
        rep["funding"].get("topDonors", []),
        all_votes,
        rep["funding"].get("industryBreakdown", []),
    )
    lda_stats = await enrich_lobbying_matches_with_lda(
        lobbying_matches, run.db, utcnow().year - 1,
        votes=all_votes,
    )
    for k, v in lda_stats.items():
        lda_totals[k] = lda_totals.get(k, 0) + v
    rep["lobbyingMatches"] = lobbying_matches
    run.prepared_reps.append((rep, bio_id))


async def _prepare_reps(run: _HouseRun, client: httpx.AsyncClient) -> None:
    """Phase 5, first pass: each member's votes on the key bills and recent roll calls, their FEC funding and their donor-vote matches, then their party-line record over the Congress. A member whose read fails keeps the last scorecard (member_failures). Sets les_reference, prepared_reps, success_count, fail_count and member_failures."""
    logger.info("--- House Phase 5: FEC DATA + SCORING ---")
    run.progress.begin("fec_scoring", total=len(run.reps))

    # Clear embeddings cached by a prior run (senate or house) so
    # memory stays bounded; within this run the cache is shared
    # across all reps.
    clear_alignment_cache()

    run.success_count = 0
    run.fail_count = 0
    # (member id, exception) for each rep that failed: one ops
    # alert after the scoring pass (alert_member_failures).
    run.member_failures = []

    recent_only = recent_not_covered_by_key_bills(run.classified_recent, run.house_roll_calls)

    # This run's Legislative Effectiveness population reference, from
    # every rep's stage-classified sponsored bills (phase 4b) — before
    # anyone is scored. See live_references.live_les_reference.
    run.les_reference = live_les_reference(
        "house",
        [(r.get("sponsoredBills") or [], r.get("party"), bills_source(r)) for r in run.reps],
        run.db,
    )

    run.prepared_reps = []
    lda_totals: dict[str, int] = {}
    for idx, rep in enumerate(run.reps):
        try:
            await _prepare_one(run, client, idx, rep, recent_only, lda_totals)
        except Exception as e:
            # Same rollback rationale as the scoring pass below.
            run.db.rollback()
            logger.error("Failed to prepare rep %s: %s", rep.get("name", "?"), e)
            run.fail_count += 1
            run.member_failures.append((rep.get("id") or rep.get("bioguideId") or rep.get("name", "?"), e))

    alert_if_lda_down(lda_totals, "house")

    # Each rep's party-line record over the whole Congress (v6.20),
    # before the reference is measured on it.
    for (rep, _), record in zip(run.prepared_reps, party_line_records(run.db, "house", [r for r, _ in run.prepared_reps])):
        rep["votingRecord"]["partyLineRecord"] = record


def _score_one(run: _HouseRun, rep: dict, bio_id: str, funding_reference: dict) -> None:
    """One member's sponsorship figures, scores, their arithmetic and partisan depth, saved. Raises on a failure; _score_reps records the member as failed."""
    # Set leadership/ideology from sponsorship analysis
    l_score = run.leadership_scores.get(bio_id)
    i_score = run.ideology_scores.get(bio_id)
    b_score = run.bipartisanship_scores.get(bio_id)
    ab_score = run.attracted_bipartisanship_scores.get(bio_id)
    rep["leadershipScore"] = round(l_score, 4) if l_score is not None else None
    rep["bipartisanshipScore"] = round(b_score, 4) if b_score is not None else None
    rep["attractedBipartisanshipScore"] = round(ab_score, 4) if ab_score is not None else None
    rep["ideologyScore"] = round(i_score, 4) if i_score is not None else None
    if l_score is not None and i_score is not None:
        rep["sponsorshipDescription"] = describe_senator_position(
            i_score, l_score, rep.get("party", "I"),
            years_in_office=rep.get("yearsInOffice"),
            ideology_bounds=run.ideology_bounds_by_party.get(rep.get("party", "I")),
        )

    # Calculate scores
    scored = {
        **rep, "lesReference": run.les_reference, "fundingReference": funding_reference,
        "constituentReference": run.constituent_reference,
    }
    scores = calculate_scores(scored)
    # The same scores' arithmetic, kept with them: what the
    # breakdown serves (services/_scorecard_common).
    scores["breakdown"] = explain_scores(scored)
    scores["confidence"] = calculate_confidence(
        {**rep, "constituentReference": run.constituent_reference},
    )
    rep["representationScore"] = scores

    # Partisan depth from the voting record and the
    # cosponsorship ideology prior, as for senators
    # (campaign promises no longer exist); relabelled
    # against the whole chamber once the run finishes.
    rep["partisanDepth"] = analyze_partisan_depth(
        [], rep.get("party", ""),
        voting_record=rep.get("votingRecord") or {},
        ideology_score=i_score,
    )

    # Set bioguideId for persistence
    rep["bioguideId"] = bio_id

    # Persist
    upsert_representative(run.db, rep)
    run.success_count += 1


def _score_reps(run: _HouseRun) -> None:
    """Phase 5, second pass: the chamber's funding and Constituent Alignment references measured on the members prepared, then each member scored and saved. Then the alerts naming members that failed and records that went unread."""
    # Scoring is a second pass so each chamber-relative reference is
    # measured from the whole population BEFORE anyone is scored
    # (the Senate pipeline already works this way). The PAC-share
    # median needs every rep's funding, which the pass above fetches.
    # Each measured on the members prepared, out of the whole House:
    # under the coverage floor the last full-coverage one is carried.
    funding_reference = live_funding_reference(
        "house", [r.get("funding") or {} for r, _ in run.prepared_reps],
        parties=[r.get("party", "") for r, _ in run.prepared_reps], roster=len(run.reps),
    )
    run.constituent_reference, run.constituent_reference_measured = live_constituent_reference_measured(
        "house", [r for r, _ in run.prepared_reps], roster=len(run.reps),
    )

    for rep, bio_id in run.prepared_reps:
        try:
            _score_one(run, rep, bio_id, funding_reference)

        except Exception as e:
            # Roll back first, exactly as the Senate loop does
            # (senate_pipeline.py). upsert_representative commits
            # internally, so a DB-level failure (IntegrityError, or
            # "database is locked" under concurrent writes) leaves the
            # session in a failed state; without this rollback the next
            # rep's first query raises PendingRollbackError and every
            # remaining rep in the batch fails too — one bad rep would
            # silently wipe out the rest of the run.
            run.db.rollback()
            logger.error("Failed to process rep %s: %s", rep.get("name", "?"), e)
            run.fail_count += 1
            run.member_failures.append((rep.get("id") or rep.get("bioguideId") or rep.get("name", "?"), e))

    run.progress.complete("fec_scoring", detail=f"{run.success_count} OK, {run.fail_count} failed")
    alert_member_failures("house", run.member_failures)
    record_member_holds(run.db, Representative, "representative", run.member_failures)
    alert_unread_sponsorship(
        "house", run.unread_bills, run.carried_members,
        [r.get("bioguideId") or r.get("name", "?") for r in run.reps if r.get("sponsoredBillsUnavailable")],
    )


def _snapshots_and_checks(run: _HouseRun) -> None:
    """Phase 6: a snapshot of each score this run saved, the calibration, ground-truth and signal-overlap checks, and partisan depth relabelled against the whole chamber."""
    logger.info("--- House Phase 6: SNAPSHOTS ---")
    run.progress.begin("snapshots")
    _record_rep_snapshots(run.db, {member_id for member_id, _ in run.member_failures})

    run_calibration_check("representative")

    try:
        # Same derived consistency + distribution gate as
        # senate_pipeline.py. The House ran distribution-only while
        # the gate was a hand-named Senate reference table; the
        # derived checks are chamber-agnostic, so both run here now.
        gt_failures = check_ground_truth(
            run.db, model=Representative, constituent_reference=run.constituent_reference,
            reference_measured=run.constituent_reference_measured,
        ).get("failures", [])
        gt_failures += check_score_distribution(run.db, model=Representative)
        lines = "\n".join(
            f"- {f.get('senator', '?')} {f.get('dimension', '?')}="
            f"{f.get('score', '?')} expected {f.get('expected', '?')}"
            for f in gt_failures
        )
        persist_ground_truth_failures(
            run.db, run.house_run, gt_failures,
            alert_title=f"House ground-truth gate failed ({len(gt_failures)})",
            alert_body=(
                f"House derived score-consistency checks failed "
                f"(run #{run.house_run.id}):\n{lines}"
            ),
            dedupe_key=f"house-ground-truth-run-{run.house_run.id}",
            condition="ground-truth-house",
        )
    except Exception:
        logger.exception("House ground truth check failed (non-fatal)")

    # Same component-overlap check as senate_pipeline.py.

    record_signal_overlap(run.db, "house")

    run.progress.complete("snapshots")


    finalize_stored_partisan_depth(run.db, Representative)


async def run_house_pipeline() -> dict:
    """Run the full House representative pipeline."""
    db = SessionLocal()
    _run_token = None  # no run of ours for the finally to stop until start() below

    # Acquire the run lock BEFORE any global/DB mutation — same reasoning
    # as senate_pipeline.py's _acquire_pipeline_lock call. Without it, a row
    # orphaned by a killed process (a deploy restarting the container
    # mid-run) stays "running" forever, blocking every future House run.
    house_run, _run_token, refused = acquire_tracked_run(db, HousePipelineRun, STALE_PIPELINE_TIMEOUT, _tracker)
    if house_run is None:
        logger.warning("House pipeline not started: %s", skip_reason_text(refused))
        db.close()
        return {"status": "skipped", "reason": refused}

    start_time = time.time()

    progress = ProgressTracker(house_run, HOUSE_PIPELINE_STEPS, db, start_time)

    try:
        run = _HouseRun(db, progress, house_run, start_time)
        logger.info("=== HOUSE PIPELINE START ===")

        invalidate_stale_analysis(db)

        # Party positions from seeds plus the bills earlier runs labelled,
        # as the Senate run does. Without this the House classified against
        # whatever this process last cached: the Senate's centroids in the
        # nightly chain, the seeds alone for a House trigger after a restart.
        clear_platform_cache()
        initialize_platform_embeddings(db)

        async with make_async_client() as client:
            if not await _fetch_members(run, client):
                logger.warning("No House members found — aborting")
                elapsed = round(time.time() - start_time, 1)
                house_run.status = PipelineStatus.FAILED
                house_run.completed_at = utcnow()
                house_run.error_message = "No House members returned from Congress API"
                house_run.elapsed_seconds = elapsed
                progress.fail("fetch_members", detail="no members returned")
                db.commit()
                return {"status": "no_data", "elapsed_seconds": elapsed}

            await _normalize(run, client)

            await _fetch_bills_and_votes(run, client)

            await _classify(run, client)

            await _sponsorship(run, client)

            await _prepare_reps(run, client)

            _score_reps(run)

            _snapshots_and_checks(run)

            elapsed = time.time() - start_time
            logger.info("=== HOUSE PIPELINE COMPLETE ===")
            logger.info("Representatives: %d success, %d failed", run.success_count, run.fail_count)
            logger.info("Time: %.1fs", elapsed)

            status = (
                PipelineStatus.COMPLETED if run.fail_count == 0
                else PipelineStatus.PARTIAL if run.success_count > 0
                else PipelineStatus.FAILED
            )
            house_run.status = status
            house_run.completed_at = utcnow()
            house_run.reps_processed = run.success_count
            house_run.reps_total = run.success_count + run.fail_count
            house_run.reps_failed = run.fail_count
            house_run.elapsed_seconds = round(elapsed, 1)
            if run.fail_count > 0:
                house_run.error_message = f"{run.fail_count} of {run.success_count + run.fail_count} reps failed — check logs"
            db.commit()

            return {
                "status": status,
                "reps_processed": run.success_count,
                "reps_failed": run.fail_count,
                "elapsed_seconds": round(elapsed, 1),
            }

    except Exception as e:
        logger.exception("House pipeline failed: %s", e)
        cause = "House pipeline failed"
        try:
            # Roll back the poisoned session before writing the FAILED status
            # (mirrors senate_pipeline.py). Without it, if the failure came
            # from a DB error the session is invalid, this db.commit() also
            # raises, the status is never persisted, and the run row is
            # wedged in RUNNING forever — which makes the stock pipeline's
            # "is House still running?" guard skip every future stock run
            # until an admin clears it by hand.
            db.rollback()
            house_run.status = PipelineStatus.FAILED
            house_run.completed_at = utcnow()
            house_run.elapsed_seconds = round(time.time() - start_time, 1)
            house_run.error_message = "House pipeline failed — see the run's error detail"
            cause = record_exception(house_run, e, progress.active_step())
            db.commit()
        except Exception:
            logger.exception("Failed to record house pipeline failure")
        return {"status": PipelineStatus.FAILED, "error": cause}
    finally:
        _tracker.stop(_run_token)
        db.close()


def _record_rep_snapshots(db: Session, failed: "set[str] | frozenset[str]" = frozenset()) -> None:
    """Snapshot today's scores for the representatives this run scored:
    serving members, except `failed` (see
    senate_pipeline._record_score_snapshots)."""

    today = utcnow().date().isoformat()
    reps = [
        r for r in db.query(Representative).filter(Representative.is_current.isnot(False)).all()
        if r.id not in failed
    ]
    count = 0
    for r in reps:
        overall = compute_overall_score(r)
        existing = (
            db.query(ScoreSnapshot)
            .filter(
                ScoreSnapshot.entity_type == "representative",
                ScoreSnapshot.entity_id == r.id,
                ScoreSnapshot.date == today,
            )
            .first()
        )
        if existing:
            existing.overall_score = overall
            existing.score_1 = r.score_funding_independence
            existing.score_2 = r.score_promise_persistence
            existing.score_3 = r.score_constituent_alignment
            existing.score_4 = r.score_funding_diversity
            existing.score_5 = r.score_legislative_effectiveness
            # Same-day re-run after a code deploy: keep the version label
            # in sync with the scores it describes (the Senate recorder
            # delete-and-recreates, which refreshes it implicitly).
            existing.algorithm_version = scoring_version()
        else:
            db.add(ScoreSnapshot(
                entity_type="representative",
                entity_id=r.id,
                date=today,
                overall_score=overall,
                score_1=r.score_funding_independence,
                score_2=r.score_promise_persistence,
                score_3=r.score_constituent_alignment,
                score_4=r.score_funding_diversity,
                score_5=r.score_legislative_effectiveness,
                algorithm_version=scoring_version(),
            ))
            count += 1
    db.commit()
    logger.info("Recorded %d representative score snapshots", count)
