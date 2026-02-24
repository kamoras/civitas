"""
Pipeline orchestrator.

The 4 phases: FETCH -> TRANSFORM -> ANALYZE -> ASSEMBLE+SAVE

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
from app.pipeline.fetch.approval_ratings import fetch_all_senator_approvals

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
        "approval_rating": data.get("approvalRating"),
        "disapproval_rating": data.get("disapprovalRating"),
        "approval_source": data.get("approvalSource"),
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
        db.add(
            KeyVote(
                senator_id=senator_id,
                bill_name=vote_data.get("billName", "Unknown Bill"),
                bill_id=vote_data.get("billId", ""),
                date=vote_data.get("date", ""),
                vote=vote_data.get("vote", "Not Voting"),
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
            )
        )

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

    # Clear embedding caches from prior runs to bound memory usage
    clear_alignment_cache()
    clear_bill_embedding_cache()
    from app.pipeline.transform.industry_classifier import clear_industry_embedding_cache
    clear_industry_embedding_cache()

    db: Session = SessionLocal()

    # Verify embedding model version — invalidate stored embeddings on change
    if not check_model_version():
        invalidate_on_model_change(db_session=db)
    else:
        _write_model_version()

    pipeline_run = _acquire_pipeline_lock(db)
    if pipeline_run is None:
        logger.warning("Pipeline already running in another process — skipping")
        db.close()
        return {"status": "skipped", "reason": "already_running"}

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
            raw_members = await fetch_senators(client, db)
            if not raw_members:
                raise RuntimeError(
                    "Failed to fetch senators. Check your DATA_GOV_API_KEY."
                )
            logger.info("Found %d senators", len(raw_members))

            # 1b. Fetch detailed member info
            logger.info("Fetching member details...")
            member_details: dict[str, dict] = {}
            for m in raw_members:
                bioguide_id = m.get("bioguideId")
                if bioguide_id:
                    detail = await fetch_member_detail(client, db, bioguide_id)
                    if detail:
                        member_details[bioguide_id] = detail

            # ========================================
            # PHASE 2: TRANSFORM (members)
            # ========================================
            pipeline_run.current_phase = "transform"
            pipeline_run.elapsed_seconds = round(time.time() - start_time, 1)
            db.commit()
            logger.info("--- Phase 2: TRANSFORM (members) ---")
            senators = normalize_members(raw_members, member_details)
            logger.info("Normalized %d senators", len(senators))

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
            discovered_bills = await fetch_significant_bills(client, db, max_bills=100)
            logger.info("Found %d significant bills", len(discovered_bills))

            # Fetch detailed data for each discovered bill
            bills_data: list[dict] = []
            for bill_ref in discovered_bills:
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
                        }
                    )
            logger.info(
                "Fetched details for %d/%d discovered bills",
                len(bills_data),
                len(discovered_bills),
            )

            # 1d. Fetch roll call votes for each bill
            logger.info("Fetching roll call votes...")
            roll_call_data_map: dict[str, dict] = {}
            for bill in bills_data:
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
            logger.info(
                "Roll call data fetched for %d/%d bills",
                len(roll_call_data_map),
                len(bills_data),
            )

            # 1d.2 Fetch recent roll calls from Senate.gov across multiple sessions.
            # We fetch from the current congress AND the previous congress (both sessions)
            # to get a richer history — the 119th congress has many nomination votes
            # (classified "mixed"); the 118th had more substantive legislation.
            logger.info("Fetching recent Senate roll calls (multi-session)...")
            all_recent_roll_calls: list[dict] = []
            seen_roll_ids: set[str] = set()

            fetch_sessions = [
                (settings.CURRENT_CONGRESS, 1),      # e.g. 119th sess 1 (2025)
                (settings.CURRENT_CONGRESS, 2),      # 119th sess 2 (2026, if started)
                (settings.CURRENT_CONGRESS - 1, 2),  # 118th sess 2 (2024)
                (settings.CURRENT_CONGRESS - 1, 1),  # 118th sess 1 (2023)
            ]

            for congress_num, session_num in fetch_sessions:
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

            # 1e. Fetch FEC data for each senator
            logger.info("Fetching FEC financial data...")
            fec_data: dict[str, dict] = {}
            for senator in senators:
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
            logger.info(
                "FEC data fetched for %d/%d senators",
                len(fec_data),
                len(senators),
            )

            # 1f. Fetch platform text for each senator from their official website
            logger.info("Fetching senator platform text from official websites...")
            platform_texts: dict[str, str] = {}
            for senator in senators:
                text = await fetch_senator_platform_text(
                    client,
                    db,
                    senator["id"],
                    senator["name"],
                    senator.get("officialWebsiteUrl", ""),
                )
                platform_texts[senator["id"]] = text
            fetched_platforms = sum(1 for t in platform_texts.values() if t)
            logger.info(
                "Platform text fetched for %d/%d senators",
                fetched_platforms,
                len(senators),
            )

            # 1g. Fetch approval ratings
            logger.info("Fetching senator approval ratings...")
            approval_data: dict[str, dict] = {}
            try:
                approval_data = await fetch_all_senator_approvals(
                    client, db, senators,
                )
                logger.info(
                    "Approval ratings: %d/%d senators",
                    len(approval_data), len(senators),
                )
            except Exception as e:
                logger.warning(
                    "Approval rating fetch failed: %s — continuing without approval data", e,
                )

            # 1h. Fetch Congressional Record floor remarks
            logger.info("Fetching Congressional Record floor proceedings...")
            try:
                all_floor_remarks = await fetch_floor_remarks(
                    client, db, days_back=60, max_granules_per_day=8,
                )
                logger.info(
                    "Floor remarks: %d speakers, %d total remarks",
                    len(all_floor_remarks),
                    sum(len(v) for v in all_floor_remarks.values()),
                )
            except Exception as e:
                logger.warning(
                    "Congressional Record fetch failed: %s — continuing without floor data",
                    e,
                )
                all_floor_remarks = {}

        if fetch_only:
            logger.info("=== FETCH COMPLETE (fetch-only mode) ===")
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
        try:
            classified_bills = await classify_all_bills(bills_data, db_session=db)
            logger.info("Classified %d key bills", len(classified_bills))
        except Exception as e:
            logger.error("Bill classification failed: %s — continuing with empty bill list", e)
            classified_bills = []

        # Override LLM partyLeaning with computed party split from actual roll call data.
        # This is more reliable than LLM guesses — if 80%+ of one party votes the same
        # way, it's a party-line vote regardless of what the LLM thought.
        for bill in classified_bills:
            roll_call_data = roll_call_data_map.get(bill["billId"])
            if roll_call_data:
                computed_split = compute_party_split(roll_call_data)
                if computed_split:
                    bill["partyLeaning"] = computed_split

        # 3a.2 Classify recent roll call votes (embedding-based, zero LLM)
        classified_recent: list[dict] = []
        if recent_roll_calls:
            logger.info("Classifying %d recent votes (embedding-based)...", len(recent_roll_calls))
            classified_recent = await classify_recent_votes(
                recent_roll_calls, db_session=db
            )
            logger.info("Classified %d recent votes", len(classified_recent))

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
        try:
            embed_bills(all_bills_for_embedding)
            logger.info("Embedded %d bills in vector database", len(all_bills_for_embedding))
        except Exception as e:
            logger.error("Bill embedding failed: %s — vector search will be unavailable this run", e)

        # 3a.3 Hybrid donor classification (FEC metadata → rules → embeddings → LLM)
        logger.info("Collecting unique donors for hybrid classification...")
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

        _flush = get_llm_stats()
        pipeline_run.llm_calls = _flush["total_calls"]
        pipeline_run.cache_hits = _flush["cache_hits"]
        pipeline_run.cache_misses = _flush["cache_misses"]
        pipeline_run.elapsed_seconds = round(time.time() - start_time, 1)
        db.commit()

        # 3b. Prepare senator data for batch LLM analysis
        logger.info("Preparing senator data for batch analysis...")
        results: list[dict] = []
        success_count = 0
        fail_count = 0

        senator_prepared: list[dict] = []
        for senator in senators:
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
            except Exception as e:
                logger.error(
                    "  Prep failed for %s: %s", senator["name"], str(e)
                )
                fail_count += 1
                results.append(senator)

        _flush = get_llm_stats()
        pipeline_run.llm_calls = _flush["total_calls"]
        pipeline_run.cache_hits = _flush["cache_hits"]
        pipeline_run.cache_misses = _flush["cache_misses"]
        pipeline_run.elapsed_seconds = round(time.time() - start_time, 1)
        db.commit()

        logger.info("Processing %d senators (1 LLM call each)...", len(senator_prepared))

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

                approval = approval_data.get(senator["id"])
                if approval:
                    result["approvalRating"] = approval.get("approve")
                    result["disapprovalRating"] = approval.get("disapprove")
                    result["approvalSource"] = approval.get("source")

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
            except Exception:
                logger.exception("  Failed for %s", senator["name"])
                fail_count += 1
                results.append(senator)
                pipeline_run.senators_failed = fail_count
                pipeline_run.senators_processed = success_count
                pipeline_run.elapsed_seconds = round(time.time() - start_time, 1)
                db.commit()

        # ========================================
        # PHASE 4: FINALIZE
        # ========================================
        pipeline_run.current_phase = "finalize"
        pipeline_run.elapsed_seconds = round(time.time() - start_time, 1)
        db.commit()
        logger.info("--- Phase 4: FINALIZE ---")

        llm_stats = get_llm_stats()
        elapsed = time.time() - start_time

        # Update PipelineRun record
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
