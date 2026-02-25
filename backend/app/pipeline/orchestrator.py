"""
Pipeline orchestrator — unified data pipeline.

The 6 phases:
  1. FETCH      — congress, FEC, platforms, floor remarks
  2. TRANSFORM  — normalize members, votes, finance
  3. ANALYZE    — classify bills/donors, cross-reference, score
  4. EXPLORE    — ingest government documents for semantic search
  5. JUSTICES   — fetch and score Supreme Court justices
  6. FINALIZE   — persist stats and mark complete

Uses SQLAlchemy sessions for persistence and PipelineRun records to track progress.
"""

import json
import logging
import time
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import (
    CampaignPromise,
    Donor,
    IndustryDonation,
    KeyVote,
    LobbyingMatch,
    PipelineRun,
    Senator,
)

# Fetch modules
from app.pipeline.fetch.congress import (
    fetch_bill,
    fetch_bill_actions,
    fetch_bill_summaries,
    fetch_member_detail,
    fetch_recent_roll_calls,
    fetch_roll_call_vote,
    fetch_senator_platform_text,
    fetch_senators,
    fetch_significant_bills,
)
from app.pipeline.fetch.fec import (
    fetch_aggregated_contributors,
    fetch_candidate_committees,
    fetch_candidate_financials,
    fetch_committee_receipts,
    fetch_pac_receipts,
    find_candidate,
)
from app.pipeline.fetch.govinfo import fetch_bill_text
from app.pipeline.fetch.congressional_record import fetch_floor_remarks
# Transform modules
from app.pipeline.transform.normalize_finance import normalize_finance
from app.pipeline.transform.normalize_members import normalize_members
from app.pipeline.transform.normalize_votes import (
    compute_party_split,
    extract_senator_vote,
    find_senate_roll_call,
    normalize_recent_votes,
    normalize_votes,
)

# Analyze modules
from app.pipeline.analyze.bill_analyzer import classify_all_bills, classify_recent_votes, clear_bill_embedding_cache
from app.pipeline.analyze.bill_learning import clear_reference_cache
from app.pipeline.analyze.party_platform import clear_platform_cache, initialize_platform_embeddings
from app.pipeline.vector_store import (
    check_model_version,
    embed_bills,
    invalidate_on_model_change,
    _write_model_version,
)
from app.pipeline.analyze.cross_reference import analyze_senator_batch
from app.pipeline.analyze.donor_classifier_ai import classify_donors_hybrid
from app.pipeline.analyze.ollama_client import get_llm_stats, reset_client, reset_stats
from app.pipeline.analyze.policy_alignment import clear_alignment_cache
from app.pipeline.analyze.floor_speech_analyzer import analyze_floor_advocacy
from app.pipeline.analyze.score_calculator import calculate_scores

# Assemble modules
from app.pipeline.assemble.senator_builder import build_senator

logger = logging.getLogger(__name__)


PIPELINE_STEPS = [
    ("fetch_senators",       "fetch",     "Fetch senator list"),
    ("fetch_member_details", "fetch",     "Fetch member details"),
    ("normalize_members",    "transform", "Normalize members"),
    ("discover_bills",       "fetch",     "Discover significant bills"),
    ("fetch_bill_details",   "fetch",     "Fetch bill details"),
    ("fetch_roll_calls",     "fetch",     "Fetch roll call votes"),
    ("fetch_recent_rcs",     "fetch",     "Fetch recent roll calls"),
    ("fetch_fec",            "fetch",     "Fetch FEC financial data"),
    ("fetch_platforms",      "fetch",     "Fetch platform text"),
    ("fetch_floor_remarks",  "fetch",     "Fetch floor remarks"),
    ("classify_bills",       "analyze",   "Classify bills"),
    ("classify_recent",      "analyze",   "Classify recent votes"),
    ("embed_bills",          "analyze",   "Embed bills in vector DB"),
    ("classify_donors",      "analyze",   "Classify donors"),
    ("prepare_senators",     "analyze",   "Prepare senator data"),
    ("analyze_senators",     "analyze",   "Analyze senators (LLM)"),
    ("finalize",             "finalize",  "Finalize & save"),
]


class ProgressTracker:
    """Track sub-step progress within a pipeline run and persist to the DB."""

    def __init__(self, pipeline_run: PipelineRun, db: Session, start_time: float):
        self._run = pipeline_run
        self._db = db
        self._start_time = start_time
        self._steps: dict[str, dict] = {}
        for key, phase, label in PIPELINE_STEPS:
            self._steps[key] = {
                "key": key,
                "phase": phase,
                "label": label,
                "status": "pending",
            }
        self._flush()

    def begin(self, key: str, *, total: int | None = None) -> None:
        step = self._steps.get(key)
        if not step:
            return
        step["status"] = "active"
        step["startedAt"] = datetime.utcnow().isoformat()
        if total is not None:
            step["total"] = total
            step["done"] = 0
        self._flush()

    def update(self, key: str, *, done: int | None = None, detail: str | None = None) -> None:
        step = self._steps.get(key)
        if not step:
            return
        if done is not None:
            step["done"] = done
        if detail is not None:
            step["detail"] = detail
        self._flush()

    def complete(self, key: str, *, detail: str | None = None) -> None:
        step = self._steps.get(key)
        if not step:
            return
        step["status"] = "done"
        step["completedAt"] = datetime.utcnow().isoformat()
        if detail is not None:
            step["detail"] = detail
        if "total" in step and "done" not in step:
            step["done"] = step["total"]
        self._flush()

    def skip(self, key: str, *, detail: str | None = None) -> None:
        step = self._steps.get(key)
        if not step:
            return
        step["status"] = "skipped"
        if detail:
            step["detail"] = detail
        self._flush()

    def _flush(self) -> None:
        ordered = [self._steps[k] for k, _, _ in PIPELINE_STEPS]
        self._run.progress_detail = json.dumps(ordered)
        self._run.elapsed_seconds = round(time.time() - self._start_time, 1)
        try:
            self._db.commit()
        except Exception:
            self._db.rollback()


def upsert_senator(db: Session, data: dict) -> None:
    """
    Upsert a fully assembled senator record into the database.
    Replaces all child records (donors, votes, lobbying matches, industry donations).
    """
    senator_id = data["id"]

    existing = db.query(Senator).filter(Senator.id == senator_id).first()

    funding = data.get("funding", {})
    corruption = data.get("representationScore", data.get("corruptionScore", {}))

    senator_fields = {
        "id": senator_id,
        "bioguide_id": data.get("bioguideId"),
        "name": data.get("name", ""),
        "state": data.get("state", ""),
        "party": data.get("party", "I"),
        "years_in_office": data.get("yearsInOffice", 0),
        "initials": data.get("initials", ""),
        "punk_nickname": data.get("punkNickname", "TBD"),
        "score_funding_independence": corruption.get("fundingIndependence", 0),
        "score_promise_persistence": corruption.get("promisePersistence", 0),
        "score_independent_voting": corruption.get("independentVoting", 0),
        "score_funding_diversity": corruption.get("fundingDiversity", 0),
        "total_raised": funding.get("totalRaised", 0),
        "total_from_pacs": funding.get("totalFromPACs", 0),
        "small_donor_percentage": funding.get("smallDonorPercentage", 0),
        "voting_summary": data.get("votingRecord", {}).get("votingSummary", ""),
        "platform_summary": data.get("platformSummary", ""),
        "updated_at": datetime.utcnow(),
    }

    if existing:
        for key, value in senator_fields.items():
            setattr(existing, key, value)
        # Delete old child records
        db.query(Donor).filter(Donor.senator_id == senator_id).delete()
        db.query(IndustryDonation).filter(
            IndustryDonation.senator_id == senator_id
        ).delete()
        db.query(KeyVote).filter(KeyVote.senator_id == senator_id).delete()
        db.query(LobbyingMatch).filter(
            LobbyingMatch.senator_id == senator_id
        ).delete()
    else:
        senator_fields["created_at"] = datetime.utcnow()
        existing = Senator(**senator_fields)
        db.add(existing)

    # Add top donors
    for rank, donor_data in enumerate(funding.get("topDonors", []), start=1):
        db.add(
            Donor(
                senator_id=senator_id,
                name=donor_data.get("name", "Unknown"),
                total=donor_data.get("total", 0),
                type=donor_data.get("type", "PAC"),
                industry=donor_data.get("industry", "OTHER"),
                rank=rank,
                pac_sponsor=donor_data.get("pacSponsor"),
                pac_industry=donor_data.get("pacIndustry"),
                pac_analysis=donor_data.get("pacAnalysis"),
            )
        )

    # Add industry donations
    for ind_data in funding.get("industryBreakdown", []):
        db.add(
            IndustryDonation(
                senator_id=senator_id,
                industry=ind_data.get("industry", "OTHER"),
                name=ind_data.get("name", "Other"),
                total=ind_data.get("total", 0),
                percentage=ind_data.get("percentage", 0),
            )
        )

    # Add votes (both key and recent)
    voting_record = data.get("votingRecord", {})
    all_vote_entries = [
        (v, "key") for v in voting_record.get("keyVotes", [])
    ] + [
        (v, "recent") for v in voting_record.get("recentVotes", [])
    ]
    for vote_data, category in all_vote_entries:
        impacted = vote_data.get("impactedGroups", [])
        affected = vote_data.get("affectedIndustries", [])
        db.add(
            KeyVote(
                senator_id=senator_id,
                bill_name=vote_data.get("billName", "Unknown Bill"),
                bill_id=vote_data.get("billId", ""),
                date=vote_data.get("date", ""),
                vote=vote_data.get("vote", "Not Voting"),
                policy_area=vote_data.get("policyArea", "PROCEDURAL"),
                stance=vote_data.get("stance", "neutral"),
                stance_vote=vote_data.get("stanceVote"),
                impacted_groups=json.dumps(impacted if isinstance(impacted, list) else []),
                affected_industries=json.dumps(affected if isinstance(affected, list) else []),
                pro_business_vote=vote_data.get("proBusinessVote"),
                classification=vote_data.get("classification", "mixed"),
                description=vote_data.get("description", ""),
                corporate_interest=vote_data.get("corporateInterest", ""),
                public_impact=vote_data.get("publicImpact", ""),
                relevant_donors=json.dumps(
                    vote_data.get("relevantDonors", [])
                ),
                relevant_donor_total=vote_data.get("relevantDonorTotal", 0),
                party_leaning=vote_data.get("partyLeaning"),
                voted_with_party=vote_data.get("votedWithParty"),
                vote_category=vote_data.get("voteCategory", category),
                key_vote_reasoning=vote_data.get("keyVoteReasoning"),
            )
        )

    # Add lobbying matches
    for match_data in data.get("lobbyingMatches", []):
        db.add(
            LobbyingMatch(
                senator_id=senator_id,
                lobbyist_org=match_data.get("lobbyistOrg", "Unknown"),
                industry=match_data.get("industry", "OTHER"),
                lobbying_spend=match_data.get("lobbyingSpend", 0),
                donation_to_senator=match_data.get("donationToSenator", 0),
                bills_influenced=json.dumps(
                    match_data.get("billsInfluenced", [])
                ),
                senator_vote_aligned=match_data.get(
                    "senatorVoteAligned", False
                ),
                description=match_data.get("description", ""),
            )
        )

    # Add campaign promises
    db.query(CampaignPromise).filter(
        CampaignPromise.senator_id == senator_id
    ).delete()
    for promise_data in data.get("campaignPromises", []):
        db.add(
            CampaignPromise(
                senator_id=senator_id,
                promise_text=promise_data.get("promiseText", ""),
                category=promise_data.get("category", "other"),
                alignment=promise_data.get("alignment", "unclear"),
                related_votes=json.dumps(
                    promise_data.get("relatedVotes", [])
                ),
                analysis=promise_data.get("analysis", ""),
                party_alignment=promise_data.get("partyAlignment"),
            )
        )

    # Save partisan depth profile as JSON on the senator row
    partisan_depth_data = data.get("partisanDepth")
    if partisan_depth_data and existing:
        existing.partisan_depth = json.dumps(partisan_depth_data)

    db.flush()


def _acquire_pipeline_lock(db: Session) -> PipelineRun | None:
    """Atomically check for a running pipeline and create a new locked run.

    Uses the shared SQLite database so the lock works across blue/green
    containers.  Returns the new PipelineRun if the lock was acquired,
    or None if another pipeline is already running.
    """
    running = (
        db.query(PipelineRun)
        .filter(PipelineRun.status == "running")
        .first()
    )
    if running:
        age = (datetime.utcnow() - running.started_at).total_seconds()
        if age > 43200:  # 12 hours -- treat as stale
            running.status = "stale"
            running.completed_at = datetime.utcnow()
            running.error_message = "Marked stale: exceeded 2-hour timeout"
            db.commit()
            logger.warning("Cleaned up stale pipeline run #%d (age: %ds)", running.id, int(age))
        else:
            return None

    pipeline_run = PipelineRun(started_at=datetime.utcnow(), status="running")
    db.add(pipeline_run)
    db.commit()
    return pipeline_run


async def run_full_pipeline(
    senator_filter: str | None = None,
    fetch_only: bool = False,
) -> dict:
    """
    Full pipeline implementation.

    Args:
        senator_filter: Optional name/id filter to process a single senator.
        fetch_only: If True, stop after fetch phase (no LLM analysis).

    Returns:
        Dict with pipeline run stats.
    """
    start_time = time.time()
    reset_stats()
    reset_client()

    # Clear in-memory caches from prior runs to bound memory usage.
    # Note: the ChromaDB reference corpus and learning store persist across
    # runs intentionally — they're the adaptive learning mechanism.
    clear_alignment_cache()
    clear_bill_embedding_cache()
    clear_reference_cache()
    clear_platform_cache()
    from app.pipeline.transform.industry_classifier import clear_industry_embedding_cache
    clear_industry_embedding_cache()

    db: Session = SessionLocal()

    # Verify embedding model version — invalidate stored embeddings on change
    if not check_model_version():
        invalidate_on_model_change(db_session=db)
    else:
        _write_model_version()

    # Build party platform centroids from seeds + accumulated bill data.
    # This implements Bayesian self-training: seed descriptions act as a
    # prior, and real bill data from previous runs updates the posterior.
    initialize_platform_embeddings(db)

    pipeline_run = _acquire_pipeline_lock(db)
    if pipeline_run is None:
        logger.warning("Pipeline already running in another process — skipping")
        db.close()
        return {"status": "skipped", "reason": "already_running"}

    progress = ProgressTracker(pipeline_run, db, start_time)

    try:
        logger.info("=== CIVITAS DATA PIPELINE ===")
        if senator_filter:
            logger.info("Single senator: %s", senator_filter)
        if fetch_only:
            logger.info("Fetch only mode -- no LLM analysis")

        pipeline_run.current_phase = "fetch"
        pipeline_run.elapsed_seconds = 0
        db.commit()

        # Validate API keys
        if not settings.DATA_GOV_API_KEY:
            raise RuntimeError(
                "DATA_GOV_API_KEY not set. Add it to .env file. "
                "Sign up free at https://api.data.gov/signup/"
            )

        # ========================================
        # PHASE 1: FETCH
        # ========================================
        logger.info("--- Phase 1: FETCH ---")

        async with httpx.AsyncClient() as client:
            # 1a. Fetch all current senators from Congress.gov
            logger.info("Fetching senator list from Congress.gov...")
            progress.begin("fetch_senators")
            raw_members = await fetch_senators(client, db)
            if not raw_members:
                raise RuntimeError(
                    "Failed to fetch senators. Check your DATA_GOV_API_KEY."
                )
            logger.info("Found %d senators", len(raw_members))
            progress.complete("fetch_senators", detail=f"{len(raw_members)} found")

            # 1b. Fetch detailed member info
            logger.info("Fetching member details...")
            progress.begin("fetch_member_details", total=len(raw_members))
            member_details: dict[str, dict] = {}
            for mi, m in enumerate(raw_members):
                bioguide_id = m.get("bioguideId")
                if bioguide_id:
                    detail = await fetch_member_detail(client, db, bioguide_id)
                    if detail:
                        member_details[bioguide_id] = detail
                progress.update("fetch_member_details", done=mi + 1)
            progress.complete("fetch_member_details", detail=f"{len(member_details)} detailed")

            # ========================================
            # PHASE 2: TRANSFORM (members)
            # ========================================
            pipeline_run.current_phase = "transform"
            pipeline_run.elapsed_seconds = round(time.time() - start_time, 1)
            db.commit()
            logger.info("--- Phase 2: TRANSFORM (members) ---")
            progress.begin("normalize_members")
            senators = normalize_members(raw_members, member_details)
            logger.info("Normalized %d senators", len(senators))
            progress.complete("normalize_members", detail=f"{len(senators)} senators")

            # Filter to single senator if specified
            if senator_filter:
                query = senator_filter.lower()
                senators = [
                    s
                    for s in senators
                    if query in s["name"].lower() or query in s["id"].lower()
                ]
                if not senators:
                    raise RuntimeError(f"Senator not found: {senator_filter}")
                logger.info(
                    "Filtered to: %s",
                    ", ".join(s["name"] for s in senators),
                )

            pipeline_run.senators_total = len(senators)
            db.commit()

            # 1c. Dynamically discover significant bills
            logger.info("Discovering significant bills...")
            progress.begin("discover_bills")
            discovered_bills = await fetch_significant_bills(client, db, max_bills=100)
            logger.info("Found %d significant bills", len(discovered_bills))
            progress.complete("discover_bills", detail=f"{len(discovered_bills)} discovered")

            # Fetch detailed data for each discovered bill
            progress.begin("fetch_bill_details", total=len(discovered_bills))
            bills_data: list[dict] = []
            for bill_idx, bill_ref in enumerate(discovered_bills):
                bill = await fetch_bill(
                    client, db, bill_ref["congress"], bill_ref["type"], bill_ref["number"]
                )
                summaries = await fetch_bill_summaries(
                    client, db, bill_ref["congress"], bill_ref["type"], bill_ref["number"]
                )
                actions = await fetch_bill_actions(
                    client, db, bill_ref["congress"], bill_ref["type"], bill_ref["number"]
                )

                # Fetch full bill text from GovInfo
                full_text = await fetch_bill_text(
                    client, db, bill_ref["congress"], bill_ref["type"], bill_ref["number"]
                )

                if bill:
                    sponsors = bill.get("sponsors", [])
                    sponsor_party = None
                    if sponsors:
                        sponsor_party = sponsors[0].get("party")

                    bills_data.append(
                        {
                            "billId": f"{bill_ref['type'].upper()}.{bill_ref['number']}",
                            "billName": bill_ref["name"],
                            "congress": bill_ref["congress"],
                            "summary": (
                                summaries[0].get("text", "")
                                if summaries
                                else bill.get("title", "")
                            ),
                            "fullText": full_text or "",
                            "actions": actions or [],
                            "sponsorParty": sponsor_party,
                        }
                    )
                progress.update("fetch_bill_details", done=bill_idx + 1)
            logger.info(
                "Fetched details for %d/%d discovered bills",
                len(bills_data),
                len(discovered_bills),
            )
            progress.complete("fetch_bill_details", detail=f"{len(bills_data)} fetched")

            # 1d. Fetch roll call votes for each bill
            logger.info("Fetching roll call votes...")
            progress.begin("fetch_roll_calls", total=len(bills_data))
            roll_call_data_map: dict[str, dict] = {}
            for rc_idx, bill in enumerate(bills_data):
                roll_call_ref = find_senate_roll_call(bill.get("actions"))
                if roll_call_ref:
                    roll_call_data = await fetch_roll_call_vote(
                        client,
                        db,
                        roll_call_ref["congress"],
                        roll_call_ref["session"],
                        roll_call_ref["rollCallNumber"],
                    )
                    if roll_call_data:
                        roll_call_data_map[bill["billId"]] = roll_call_data
                progress.update("fetch_roll_calls", done=rc_idx + 1)
            logger.info(
                "Roll call data fetched for %d/%d bills",
                len(roll_call_data_map),
                len(bills_data),
            )
            progress.complete("fetch_roll_calls", detail=f"{len(roll_call_data_map)} matched")

            # 1d.2 Fetch recent roll calls from Senate.gov across multiple sessions.
            # We fetch from the current congress AND the previous congress (both sessions)
            # to get a richer history — the 119th congress has many nomination votes
            # (classified "mixed"); the 118th had more substantive legislation.
            logger.info("Fetching recent Senate roll calls (multi-session)...")
            progress.begin("fetch_recent_rcs", total=4)
            all_recent_roll_calls: list[dict] = []
            seen_roll_ids: set[str] = set()

            fetch_sessions = [
                (settings.CURRENT_CONGRESS, 1),      # e.g. 119th sess 1 (2025)
                (settings.CURRENT_CONGRESS, 2),      # 119th sess 2 (2026, if started)
                (settings.CURRENT_CONGRESS - 1, 2),  # 118th sess 2 (2024)
                (settings.CURRENT_CONGRESS - 1, 1),  # 118th sess 1 (2023)
            ]

            for sess_idx, (congress_num, session_num) in enumerate(fetch_sessions):
                session_rcs = await fetch_recent_roll_calls(
                    client, db,
                    congress=congress_num,
                    session_number=session_num,
                    count=100,
                )
                added = 0
                for rc in session_rcs:
                    rc_id = (
                        rc.get("documentName")
                        or f"Roll-{congress_num}-{session_num}-{rc.get('rollNumber', '')}"
                    )
                    if rc_id not in seen_roll_ids:
                        seen_roll_ids.add(rc_id)
                        all_recent_roll_calls.append(rc)
                        added += 1
                logger.info(
                    "Congress %d session %d: %d roll calls (%d new)",
                    congress_num, session_num, len(session_rcs), added,
                )
                progress.update("fetch_recent_rcs", done=sess_idx + 1)

            recent_roll_calls = all_recent_roll_calls

            # Build a map for recent roll calls keyed by document name
            recent_rc_map: dict[str, dict] = {}
            for rc in recent_roll_calls:
                congress_num_rc = rc.get("congress", settings.CURRENT_CONGRESS)
                session_num_rc = rc.get("session", 1)
                rc_id = (
                    rc.get("documentName")
                    or f"Roll-{congress_num_rc}-{session_num_rc}-{rc.get('rollNumber', '')}"
                )
                recent_rc_map[rc_id] = rc
            logger.info("Total unique recent roll calls: %d", len(recent_roll_calls))
            progress.complete("fetch_recent_rcs", detail=f"{len(recent_roll_calls)} unique")

            # 1e. Fetch FEC data for each senator
            logger.info("Fetching FEC financial data...")
            progress.begin("fetch_fec", total=len(senators))
            fec_data: dict[str, dict] = {}
            for fec_idx, senator in enumerate(senators):
                candidate = await find_candidate(
                    client, db, senator["name"], senator["state"]
                )
                if not candidate or not candidate.get("candidate_id"):
                    logger.warning(
                        "No FEC match for %s (%s)",
                        senator["name"],
                        senator["state"],
                    )
                    continue

                candidate_id = candidate["candidate_id"]
                financials = await fetch_candidate_financials(
                    client, db, candidate_id
                )
                committees = await fetch_candidate_committees(
                    client, db, candidate_id
                )
                committee_id = (
                    committees[0].get("committee_id")
                    if committees
                    else None
                )

                receipts: list = []
                pac_receipts_data: list = []
                aggregated: list = []
                if committee_id:
                    receipts = await fetch_committee_receipts(
                        client, db, committee_id
                    )
                    pac_receipts_data = await fetch_pac_receipts(
                        client, db, committee_id
                    )
                    aggregated = await fetch_aggregated_contributors(
                        client, db, committee_id
                    )

                fec_data[senator["id"]] = {
                    "candidate": candidate,
                    "financials": financials,
                    "receipts": receipts,
                    "pacReceipts": pac_receipts_data,
                    "aggregated": aggregated,
                }
                progress.update("fetch_fec", done=fec_idx + 1)
            logger.info(
                "FEC data fetched for %d/%d senators",
                len(fec_data),
                len(senators),
            )
            progress.complete("fetch_fec", detail=f"{len(fec_data)}/{len(senators)} matched")

            # 1f. Fetch platform text for each senator from their official website
            logger.info("Fetching senator platform text from official websites...")
            progress.begin("fetch_platforms", total=len(senators))
            platform_texts: dict[str, str] = {}
            for plat_idx, senator in enumerate(senators):
                text = await fetch_senator_platform_text(
                    client,
                    db,
                    senator["id"],
                    senator["name"],
                    senator.get("officialWebsiteUrl", ""),
                )
                platform_texts[senator["id"]] = text
                progress.update("fetch_platforms", done=plat_idx + 1)
            fetched_platforms = sum(1 for t in platform_texts.values() if t)
            logger.info(
                "Platform text fetched for %d/%d senators",
                fetched_platforms,
                len(senators),
            )
            progress.complete("fetch_platforms", detail=f"{fetched_platforms}/{len(senators)} found")

            # 1g. Fetch Congressional Record floor remarks
            logger.info("Fetching Congressional Record floor proceedings...")
            progress.begin("fetch_floor_remarks")
            try:
                all_floor_remarks = await fetch_floor_remarks(
                    client, db, days_back=60, max_granules_per_day=8,
                )
                logger.info(
                    "Floor remarks: %d speakers, %d total remarks",
                    len(all_floor_remarks),
                    sum(len(v) for v in all_floor_remarks.values()),
                )
                total_remarks = sum(len(v) for v in all_floor_remarks.values())
                progress.complete(
                    "fetch_floor_remarks",
                    detail=f"{len(all_floor_remarks)} speakers, {total_remarks} remarks",
                )
            except Exception as e:
                logger.warning(
                    "Congressional Record fetch failed: %s — continuing without floor data",
                    e,
                )
                all_floor_remarks = {}
                progress.complete("fetch_floor_remarks", detail="failed — skipped")

        if fetch_only:
            logger.info("=== FETCH COMPLETE (fetch-only mode) ===")
            for sk in ("classify_bills", "classify_recent", "embed_bills",
                        "classify_donors", "prepare_senators", "analyze_senators",
                        "explore_documents", "justice_scorecards", "finalize"):
                progress.skip(sk, detail="fetch-only mode")
            elapsed = time.time() - start_time
            pipeline_run.status = "completed"
            pipeline_run.completed_at = datetime.utcnow()
            pipeline_run.elapsed_seconds = elapsed
            db.commit()
            return {
                "status": "completed",
                "fetch_only": True,
                "senators_fetched": len(senators),
                "bills_fetched": len(bills_data),
                "elapsed_seconds": round(elapsed, 1),
            }

        # ========================================
        # PHASE 3: ANALYZE
        # ========================================
        pipeline_run.current_phase = "analyze"
        pipeline_run.elapsed_seconds = round(time.time() - start_time, 1)
        db.commit()
        logger.info("--- Phase 3: ANALYZE ---")

        # 3a. Classify bills using embeddings (zero LLM calls)
        logger.info("Classifying key bills (embedding-based)...")
        progress.begin("classify_bills", total=len(bills_data))
        try:
            classified_bills = await classify_all_bills(bills_data, db_session=db)
            logger.info("Classified %d key bills", len(classified_bills))
            progress.complete("classify_bills", detail=f"{len(classified_bills)} classified")
        except Exception as e:
            logger.error("Bill classification failed: %s — continuing with empty bill list", e)
            classified_bills = []
            progress.complete("classify_bills", detail="failed")

        # Refine content-based party alignment with vote data as a secondary signal.
        # Content analysis (what the bill does) is the primary signal.
        # Vote tallies validate or adjust — they don't blindly override, because
        # senators trade votes, face whip pressure, and make tactical compromises.
        from app.pipeline.analyze.party_platform import (
            refine_with_vote_data,
            record_sponsor_alignment,
        )
        for bill in classified_bills:
            roll_call_data = roll_call_data_map.get(bill["billId"])
            if roll_call_data:
                vote_split = compute_party_split(roll_call_data)
                bill["partyLeaning"] = refine_with_vote_data(
                    bill.get("partyLeaning", "bipartisan"), vote_split,
                )

        # Use bill sponsor party as ground truth for the learning store.
        # Bills sponsored by R senators are examples of R-aligned legislation.
        for bill_ref in bills_data:
            sponsor_party = bill_ref.get("sponsorParty")
            if sponsor_party in ("R", "D"):
                bill_id = bill_ref["billId"]
                bill_text = f"{bill_ref['billName']} {(bill_ref.get('summary') or '')[:200]}"
                try:
                    record_sponsor_alignment(db, bill_id, bill_text, sponsor_party)
                except Exception:
                    pass

        # 3a.2 Classify recent roll call votes (embedding-based, zero LLM)
        classified_recent: list[dict] = []
        if recent_roll_calls:
            logger.info("Classifying %d recent votes (embedding-based)...", len(recent_roll_calls))
            progress.begin("classify_recent", total=len(recent_roll_calls))
            classified_recent = await classify_recent_votes(
                recent_roll_calls, db_session=db
            )
            logger.info("Classified %d recent votes", len(classified_recent))
            progress.complete("classify_recent", detail=f"{len(classified_recent)} classified")
        else:
            progress.skip("classify_recent", detail="no roll calls")

        pipeline_run.bills_classified = len(classified_bills) + len(classified_recent)
        db.commit()

        # Override partyLeaning for recent votes too, using the roll call member data.
        for rc in classified_recent:
            rc_id = rc.get("billId", "")
            roll_call_data = recent_rc_map.get(rc_id)
            if roll_call_data:
                computed_split = compute_party_split(roll_call_data)
                if computed_split:
                    rc["partyLeaning"] = computed_split

        # 3a.3 Embed classified bills in vector database for semantic search
        logger.info("Embedding bills in vector database...")
        all_bills_for_embedding = classified_bills + classified_recent
        progress.begin("embed_bills", total=len(all_bills_for_embedding))
        try:
            embed_bills(all_bills_for_embedding)
            logger.info("Embedded %d bills in vector database", len(all_bills_for_embedding))
            progress.complete("embed_bills", detail=f"{len(all_bills_for_embedding)} embedded")
        except Exception as e:
            logger.error("Bill embedding failed: %s — vector search will be unavailable this run", e)
            progress.complete("embed_bills", detail="failed")

        # 3a.3 Hybrid donor classification (FEC metadata → rules → embeddings → LLM)
        logger.info("Collecting unique donors for hybrid classification...")
        progress.begin("classify_donors")
        all_donor_entries: list[dict] = []
        skip_employers = {
            "NONE", "N/A", "SELF-EMPLOYED", "SELF EMPLOYED", "RETIRED",
            "NOT EMPLOYED", "SELF", "HOMEMAKER", "INFORMATION REQUESTED",
            "STUDENT", "UNEMPLOYED", "DISABLED",
        }
        for senator in senators:
            fec = fec_data.get(senator["id"])
            if not fec:
                continue
            cand_name = (fec.get("candidate") or {}).get("name", "")
            for r in fec.get("pacReceipts") or []:
                name = r.get("contributor_name") or ""
                if not name:
                    committee = r.get("committee") or {}
                    name = committee.get("name", "")
                if name and name != "Unknown":
                    all_donor_entries.append({
                        "name": name,
                        "amount": r.get("contribution_receipt_amount", 0) or 0,
                        "fec_receipt": r,
                        "candidate_name": cand_name,
                    })
            for r in fec.get("receipts") or []:
                employer = (r.get("contributor_employer") or "").strip()
                if employer and employer.upper() not in skip_employers:
                    all_donor_entries.append({
                        "name": employer,
                        "amount": r.get("contribution_receipt_amount", 0) or 0,
                    })
            for c in fec.get("aggregated") or []:
                name = c.get("contributor_name") or "Unknown"
                if name and name != "Unknown":
                    all_donor_entries.append({
                        "name": name,
                        "amount": c.get("total", 0) or 0,
                        "candidate_name": cand_name,
                    })

        ai_classifications: dict[str, dict] = {}
        if all_donor_entries:
            progress.update("classify_donors", detail=f"{len(all_donor_entries)} donors queued")

            def _flush_donor_progress():
                s = get_llm_stats()
                pipeline_run.llm_calls = s["total_calls"]
                pipeline_run.cache_hits = s["cache_hits"]
                pipeline_run.cache_misses = s["cache_misses"]
                pipeline_run.elapsed_seconds = round(time.time() - start_time, 1)
                db.commit()

            ai_classifications = await classify_donors_hybrid(
                all_donor_entries, db_session=db, on_progress=_flush_donor_progress
            )
            progress.complete("classify_donors", detail=f"{len(ai_classifications)} classified")
        else:
            progress.skip("classify_donors", detail="no donors")

        _flush = get_llm_stats()
        pipeline_run.llm_calls = _flush["total_calls"]
        pipeline_run.cache_hits = _flush["cache_hits"]
        pipeline_run.cache_misses = _flush["cache_misses"]
        pipeline_run.elapsed_seconds = round(time.time() - start_time, 1)
        db.commit()

        # 3b. Prepare senator data for batch LLM analysis
        logger.info("Preparing senator data for batch analysis...")
        progress.begin("prepare_senators", total=len(senators))
        results: list[dict] = []
        success_count = 0
        fail_count = 0

        senator_prepared: list[dict] = []
        for prep_idx, senator in enumerate(senators):
            try:
                fec = fec_data.get(senator["id"])
                if fec:
                    funding = normalize_finance(
                        fec["candidate"],
                        fec.get("financials") or [],
                        fec.get("receipts") or [],
                        fec.get("pacReceipts") or [],
                        fec.get("aggregated") or [],
                        ai_classifications=ai_classifications,
                        db_session=db,
                    )
                else:
                    funding = senator.get("funding", {})

                # Extract senator's last name for vote matching
                name_parts = senator["name"].split()
                last_name = name_parts[-1] if name_parts else ""
                if not last_name:
                    logger.warning(
                        "Senator %s has no parseable last name — vote matching will be skipped",
                        senator.get("id", "unknown"),
                    )

                senator_votes: dict[str, str] = {}
                for bill in classified_bills:
                    roll_call_data = roll_call_data_map.get(bill["billId"])
                    if roll_call_data:
                        vote = extract_senator_vote(
                            roll_call_data,
                            senator.get("bioguideId", ""),
                            last_name,
                            senator["state"],
                        )
                        if vote:
                            senator_votes[bill["billId"]] = vote

                # Also extract recent roll call votes into the same map so they
                # contribute to stance breakdown in normalize_votes
                for rc in classified_recent:
                    rc_id = rc.get("billId", "")
                    roll_call_data = recent_rc_map.get(rc_id)
                    if roll_call_data:
                        vote = extract_senator_vote(
                            roll_call_data,
                            senator.get("bioguideId", ""),
                            last_name,
                            senator["state"],
                        )
                        if vote:
                            senator_votes[rc_id] = vote

                # Pass both key bills and recent roll calls to normalize_votes
                # so all tracked votes contribute to the policy breakdown
                all_classified = classified_bills + classified_recent
                voting_record = normalize_votes(
                    senator.get("bioguideId", ""),
                    all_classified,
                    senator_votes,
                    senator_party=senator.get("party", "I"),
                )

                # Normalize recent votes for display in the UI
                recent_senator_votes = normalize_recent_votes(
                    classified_recent,
                    recent_rc_map,
                    last_name,
                    senator["state"],
                    senator.get("party", "I"),
                )
                voting_record["recentVotes"] = recent_senator_votes

                senator_prepared.append(
                    {
                        "senator": senator,
                        "funding": funding,
                        "votingRecord": voting_record,
                    }
                )
                progress.update("prepare_senators", done=prep_idx + 1)
            except Exception as e:
                logger.error(
                    "  Prep failed for %s: %s", senator["name"], str(e)
                )
                fail_count += 1
                results.append(senator)
                progress.update("prepare_senators", done=prep_idx + 1)

        progress.complete("prepare_senators", detail=f"{len(senator_prepared)} ready, {fail_count} failed")

        _flush = get_llm_stats()
        pipeline_run.llm_calls = _flush["total_calls"]
        pipeline_run.cache_hits = _flush["cache_hits"]
        pipeline_run.cache_misses = _flush["cache_misses"]
        pipeline_run.elapsed_seconds = round(time.time() - start_time, 1)
        db.commit()

        logger.info("Processing %d senators (1 LLM call each)...", len(senator_prepared))

        progress.begin("analyze_senators", total=len(senator_prepared))
        for senator_idx, prepared in enumerate(senator_prepared):
            senator = prepared["senator"]
            funding = prepared["funding"]
            voting_record = prepared["votingRecord"]

            logger.info(
                "  [%d/%d] %s",
                senator_idx + 1,
                len(senator_prepared),
                senator["name"],
            )
            progress.update("analyze_senators", done=senator_idx, detail=senator["name"])

            try:
                all_votes = (voting_record.get("keyVotes") or []) + (
                    voting_record.get("recentVotes") or []
                )

                analysis_results = await analyze_senator_batch(
                    [
                        {
                            "senator": senator,
                            "donors": funding.get("topDonors") or [],
                            "keyVotes": voting_record.get("keyVotes") or [],
                            "allVotes": all_votes,
                            "platformText": platform_texts.get(senator["id"], ""),
                        }
                    ],
                    db_session=db,
                )
                analysis = analysis_results[0] if analysis_results else {}
                platform_data = {
                    "campaignPromises": analysis.get("campaignPromises", []),
                    "platformSummary": analysis.get("platformSummary", ""),
                }
                all_key_votes = analysis.get("keyVotes") or voting_record["keyVotes"]
                key_vote_ids = set(analysis.get("keyVoteIds", []))
                reasoning_map = analysis.get("reasoning", {})

                final_key_votes = []
                final_recent_votes = []

                for v in all_key_votes:
                    if v["billId"] in key_vote_ids:
                        v["voteCategory"] = "key"
                        v["keyVoteReasoning"] = reasoning_map.get(v["billId"])
                        final_key_votes.append(v)
                    else:
                        v["voteCategory"] = "recent"
                        final_recent_votes.append(v)

                if not final_key_votes and all_key_votes:
                    for v in all_key_votes[:min(5, len(all_key_votes))]:
                        v["voteCategory"] = "key"
                        final_key_votes.append(v)
                    final_recent_votes = [
                        v for v in final_recent_votes
                        if v["billId"] not in {kv["billId"] for kv in final_key_votes}
                    ]

                voting_record["keyVotes"] = final_key_votes
                voting_record["recentVotes"] = final_recent_votes
                voting_record["votingSummary"] = analysis.get("votingSummary", "")

                lobbying_matches = analysis.get("lobbyingMatches", [])

                # Match Congressional Record floor remarks to this senator
                name_parts = senator["name"].split()
                senator_last_name = name_parts[-1].upper() if name_parts else ""
                senator_floor_remarks = all_floor_remarks.get(
                    senator_last_name, []
                )
                floor_advocacy = analyze_floor_advocacy(
                    senator_floor_remarks,
                    platform_data.get("campaignPromises", []),
                )
                if senator_floor_remarks:
                    logger.info(
                        "    floor remarks: %d (%d categories advocated)",
                        floor_advocacy["totalRemarks"],
                        len(floor_advocacy["advocatedCategories"]),
                    )

                temp_senator = {
                    **senator,
                    "funding": funding,
                    "votingRecord": voting_record,
                    "lobbyingMatches": lobbying_matches,
                    "campaignPromises": platform_data.get("campaignPromises", []),
                }
                corruption_score = calculate_scores(
                    temp_senator,
                    {"flipFlopScore": analysis.get("flipFlopScore", 25)},
                    floor_advocacy=floor_advocacy,
                )

                # Enrich donors with PAC details from combined analysis
                pac_detail_map = {
                    d["name"].upper().strip(): d
                    for d in analysis.get("pacDetails", [])
                }
                if pac_detail_map:
                    enriched_donors = []
                    for d in funding.get("topDonors", []):
                        key = d["name"].upper().strip()
                        if key in pac_detail_map:
                            enriched_donors.append({**d, **pac_detail_map[key]})
                        else:
                            enriched_donors.append(d)
                    funding["topDonors"] = enriched_donors

                result = build_senator(
                    senator,
                    funding,
                    voting_record,
                    lobbying_matches,
                    corruption_score,
                )

                result["campaignPromises"] = platform_data.get("campaignPromises", [])
                result["platformSummary"] = platform_data.get("platformSummary", "")

                from app.pipeline.analyze.party_platform import analyze_partisan_depth
                partisan_profile = analyze_partisan_depth(
                    platform_data.get("campaignPromises", []),
                    senator.get("party", ""),
                )
                result["partisanDepth"] = partisan_profile
                if partisan_profile.get("totalPositions", 0) > 0:
                    logger.info(
                        "    partisan depth: %s (%s, %d positions, %d cross-party)",
                        partisan_profile["depth"],
                        partisan_profile["overallParty"],
                        partisan_profile["totalPositions"],
                        partisan_profile["crossPartyCount"],
                    )

                results.append(result)
                success_count += 1

                upsert_senator(db, result)
                pipeline_run.senators_processed = success_count
                incremental_stats = get_llm_stats()
                pipeline_run.llm_calls = incremental_stats["total_calls"]
                pipeline_run.cache_hits = incremental_stats["cache_hits"]
                pipeline_run.cache_misses = incremental_stats["cache_misses"]
                pipeline_run.bills_classified = len(classified_bills)
                pipeline_run.elapsed_seconds = round(time.time() - start_time, 1)
                db.commit()

                from app.config_definitions import SCORE_WEIGHTS
                weighted_score = round(sum(
                    corruption_score.get(k, 0) * w
                    for k, w in SCORE_WEIGHTS.items()
                ))
                logger.info("    score %d/100", weighted_score)
                progress.update("analyze_senators", done=senator_idx + 1)
            except Exception:
                logger.exception("  Failed for %s", senator["name"])
                fail_count += 1
                results.append(senator)
                pipeline_run.senators_failed = fail_count
                pipeline_run.senators_processed = success_count
                pipeline_run.elapsed_seconds = round(time.time() - start_time, 1)
                db.commit()
                progress.update("analyze_senators", done=senator_idx + 1)

        progress.complete(
            "analyze_senators",
            detail=f"{success_count} OK, {fail_count} failed",
        )

        # ========================================
        # PHASE 4: EXPLORE DOCUMENTS
        # ========================================
        pipeline_run.current_phase = "explore"
        pipeline_run.elapsed_seconds = round(time.time() - start_time, 1)
        db.commit()
        logger.info("--- Phase 4: EXPLORE DOCUMENTS ---")
        progress.begin("explore_documents")
        explore_result: dict = {}
        try:
            from app.pipeline.explore_pipeline import run_explore_pipeline
            explore_result = await run_explore_pipeline(days_back=60)
            total_docs = sum(v for v in explore_result.values() if isinstance(v, int))
            logger.info("Explore pipeline ingested %d documents", total_docs)
            progress.complete("explore_documents", detail=f"{total_docs} ingested")
        except Exception as e:
            logger.exception("Explore pipeline failed: %s — continuing", e)
            progress.complete("explore_documents", detail="failed")

        # ========================================
        # PHASE 5: SCOTUS JUSTICES
        # ========================================
        pipeline_run.current_phase = "justices"
        pipeline_run.elapsed_seconds = round(time.time() - start_time, 1)
        db.commit()
        logger.info("--- Phase 5: SCOTUS JUSTICES ---")
        progress.begin("justice_scorecards")
        justice_result: dict = {}
        try:
            from app.pipeline.justice_pipeline import run_justice_pipeline
            justice_db = SessionLocal()
            try:
                justice_result = await run_justice_pipeline(justice_db)
            finally:
                justice_db.close()
            justices_count = justice_result.get("justices_scored", 0)
            logger.info("Justice pipeline scored %d justices", justices_count)
            progress.complete("justice_scorecards", detail=f"{justices_count} scored")
        except Exception as e:
            logger.exception("Justice pipeline failed: %s — continuing", e)
            progress.complete("justice_scorecards", detail="failed")

        # ========================================
        # PHASE 6: FINALIZE
        # ========================================
        pipeline_run.current_phase = "finalize"
        pipeline_run.elapsed_seconds = round(time.time() - start_time, 1)
        db.commit()
        logger.info("--- Phase 6: FINALIZE ---")
        progress.begin("finalize")

        llm_stats = get_llm_stats()
        elapsed = time.time() - start_time

        pipeline_run.status = "completed"
        pipeline_run.completed_at = datetime.utcnow()
        pipeline_run.senators_processed = success_count
        pipeline_run.senators_failed = fail_count
        pipeline_run.bills_classified = len(classified_bills)
        pipeline_run.llm_calls = llm_stats["total_calls"]
        pipeline_run.cache_hits = llm_stats["cache_hits"]
        pipeline_run.cache_misses = llm_stats["cache_misses"]
        pipeline_run.elapsed_seconds = round(elapsed, 1)
        db.commit()
        progress.complete("finalize", detail="done")

        logger.info("=== PIPELINE COMPLETE ===")
        logger.info(
            "Senators: %d success, %d failed", success_count, fail_count
        )
        logger.info("Bills classified: %d", len(classified_bills))
        logger.info(
            "LLM: %d calls, %s",
            llm_stats["total_calls"],
            llm_stats["estimated_cost"],
        )
        logger.info("Time: %.1fs", elapsed)

        return {
            "status": "completed",
            "senators_processed": success_count,
            "senators_failed": fail_count,
            "bills_classified": len(classified_bills),
            "explore": explore_result,
            "justices": justice_result,
            "llm_stats": llm_stats,
            "elapsed_seconds": round(elapsed, 1),
        }

    except BaseException as e:
        logger.exception("Pipeline failed: %s", e)
        try:
            pipeline_run.status = "failed"
            pipeline_run.completed_at = datetime.utcnow()
            pipeline_run.error_message = str(e)[:500]
            pipeline_run.elapsed_seconds = round(time.time() - start_time, 1)
            db.commit()
        except Exception:
            logger.exception("Failed to record pipeline failure in DB")
        raise
    finally:
        db.close()
