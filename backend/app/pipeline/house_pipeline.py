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
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.http_client import make_async_client
from app.models import HousePipelineRun, PipelineStatus, Representative, ScoreSnapshot
from app.pipeline.analyze.bill_stage import is_enacted
from app.pipeline.analyze.cross_reference import detect_lobbying_matches
from app.pipeline.analyze.party_line_record import party_line_records
from app.pipeline.analyze.policy_alignment import clear_alignment_cache
from app.pipeline.analyze.score_calculator import (
    ALGORITHM_VERSION,
    calculate_confidence,
    calculate_scores,
    compute_overall_score,
)
from app.pipeline.member_lifecycle import (
    CHAMBER_HOUSE,
    purge_departed_members,
    reconcile_roster,
)
from app.pipeline.progress_tracker import ProgressTracker
from app.pipeline.run_tracker import PipelineRunTracker, STALE_PIPELINE_TIMEOUT, acquire_tracked_run, skip_reason_text
from app.services.representative_service import upsert_representative

from app.pipeline.fetch.congress import (
    congress_first_year,
    extract_official_title,
    fetch_bill,
    fetch_bill_actions,
    fetch_bill_cosponsors,
    fetch_bill_summaries,
    fetch_bill_titles,
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
from app.pipeline.analyze.bill_learning import stamp_motion_type
from app.pipeline.fetch.lda import alert_if_lda_down, enrich_lobbying_matches_with_lda
from app.pipeline.run_checks import persist_ground_truth_failures, run_calibration_check
from app.pipeline.transform.normalize_finance import normalize_finance
from app.pipeline.transform.normalize_members import normalize_house_members
from app.pipeline.transform.committee_data import load_leadership_tenures
from app.pipeline.transform.normalize_votes import (
    extract_representative_vote,
    find_house_roll_call,
    majority_leader_spans,
    normalize_votes,
    member_vote_entry,
    stamp_roll_call_outcome,
    compute_party_split,
    compute_party_vote_split,
    house_roll_call_id,
    is_housekeeping,
)
from app.time_utils import utcnow
from app.pipeline.senate_pipeline import invalidate_stale_analysis
from app.pipeline.fetch.house_clerk import fetch_house_sworn_dates
from app.pipeline.analyze.bill_analyzer import classify_all_bills, classify_policy_areas_multi
from app.pipeline.analyze.party_platform import (
    analyze_partisan_depth,
    classify_party_alignment_multi,
    clear_platform_cache,
    initialize_platform_embeddings,
    refine_with_vote_data,
)
from app.pipeline.analyze.sponsorship_analysis import (
    compute_bipartisanship_scores,
    compute_ideology_scores,
    compute_leadership_scores,
    describe_senator_position,
    party_ideology_bounds,
)
from app.pipeline.analyze.bill_stage import classify_bill_stage_from_actions
from app.pipeline.fetch.voteview import refresh_member_ideal_points
from app.pipeline.analyze.commemorative import mark_commemorative
from app.pipeline.sponsorship_backfill import backfill_withheld_sponsorship_scores
from app.pipeline.live_references import live_constituent_reference_measured, live_funding_reference, live_les_reference
from app.pipeline.analyze.ground_truth import check_ground_truth, check_score_distribution
from app.pipeline.analyze.signal_overlap import record_signal_overlap
from app.pipeline.partisan_depth_store import finalize_stored_partisan_depth

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


def is_house_pipeline_running() -> bool:
    return _tracker.is_running


def house_pipeline_age() -> "timedelta | None":
    """Wall-clock age of the in-process House run, or None when idle.

    Lets callers distinguish a live run from a hung one — a run wedged
    silently mid-phase would otherwise hold the running flag indefinitely
    and block every hourly action-center refresh behind it.
    """
    return _tracker.age


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
        logger.info("=== HOUSE PIPELINE START ===")

        invalidate_stale_analysis(db)

        # Party positions from seeds plus the bills earlier runs labelled,
        # as the Senate run does. Without this the House classified against
        # whatever this process last cached: the Senate's centroids in the
        # nightly chain, the seeds alone for a House trigger after a restart.
        clear_platform_cache()
        initialize_platform_embeddings(db)

        async with make_async_client() as client:
            # FEC committee master, loaded on first use (see the FEC step).
            committee_master: dict[str, dict] | None = None
            committee_contributions: dict | None = None

            # ── PHASE 1: FETCH MEMBERS ──
            logger.info("--- House Phase 1: FETCH MEMBERS ---")
            progress.begin("fetch_members")
            raw_members = await fetch_representatives(client, db)
            logger.info("Fetched %d raw House members", len(raw_members))

            if not raw_members:
                logger.warning("No House members found — aborting")
                elapsed = round(time.time() - start_time, 1)
                house_run.status = PipelineStatus.FAILED
                house_run.completed_at = utcnow()
                house_run.error_message = "No House members returned from Congress API"
                house_run.elapsed_seconds = elapsed
                progress.fail("fetch_members", detail="no members returned")
                db.commit()
                return {"status": "no_data", "elapsed_seconds": elapsed}

            member_details: dict[str, dict] = {}
            for i, m in enumerate(raw_members):
                bio_id = m.get("bioguideId", "")
                if bio_id:
                    detail = await fetch_member_detail(client, db, bio_id)
                    if detail:
                        member_details[bio_id] = detail
                if (i + 1) % 50 == 0:
                    logger.info("Member details: %d/%d", i + 1, len(raw_members))

            logger.info("Fetched details for %d members", len(member_details))
            progress.complete("fetch_members", detail=f"{len(raw_members)} found, {len(member_details)} detailed")

            # ── PHASE 2: NORMALIZE ──
            logger.info("--- House Phase 2: NORMALIZE ---")
            progress.begin("normalize")
            reps = normalize_house_members(raw_members, member_details)
            logger.info("Normalized %d representatives", len(reps))
            progress.complete("normalize", detail=f"{len(reps)} representatives")

            # Reconcile the roster: anyone in the database but not in
            # tonight's fetch has left office, and anyone who left long
            # enough ago gets removed. See member_lifecycle.py — the House
            # is where this matters most, since special elections leave
            # seats open for months at a time.
            reconcile_roster(db, CHAMBER_HOUSE, {m.get("bioguideId", "") for m in raw_members})
            purge_departed_members(db, CHAMBER_HOUSE)
            db.commit()

            # When each member took the seat, for Legislative Effectiveness's
            # mid-Congress proration (v6.23). The Clerk's list when it can be
            # read; otherwise the dates stored last, so one failed request
            # doesn't score a special-election arrival against a full term.
            sworn_dates = await fetch_house_sworn_dates(client, db)
            if sworn_dates:
                for r in reps:
                    r["swornDate"] = sworn_dates.get(r.get("bioguideId", ""))
            else:
                stored = dict(
                    db.query(Representative.bioguide_id, Representative.sworn_date)
                    .filter(Representative.sworn_date.isnot(None))
                    .all()
                )
                for r in reps:
                    if r.get("bioguideId") in stored:
                        r["swornDate"] = stored[r["bioguideId"]]

            # Build bioguide -> rep mapping
            bio_to_rep: dict[str, dict] = {}
            for r in reps:
                bio_id = r.get("bioguideId", "")
                if bio_id:
                    bio_to_rep[bio_id] = r

            # ── PHASE 3: FETCH BILLS & VOTES ──
            logger.info("--- House Phase 3: FETCH BILLS & VOTES ---")
            progress.begin("fetch_bills_votes")

            bills_data = await fetch_significant_bills(client, db, max_bills=40)
            logger.info("Discovered %d significant bills", len(bills_data))

            # Fetch bill details and actions for House vote discovery
            bill_details_map: dict[str, dict] = {}
            bill_actions_map: dict[str, list] = {}

            for b in bills_data:
                bill_key = f"{b['type'].upper()}.{b['number']}"
                detail = await fetch_bill(client, db, b["congress"], b["type"], b["number"])
                if detail:
                    bill_details_map[bill_key] = detail
                actions = await fetch_bill_actions(client, db, b["congress"], b["type"], b["number"])
                if actions:
                    bill_actions_map[bill_key] = actions

            # Discover House roll calls from bill actions
            house_roll_calls: dict[str, dict] = {}
            for bill_key, actions in bill_actions_map.items():
                rc_info = find_house_roll_call(actions)
                if rc_info:
                    rc_data = await fetch_house_roll_call_vote(
                        client, db,
                        rc_info["year"],
                        rc_info["rollCallNumber"],
                    )
                    if rc_data:
                        house_roll_calls[bill_key] = rc_data

            logger.info("Found %d House roll calls from bill actions", len(house_roll_calls))

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
            scored = settings.CURRENT_CONGRESS
            current_year, year_is_young = roll_call_year(scored, utcnow())
            recent_rcs = await fetch_recent_house_roll_calls(client, db, year=current_year, count=120)
            same_congress_prior_year = current_year > congress_first_year(scored)
            if recent_rcs is not None and len(recent_rcs) < 60 and year_is_young and same_congress_prior_year:
                earlier = await fetch_recent_house_roll_calls(
                    client, db, year=current_year - 1, count=120 - len(recent_rcs),
                )
                recent_rcs = None if earlier is None else recent_rcs + earlier
            if recent_rcs is None:
                # The Clerk could not be read. Saving now would replace
                # every member's stored votes with none, so the run fails
                # here, before any member is written.
                raise RuntimeError("House roll calls could not be read from clerk.house.gov")
            recent_rcs = in_congress(recent_rcs, scored)
            logger.info("Fetched %d recent House roll calls", len(recent_rcs))

            # Map recent roll calls by a synthetic billId
            recent_rc_map: dict[str, dict] = {}
            for rc in recent_rcs:
                bill_id = house_roll_call_id(rc)
                recent_rc_map[bill_id] = rc

            progress.complete(
                "fetch_bills_votes",
                detail=f"{len(bills_data)} bills, {len(house_roll_calls)} roll calls, {len(recent_rcs)} recent",
            )

            # ── PHASE 4: CLASSIFY BILLS ──
            logger.info("--- House Phase 4: CLASSIFY BILLS ---")
            progress.begin("classify_bills")


            bills_for_classification = []
            for b in bills_data:
                bill_key = f"{b['type'].upper()}.{b['number']}"
                detail = bill_details_map.get(bill_key, {})
                summaries = await fetch_bill_summaries(client, db, b["congress"], b["type"], b["number"])
                summary_text = ""
                if summaries:
                    summary_text = summaries[0].get("text", "")

                titles = await fetch_bill_titles(client, db, b["congress"], b["type"], b["number"])
                official_title = extract_official_title(titles or [])

                bills_for_classification.append({
                    "billId": bill_key,
                    "billName": b["name"],
                    "officialTitle": official_title,
                    "congress": b["congress"],
                    "type": b["type"],
                    "summary": summary_text,
                    "actions": bill_actions_map.get(bill_key, []),
                })

            classified_bills = await classify_all_bills(bills_for_classification, db)
            logger.info("Classified %d bills", len(classified_bills))

            # Also classify recent roll calls
            recent_for_classification = []
            for bill_id, rc in recent_rc_map.items():
                recent_for_classification.append({
                    "billId": bill_id,
                    "billName": rc.get("documentTitle") or rc.get("voteTitle", ""),
                    "congress": rc.get("congress", settings.CURRENT_CONGRESS),
                    "summary": "",
                    "actions": [],
                })

            classified_recent = await classify_all_bills(recent_for_classification, db)

            logger.info("Classified %d recent House votes", len(classified_recent))

            # Refine LLM party leanings with actual roll-call splits.
            # The real member-vote split is authoritative for party-loyalty
            # measurement (see refine_with_vote_data) — previously it was
            # only used when the LLM produced no label, so bipartisan-passed
            # bills kept partisan content labels and half the chamber was
            # marked as voting "against party" on near-unanimous bills.

            for bill in classified_bills:
                bill_id = bill.get("billId", "")
                rc = house_roll_calls.get(bill_id)
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

            for bill in classified_recent:
                bill_id = bill.get("billId", "")
                rc = recent_rc_map.get(bill_id)
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

            progress.complete(
                "classify_bills",
                detail=f"{len(classified_bills)} bills, {len(classified_recent)} recent votes",
            )

            # ── PHASE 4b: SPONSORSHIP ANALYSIS (PageRank + SVD) ──
            logger.info("--- House Phase 4b: SPONSORSHIP ANALYSIS ---")
            progress.begin("sponsorship")
            # Defined up front so the "continuing with empty scores" failure
            # path below leaves it a safe empty map (label falls back to the
            # fixed global cutoffs).
            ideology_bounds_by_party: dict[str, tuple[float, float]] = {}


            cosponsors_map: dict[str, list[dict]] = {}
            all_bills_for_analysis: list[dict] = []
            leadership_scores: dict[str, float] = {}
            ideology_scores: dict[str, float] = {}
            bipartisanship_scores: dict[str, float] = {}
            attracted_bipartisanship_scores: dict[str, float] = {}


            try:
                # Fetch cosponsors for significant bills to build the
                # rep-rep cosponsorship graph. This reuses bills already
                # fetched in Phase 3, keeping API calls manageable (~40).
                for b in bills_data:
                    bill_key = f"{b['type'].upper()}.{b['number']}"
                    cosponsors = await fetch_bill_cosponsors(
                        client, db, b["congress"], b["type"], b["number"],
                    )
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
                for r in reps:
                    bio_id = r.get("bioguideId", "")
                    party = r.get("party", "")
                    if not bio_id:
                        continue
                    sponsored = await fetch_member_sponsored(client, db, bio_id)
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
                            bill_actions = await fetch_bill_actions(
                                client, db, sp_congress, sp_type.lower(), int(sp_num),
                            )
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
                            if sp_title and len(sp_title) > 10:
                                policy_areas_raw = classify_policy_areas_multi(sp_title, db_session=db)
                                if policy_areas_raw:
                                    alignment = classify_party_alignment_multi(
                                        sp_title, policy_areas_raw, "pro",
                                    )
                                    party_leaning = alignment.get("overall", "bipartisan")
                                    rc = house_roll_calls.get(sp_key)
                                    if rc and not is_housekeeping(rc.get("question"), rc.get("chamber")):
                                        split = compute_party_split(rc)
                                        party_leaning = refine_with_vote_data(party_leaning, split)
                            sp_list.append({
                                "billId": sp_key,
                                "title": sp_title,
                                "introducedDate": sp.get("introducedDate", ""),
                                "latestAction": latest_action_text,
                                "latestActionDate": (sp.get("latestAction") or {}).get("actionDate", ""),
                                "policyArea": policy_areas_raw[0]["area"] if policy_areas_raw else "",
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
                                "stage": classify_bill_stage_from_actions(bill_actions, is_law, sp.get("type")),
                            })
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
                    for sp_data in sp_list[:5]:
                        sp_bill_id = sp_data["billId"]
                        if sp_bill_id in cosponsors_map:
                            continue
                        parts = sp_bill_id.split(".")
                        if len(parts) == 2 and parts[1].isdigit():
                            cosponsors = await fetch_bill_cosponsors(
                                client, db,
                                sp_data.get("congress", settings.CURRENT_CONGRESS),
                                parts[0].lower(),
                                int(parts[1]),
                            )
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

                all_bills_for_analysis.extend(sponsored_for_cosponsor)
                total_cosponsors = sum(len(v) for v in cosponsors_map.values())
                logger.info(
                    "Cosponsorship data: %d bills with cosponsors (%d total cosponsorships)",
                    len(cosponsors_map), total_cosponsors,
                )

                rep_bio_ids = {
                    r.get("bioguideId", "")
                    for r in reps
                    if r.get("bioguideId")
                }
                rep_party_map = {
                    r.get("bioguideId", ""): r.get("party", "")
                    for r in reps
                    if r.get("bioguideId")
                }
                leadership_scores = compute_leadership_scores(
                    all_bills_for_analysis, cosponsors_map, rep_bio_ids, rep_party_map,
                )
                bipartisanship_scores = compute_bipartisanship_scores(
                    all_bills_for_analysis, cosponsors_map, rep_party_map,
                )
                # Receive-only variant for Legislative Effectiveness's
                # coalition-attraction component (score_calculator v6.11) —
                # see senate_pipeline's identical call for the rationale.
                attracted_bipartisanship_scores = compute_bipartisanship_scores(
                    all_bills_for_analysis, cosponsors_map, rep_party_map,
                    direction="receive",
                )
                ideology_scores = compute_ideology_scores(
                    all_bills_for_analysis, cosponsors_map, rep_bio_ids, rep_party_map,
                )
                # Party-relative ideology label thresholds over the full
                # cohort (see party_ideology_bounds) so progressive/moderate/
                # centrist reflects position WITHIN a party, not party identity.
                ideology_bounds_by_party = party_ideology_bounds(
                    [(ideology_scores.get(bio), rep_party_map.get(bio)) for bio in rep_bio_ids]
                )
                # Refresh this chamber's roll-call ideal points from
                # Voteview (position-congruence component, score_calculator
                # v6.11). Best-effort: never raises; a fetch/gate failure
                # keeps the last good /data/member_ideal_points.json section.
                await refresh_member_ideal_points("house", settings.CURRENT_CONGRESS)
                logger.info(
                    "Sponsorship analysis: %d leadership scores, %d ideology scores",
                    len(leadership_scores), len(ideology_scores),
                )
                progress.complete(
                    "sponsorship",
                    detail=f"{len(leadership_scores)} leadership, {len(ideology_scores)} ideology",
                )
            except Exception as phase4b_err:
                logger.error(
                    "Phase 4b failed (%s) — continuing with empty sponsorship scores",
                    phase4b_err,
                )
                progress.fail("sponsorship", detail="failed — continuing with empty scores")

            # Commemorative bills (V&W's 1x tier) — before the LES reference
            # is measured, since its stage totals are significance-weighted.
            mark_commemorative([sp for r in reps for sp in r.get("sponsoredBills") or []])

            # A withheld or failed analysis leaves members out of these
            # dicts; score them with last run's values, as the Senate does.

            backfill_withheld_sponsorship_scores(
                db, Representative, {r["bioguideId"] for r in reps if r.get("bioguideId")},
                leadership_scores, ideology_scores,
                bipartisanship_scores, attracted_bipartisanship_scores,
            )

            # ── PHASE 5: FEC DATA + SCORING ──
            logger.info("--- House Phase 5: FEC DATA + SCORING ---")
            progress.begin("fec_scoring", total=len(reps))

            # Clear embeddings cached by a prior run (senate or house) so
            # memory stays bounded; within this run the cache is shared
            # across all reps.
            clear_alignment_cache()

            success_count = 0
            fail_count = 0

            recent_only = recent_not_covered_by_key_bills(classified_recent, house_roll_calls)

            # This run's Legislative Effectiveness population reference, from
            # every rep's stage-classified sponsored bills (phase 4b) — before
            # anyone is scored. See live_references.live_les_reference.
            les_reference = live_les_reference(
                "house",
                [(r.get("sponsoredBills") or [], r.get("party")) for r in reps],
                db,
            )

            prepared_reps: list[tuple[dict, str]] = []
            lda_totals: dict[str, int] = {}
            for idx, rep in enumerate(reps):
                try:
                    bio_id = rep.get("bioguideId", "")
                    rep_name = rep.get("name", "")
                    rep_state = rep.get("state", "")
                    rep_district = rep.get("district", 0)

                    if (idx + 1) % 25 == 0:
                        logger.info("Processing representative %d/%d: %s", idx + 1, len(reps), rep_name)
                        progress.update("fec_scoring", done=idx + 1)

                    # Extract votes from key bills
                    rep_votes: dict[str, str] = {}
                    for bill in classified_bills:
                        bill_id = bill.get("billId", "")
                        rc = house_roll_calls.get(bill_id)
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
                        rc = recent_rc_map.get(bill_id)
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
                        classified_bills,
                        rep_votes,
                        rep.get("party", "I"),
                        leader_spans=leader_spans,
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
                            (recent_rc_map.get(rv.get("billId", "")) or {}).get("documentName"),
                        )
                        voting_data["recentVotes"].append(entry)

                    rep["votingRecord"] = voting_data

                    # FEC data
                    district_str = str(rep_district).zfill(2) if rep_district else None
                    fec_candidate = await find_candidate(
                        client, db, rep_name, rep_state,
                        office="H", district=district_str, bioguide_id=bio_id,
                    )

                    if fec_candidate:
                        cand_id = fec_candidate.get("candidate_id", "")
                        financials = await fetch_candidate_financials(client, db, cand_id)
                        committees = await fetch_candidate_committees(client, db, cand_id)
                        if seated_at_convening(rep.get("swornDate"), scored):
                            financials = await with_seat_election(
                                client, db, financials, committees, congress_first_year(scored) - 1,
                            )

                        recent_cycles = compute_recent_election_cycles(financials, "H")

                        raw_receipts = []
                        raw_pac_receipts = []

                        for comm in committees:
                            comm_id = comm.get("committee_id", "")
                            if comm_id:
                                raw_receipts.extend(await fetch_committee_receipts(client, db, comm_id, cycles=recent_cycles))
                                raw_pac_receipts.extend(await fetch_pac_receipts(client, db, comm_id, cycles=recent_cycles))

                        # Resolve PAC committee type, designation and
                        # connected organization: the tier-1
                        # political-committee rule and lobbying
                        # client name in normalize_finance. The FEC's bulk
                        # committee master (loaded once, on the first member
                        # with receipts) answers almost every PAC; the
                        # per-committee API covers only what it lacks.
                        if committee_master is None:
                            committee_master = await fetch_committee_master(
                                client, db, committee_master_cycles(),
                            )
                            # Every committee-to-candidate contribution, from
                            # the same bulk downloads, once per run.
                            committee_contributions = await fetch_committee_contributions(
                                client, db, committee_master_cycles(),
                            )
                        pac_committee_ids = {
                            cid for r in raw_pac_receipts if (cid := committee_id_of(r))
                        }
                        committee_meta_map = await resolve_committee_meta(
                            client, db, pac_committee_ids, committee_master,
                        )
                        detail = await fetch_contribution_detail(
                            client, db, cand_id,
                            [c["committee_id"] for c in committees if c.get("committee_id")],
                            recent_cycles, committee_contributions, committee_master,
                        )

                        # The chamber bounds the election window
                        # (seat_winning_floor); a crosswalk match carries
                        # only the id.
                        finance_data = normalize_finance(
                            {**fec_candidate, "office": "H"}, financials, raw_receipts, raw_pac_receipts,
                            db_session=db,
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

                    # Detect donor-industry-vs-vote connections (embeddings
                    # only, zero LLM — see cross_reference.detect_lobbying_matches).
                    # Without this, Constituent Alignment's donor-independence
                    # component would default to a flat, fundraising-size-
                    # based score regardless of actual donor-vote behavior.
                    lobbying_matches = detect_lobbying_matches(
                        rep["funding"].get("topDonors", []),
                        all_votes,
                        rep["funding"].get("industryBreakdown", []),
                    )
                    lda_stats = await enrich_lobbying_matches_with_lda(
                        lobbying_matches, db, utcnow().year - 1,
                        votes=all_votes,
                    )
                    for k, v in lda_stats.items():
                        lda_totals[k] = lda_totals.get(k, 0) + v
                    rep["lobbyingMatches"] = lobbying_matches
                    prepared_reps.append((rep, bio_id))

                except Exception as e:
                    # Same rollback rationale as the scoring pass below.
                    db.rollback()
                    logger.error("Failed to prepare rep %s: %s", rep.get("name", "?"), e)
                    fail_count += 1

            alert_if_lda_down(lda_totals, "house")

            # Each rep's party-line record over the whole Congress (v6.20),
            # before the reference is measured on it.
            for (rep, _), record in zip(prepared_reps, party_line_records(db, "house", [r for r, _ in prepared_reps])):
                rep["votingRecord"]["partyLineRecord"] = record

            # Scoring is a second pass so each chamber-relative reference is
            # measured from the whole population BEFORE anyone is scored
            # (the Senate pipeline already works this way). The PAC-share
            # median needs every rep's funding, which the pass above fetches.
            funding_reference = live_funding_reference(
                "house", [r.get("funding") or {} for r, _ in prepared_reps],
                parties=[r.get("party", "") for r, _ in prepared_reps],
            )
            constituent_reference, constituent_reference_measured = live_constituent_reference_measured(
                "house", [r for r, _ in prepared_reps],
            )

            for rep, bio_id in prepared_reps:
                try:
                    # Set leadership/ideology from sponsorship analysis
                    l_score = leadership_scores.get(bio_id)
                    i_score = ideology_scores.get(bio_id)
                    b_score = bipartisanship_scores.get(bio_id)
                    ab_score = attracted_bipartisanship_scores.get(bio_id)
                    rep["leadershipScore"] = round(l_score, 4) if l_score is not None else None
                    rep["bipartisanshipScore"] = round(b_score, 4) if b_score is not None else None
                    rep["attractedBipartisanshipScore"] = round(ab_score, 4) if ab_score is not None else None
                    rep["ideologyScore"] = round(i_score, 4) if i_score is not None else None
                    if l_score is not None and i_score is not None:
                        rep["sponsorshipDescription"] = describe_senator_position(
                            i_score, l_score, rep.get("party", "I"),
                            years_in_office=rep.get("yearsInOffice"),
                            ideology_bounds=ideology_bounds_by_party.get(rep.get("party", "I")),
                        )

                    # Calculate scores
                    scores = calculate_scores({
                        **rep, "lesReference": les_reference, "fundingReference": funding_reference,
                        "constituentReference": constituent_reference,
                    })
                    scores["confidence"] = calculate_confidence(
                        {**rep, "constituentReference": constituent_reference},
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
                    upsert_representative(db, rep)
                    success_count += 1

                except Exception as e:
                    # Roll back first, exactly as the Senate loop does
                    # (senate_pipeline.py). upsert_representative commits
                    # internally, so a DB-level failure (IntegrityError, or
                    # "database is locked" under concurrent writes) leaves the
                    # session in a failed state; without this rollback the next
                    # rep's first query raises PendingRollbackError and every
                    # remaining rep in the batch fails too — one bad rep would
                    # silently wipe out the rest of the run.
                    db.rollback()
                    logger.error("Failed to process rep %s: %s", rep.get("name", "?"), e)
                    fail_count += 1

            progress.complete("fec_scoring", detail=f"{success_count} OK, {fail_count} failed")

            # ── PHASE 6: SNAPSHOTS ──
            logger.info("--- House Phase 6: SNAPSHOTS ---")
            progress.begin("snapshots")
            _record_rep_snapshots(db)

            run_calibration_check("representative")

            try:
                # Same derived consistency + distribution gate as
                # senate_pipeline.py. The House ran distribution-only while
                # the gate was a hand-named Senate reference table; the
                # derived checks are chamber-agnostic, so both run here now.
                gt_failures = check_ground_truth(
                    db, model=Representative, constituent_reference=constituent_reference,
                    reference_measured=constituent_reference_measured,
                ).get("failures", [])
                gt_failures += check_score_distribution(db, model=Representative)
                lines = "\n".join(
                    f"- {f.get('senator', '?')} {f.get('dimension', '?')}="
                    f"{f.get('score', '?')} expected {f.get('expected', '?')}"
                    for f in gt_failures
                )
                persist_ground_truth_failures(
                    db, house_run, gt_failures,
                    alert_title=f"House ground-truth gate failed ({len(gt_failures)})",
                    alert_body=(
                        f"House derived score-consistency checks failed "
                        f"(run #{house_run.id}):\n{lines}"
                    ),
                    dedupe_key=f"house-ground-truth-run-{house_run.id}",
                    condition="ground-truth-house",
                )
            except Exception:
                logger.exception("House ground truth check failed (non-fatal)")

            # Same component-overlap check as senate_pipeline.py.

            record_signal_overlap(db, "house")

            progress.complete("snapshots")


            finalize_stored_partisan_depth(db, Representative)

            elapsed = time.time() - start_time
            logger.info("=== HOUSE PIPELINE COMPLETE ===")
            logger.info("Representatives: %d success, %d failed", success_count, fail_count)
            logger.info("Time: %.1fs", elapsed)

            status = (
                PipelineStatus.COMPLETED if fail_count == 0
                else PipelineStatus.PARTIAL if success_count > 0
                else PipelineStatus.FAILED
            )
            house_run.status = status
            house_run.completed_at = utcnow()
            house_run.reps_processed = success_count
            house_run.reps_total = success_count + fail_count
            house_run.reps_failed = fail_count
            house_run.elapsed_seconds = round(elapsed, 1)
            if fail_count > 0:
                house_run.error_message = f"{fail_count} of {success_count + fail_count} reps failed — check logs"
            db.commit()

            return {
                "status": status,
                "reps_processed": success_count,
                "reps_failed": fail_count,
                "elapsed_seconds": round(elapsed, 1),
            }

    except Exception as e:
        logger.exception("House pipeline failed: %s", e)
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
            house_run.error_message = "House pipeline failed — see server logs"
            db.commit()
        except Exception:
            logger.exception("Failed to record house pipeline failure")
        return {"status": PipelineStatus.FAILED, "error": str(e)[:500]}
    finally:
        _tracker.stop(_run_token)
        db.close()


def _record_rep_snapshots(db: Session) -> None:
    """Snapshot today's scores for all representatives."""

    today = utcnow().date().isoformat()
    reps = db.query(Representative).all()
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
            existing.algorithm_version = ALGORITHM_VERSION
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
                algorithm_version=ALGORITHM_VERSION,
            ))
            count += 1
    db.commit()
    logger.info("Recorded %d representative score snapshots", count)
