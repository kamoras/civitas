"""
Pipeline orchestrator -- full pipeline flow ported from scripts/pipeline/index.mjs.

The 4 phases: FETCH -> TRANSFORM -> ANALYZE -> ASSEMBLE+SAVE

Adapts to use database (SQLAlchemy session) instead of file writes,
using senator_service.upsert_senator() to save results and PipelineRun
records to track progress.
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

# Transform modules
from app.pipeline.transform.normalize_finance import normalize_finance
from app.pipeline.transform.normalize_members import normalize_members
from app.pipeline.transform.normalize_votes import (
    extract_senator_vote,
    find_senate_roll_call,
    normalize_recent_votes,
    normalize_votes,
)

# Analyze modules
from app.pipeline.analyze.bill_analyzer import classify_all_bills, classify_recent_votes
from app.pipeline.analyze.cross_reference import analyze_senator_batch
from app.pipeline.analyze.industry_llm_classifier import classify_unknown_industries
from app.pipeline.analyze.ollama_client import get_llm_stats, reset_stats
from app.pipeline.analyze.pac_analyzer import analyze_pacs_batch
from app.pipeline.analyze.platform_analyzer import analyze_platform_batch
from app.pipeline.analyze.score_calculator import calculate_scores
from app.pipeline.analyze.vote_summarizer import select_key_votes_batch

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
        "score_corporate_funding": corruption.get("constituentFunding", 0),
        "score_lobbyist_alignment": corruption.get("independenceIndex", 0),
        "score_industry_concentration": corruption.get("donorDiversity", 0),
        "score_flip_flop_index": corruption.get("promiseFulfillment", 0),
        "score_revolving_door": corruption.get("accountability", 0),
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


async def run_full_pipeline(
    senator_filter: str | None = None,
    fetch_only: bool = False,
) -> dict:
    """
    Full pipeline implementation -- ported from scripts/pipeline/index.mjs.

    Args:
        senator_filter: Optional name/id filter to process a single senator.
        fetch_only: If True, stop after fetch phase (no LLM analysis).

    Returns:
        Dict with pipeline run stats.
    """
    start_time = time.time()
    reset_stats()

    db: Session = SessionLocal()

    # Create PipelineRun record
    pipeline_run = PipelineRun(
        started_at=datetime.utcnow(),
        status="running",
    )
    db.add(pipeline_run)
    db.commit()

    try:
        logger.info("=== MODERN PUNK DATA PIPELINE ===")
        if senator_filter:
            logger.info("Single senator: %s", senator_filter)
        if fetch_only:
            logger.info("Fetch only mode -- no LLM analysis")

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

            # 1c. Dynamically discover significant bills
            logger.info("Discovering significant bills...")
            discovered_bills = await fetch_significant_bills(client, db)
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

            # 1d.2 Fetch recent roll calls from Senate.gov
            logger.info("Fetching recent Senate roll calls...")
            recent_roll_calls = await fetch_recent_roll_calls(client, db)
            # Build a map for recent roll calls keyed by document name
            recent_rc_map: dict[str, dict] = {}
            for rc in recent_roll_calls:
                rc_id = rc.get("documentName") or f"Roll-{rc['rollNumber']}"
                recent_rc_map[rc_id] = rc
            logger.info("Fetched %d recent roll calls", len(recent_roll_calls))

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
        logger.info("--- Phase 3: ANALYZE ---")

        # 3a. Classify all key bills using LLM (with partyLeaning)
        logger.info("Classifying key bills...")
        classified_bills = await classify_all_bills(bills_data, db_session=db)
        logger.info("Classified %d key bills", len(classified_bills))

        # 3a.2 Classify recent roll call votes
        classified_recent: list[dict] = []
        if recent_roll_calls:
            logger.info("Classifying %d recent votes...", len(recent_roll_calls))
            classified_recent = await classify_recent_votes(
                recent_roll_calls, db_session=db
            )
            logger.info("Classified %d recent votes", len(classified_recent))

        # 3a.3 LLM industry classification for unknown donors
        logger.info("Classifying unknown industries via LLM...")
        # Collect all org names classified as "OTHER" across all senators
        all_other_orgs: set[str] = set()

        # 3b. Prepare senator data for batch LLM analysis
        logger.info("Preparing senator data for batch analysis...")
        results: list[dict] = []
        success_count = 0
        fail_count = 0

        # Build per-senator data (finance + votes) without LLM calls
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
                    )
                    # Collect "OTHER" industries for LLM reclassification
                    for ind in funding.get("industryBreakdown", []):
                        if ind.get("industry") == "OTHER":
                            all_other_orgs.add(ind.get("name", ""))
                else:
                    funding = senator.get("funding", {})

                # Extract senator's last name for vote matching
                name_parts = senator["name"].split()
                last_name = name_parts[-1] if name_parts else ""

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
                # contribute to proCorporateVotes in normalize_votes
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
                # so all tracked votes count toward proCorporateVotes
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

        # Reclassify "OTHER" industries using LLM
        if all_other_orgs:
            all_other_orgs.discard("")
            logger.info(
                "Reclassifying %d unknown industries via LLM...",
                len(all_other_orgs),
            )
            reclassified = await classify_unknown_industries(
                list(all_other_orgs), db_session=db
            )
            # Apply reclassifications to prepared senator data
            for prepared in senator_prepared:
                breakdown = prepared["funding"].get("industryBreakdown", [])
                new_breakdown: dict[str, dict] = {}
                for ind in breakdown:
                    industry = ind["industry"]
                    if industry == "OTHER" and ind["name"] in reclassified:
                        new_industry = reclassified[ind["name"]]
                        if new_industry != "OTHER":
                            industry = new_industry
                    existing = new_breakdown.get(industry)
                    if existing:
                        existing["total"] += ind["total"]
                    else:
                        new_breakdown[industry] = {
                            "industry": industry,
                            "name": industry.replace("_", " "),
                            "total": ind["total"],
                            "percentage": ind["percentage"],
                        }
                # Recalculate percentages
                total = sum(i["total"] for i in new_breakdown.values())
                for ind in new_breakdown.values():
                    ind["percentage"] = (
                        round(ind["total"] / total * 100) if total > 0 else 0
                    )
                prepared["funding"]["industryBreakdown"] = sorted(
                    new_breakdown.values(),
                    key=lambda x: x["total"],
                    reverse=True,
                )[:8]
            logger.info(
                "Reclassified %d/%d industries",
                sum(1 for v in reclassified.values() if v != "OTHER"),
                len(reclassified),
            )

        # Batch senators into groups of 10
        BATCH_SIZE = 3
        batches: list[list[dict]] = []
        for i in range(0, len(senator_prepared), BATCH_SIZE):
            batches.append(senator_prepared[i : i + BATCH_SIZE])
        logger.info(
            "Processing %d senators in %d LLM batches (%d/batch)...",
            len(senator_prepared),
            len(batches),
            BATCH_SIZE,
        )

        for batch_idx, batch in enumerate(batches):
            logger.info(
                "Batch %d/%d: %s",
                batch_idx + 1,
                len(batches),
                ", ".join(b["senator"]["name"] for b in batch),
            )

            # Build batch input for analyze_senator_batch
            batch_input = [
                {
                    "senator": b["senator"],
                    "donors": (b["funding"].get("topDonors") or []),
                    "keyVotes": (b["votingRecord"].get("keyVotes") or []),
                }
                for b in batch
            ]

            batch_results = await analyze_senator_batch(
                batch_input, db_session=db
            )

            # 3c. Select key votes for this batch
            all_votes_per_senator = [
                (b["votingRecord"].get("keyVotes") or [])
                + (b["votingRecord"].get("recentVotes") or [])
                for b in batch
            ]
            key_vote_input = [
                {
                    "senator": b["senator"],
                    "allVotes": all_votes_per_senator[i],
                }
                for i, b in enumerate(batch)
            ]
            key_vote_results = await select_key_votes_batch(
                key_vote_input, db_session=db
            )

            # 3d. PAC analysis for this batch
            pac_input = [
                {
                    "senatorId": b["senator"]["id"],
                    "donors": b["funding"].get("topDonors", []),
                }
                for b in batch
            ]
            pac_results = await analyze_pacs_batch(pac_input, db_session=db)

            # 3e. Platform analysis for this batch
            platform_input = [
                {
                    "senator": b["senator"],
                    "allVotes": all_votes_per_senator[i],
                }
                for i, b in enumerate(batch)
            ]
            platform_results = await analyze_platform_batch(
                platform_input, db_session=db
            )

            # Process each senator's result
            for i, prepared in enumerate(batch):
                senator = prepared["senator"]
                funding = prepared["funding"]
                voting_record = prepared["votingRecord"]
                analysis = batch_results[i]
                kv_selection = key_vote_results[i] if i < len(key_vote_results) else {}
                pac_data = pac_results[i] if i < len(pac_results) else {}
                platform_data = platform_results[i] if i < len(platform_results) else {}

                try:
                    # Merge cross-reference analysis into voting record
                    all_votes = analysis.get("keyVotes") or voting_record["keyVotes"]

                    # Split votes: LLM-selected ones become "key", rest stay as they are
                    key_vote_ids = set(kv_selection.get("keyVoteIds", []))
                    reasoning_map = kv_selection.get("reasoning", {})

                    final_key_votes = []
                    final_recent_votes = list(voting_record.get("recentVotes", []))

                    for v in all_votes:
                        if v["billId"] in key_vote_ids:
                            v["voteCategory"] = "key"
                            v["keyVoteReasoning"] = reasoning_map.get(v["billId"])
                            final_key_votes.append(v)
                        else:
                            v["voteCategory"] = "recent"
                            final_recent_votes.append(v)

                    # If LLM didn't select any key votes, promote the first 3-5
                    if not final_key_votes and all_votes:
                        for v in all_votes[:min(5, len(all_votes))]:
                            v["voteCategory"] = "key"
                            final_key_votes.append(v)
                        final_recent_votes = [
                            v for v in final_recent_votes
                            if v["billId"] not in {kv["billId"] for kv in final_key_votes}
                        ]

                    voting_record["keyVotes"] = final_key_votes
                    voting_record["recentVotes"] = final_recent_votes
                    voting_record["votingSummary"] = kv_selection.get("votingSummary", "")

                    lobbying_matches = analysis.get("lobbyingMatches", [])

                    # Calculate scores using the flip-flop score from the batch
                    temp_senator = {
                        **senator,
                        "funding": funding,
                        "votingRecord": voting_record,
                        "lobbyingMatches": lobbying_matches,
                    }
                    corruption_score = calculate_scores(
                        temp_senator,
                        {"flipFlopScore": analysis.get("flipFlopScore", 25)},
                    )

                    nickname = analysis.get("punkNickname", "TBD")

                    # Enrich donors with PAC analysis
                    if pac_data.get("donors"):
                        funding["topDonors"] = pac_data["donors"]

                    # Assemble final record
                    result = build_senator(
                        senator,
                        funding,
                        voting_record,
                        lobbying_matches,
                        corruption_score,
                        nickname,
                    )

                    # Add platform analysis
                    result["campaignPromises"] = platform_data.get("campaignPromises", [])
                    result["platformSummary"] = platform_data.get("platformSummary", "")

                    results.append(result)
                    success_count += 1

                    # Write immediately so the UI reflects progress batch-by-batch
                    upsert_senator(db, result)

                    weighted_score = round(
                        corruption_score["constituentFunding"] * 0.3
                        + corruption_score["promiseFulfillment"] * 0.3
                        + corruption_score["independenceIndex"] * 0.2
                        + corruption_score["donorDiversity"] * 0.1
                        + corruption_score["accountability"] * 0.1
                    )
                    logger.info(
                        '  %s: score %d/100 -- "%s"',
                        senator["name"],
                        weighted_score,
                        nickname,
                    )
                except Exception as e:
                    logger.error(
                        "  Failed for %s: %s", senator["name"], str(e)
                    )
                    fail_count += 1
                    results.append(senator)

            # Commit after each batch so the UI reflects progress in real time
            db.commit()

        # ========================================
        # PHASE 4: FINALIZE
        # ========================================
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

    except Exception as e:
        logger.error("Pipeline failed: %s", str(e))
        pipeline_run.status = "failed"
        pipeline_run.completed_at = datetime.utcnow()
        pipeline_run.error_message = str(e)
        pipeline_run.elapsed_seconds = round(time.time() - start_time, 1)
        db.commit()
        raise
    finally:
        db.close()
