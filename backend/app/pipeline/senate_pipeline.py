"""
Pipeline orchestrator — unified data pipeline.

The 7 phases:
  1. FETCH      — congress, FEC, platforms, floor remarks
  2. TRANSFORM  — normalize members, votes, finance
  3. ANALYZE    — classify bills/donors, cross-reference, score
  4. EXPLORE    — ingest government documents for semantic search
  5. JUSTICES   — fetch and score Supreme Court justices
  6. PRESIDENTS — fetch and score presidential records
  7. FINALIZE   — persist stats and mark complete

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
    PipelineStatus,
    PromiseAlignment,
    ScoreSnapshot,
    Senator,
    SponsoredBill,
)

# Fetch modules
from app.pipeline.fetch.congress import (
    extract_official_title,
    fetch_bill,
    fetch_bill_actions,
    fetch_bill_cosponsors,
    fetch_bill_summaries,
    fetch_bill_titles,
    fetch_member_detail,
    fetch_member_sponsored,
    fetch_recent_roll_calls,
    fetch_roll_call_vote,
    fetch_senator_platform_text,
    fetch_senators,
    fetch_significant_bills,
)
from app.pipeline.fetch.fec import (
    compute_recent_election_cycles,
    fetch_aggregated_contributors,
    fetch_candidate_committees,
    fetch_candidate_financials,
    fetch_committee_receipts,
    fetch_committee_type,
    fetch_outside_spending,
    fetch_pac_receipts,
    find_candidate,
)
from app.pipeline.fetch.govinfo import fetch_bill_text
from app.pipeline.fetch.lda import enrich_lobbying_matches_with_lda
from app.pipeline.progress_tracker import ProgressTracker
# Transform modules
from app.pipeline.transform.normalize_finance import normalize_finance
from app.pipeline.transform.normalize_members import normalize_members
from app.pipeline.transform.normalize_votes import (
    compute_party_split,
    compute_party_vote_split,
    extract_senator_vote,
    find_senate_roll_call,
    normalize_recent_votes,
    normalize_votes,
    opposing_party_unity,
)

# Analyze modules
from app.pipeline.analyze.bill_analyzer import classify_all_bills, classify_recent_votes, clear_bill_embedding_cache
from app.pipeline.analyze.bill_learning import clear_reference_cache
from app.pipeline.analyze.party_platform import clear_platform_cache, initialize_platform_embeddings
from app.pipeline.vector_store import (
    check_model_version,
    embed_bills,
    get_chroma_client,
    invalidate_on_model_change,
    _write_model_version,
)
from app.pipeline.analyze.cross_reference import analyze_senator_batch, precompute_senator_analysis
from app.pipeline.analyze.donor_classifier_ai import classify_donors_hybrid
from app.pipeline.analyze.ollama_client import get_llm_stats, reset_client, reset_stats
from app.pipeline.analyze.policy_alignment import clear_alignment_cache
from app.pipeline.analyze.score_calculator import calculate_confidence, calculate_scores

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
    ("fetch_cosponsors",     "fetch",     "Fetch bill cosponsors"),
    ("fetch_sponsored",      "fetch",     "Fetch sponsored legislation"),
    ("fetch_official_titles", "fetch",    "Fetch official bill titles"),
    ("fetch_fec",            "fetch",     "Fetch FEC financial data"),
    ("fetch_platforms",      "fetch",     "Fetch platform text"),
    ("classify_bills",       "analyze",   "Classify bills"),
    ("classify_recent",      "analyze",   "Classify recent votes"),
    ("embed_bills",          "analyze",   "Embed bills in vector DB"),
    ("classify_donors",      "analyze",   "Classify donors"),
    ("prepare_senators",     "analyze",   "Prepare senator data"),
    ("sponsorship_analysis", "analyze",   "Sponsorship leadership & ideology (SVD/PageRank)"),
    ("analyze_senators",     "analyze",   "Analyze senators"),
    ("finalize",             "finalize",  "Finalize & save"),
]

STALE_PIPELINE_TIMEOUT_S = 43200  # 12 hours
MAX_SIGNIFICANT_BILLS = 100
RECENT_RC_COUNT_PER_SESSION = 100
RECENT_RC_SESSIONS = 2
MIN_CONGRESS_FOR_BILL_TITLES = 116




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
        "name": data.get("name") or "",
        "state": data.get("state") or "",
        "party": data.get("party") or "I",
        "years_in_office": data.get("yearsInOffice") or 0,
        "initials": data.get("initials") or "",
        "score_funding_independence": corruption.get("fundingIndependence") or 0,
        "score_promise_persistence": corruption.get("promisePersistence") or 0,
        "score_independent_voting": corruption.get("independentVoting") or 0,
        "score_funding_diversity": corruption.get("fundingDiversity") or 0,
        "score_legislative_effectiveness": corruption.get("legislativeEffectiveness") or 0,
        "total_raised": funding.get("totalRaised") or 0,
        "total_from_pacs": funding.get("totalFromPACs") or 0,
        "small_donor_percentage": funding.get("smallDonorPercentage") or 0,
        "outside_spending_for": funding.get("outsideSpendingFor"),
        "voting_summary": (data.get("votingRecord") or {}).get("votingSummary") or "",
        "platform_summary": data.get("platformSummary") or "",
        "website_url": data.get("officialWebsiteUrl") or "",
        "contact_form_url": data.get("contactFormUrl") or "",
        "office_phone": data.get("officePhone") or "",
        "office_address": data.get("officeAddress") or "",
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
                name=donor_data.get("name") or "Unknown",
                total=donor_data.get("total") or 0,
                type=donor_data.get("type") or "PAC",
                industry=donor_data.get("industry") or "OTHER",
                rank=rank,
                pac_sponsor=donor_data.get("pacSponsor"),
                pac_industry=donor_data.get("pacIndustry"),
                pac_analysis=donor_data.get("pacAnalysis"),
                committee_type=donor_data.get("committeeType"),
            )
        )

    # Add industry donations
    for ind_data in funding.get("industryBreakdown", []):
        db.add(
            IndustryDonation(
                senator_id=senator_id,
                industry=ind_data.get("industry") or "OTHER",
                name=ind_data.get("name") or "Other",
                total=ind_data.get("total") or 0,
                percentage=ind_data.get("percentage") or 0,
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
                bill_name=vote_data.get("billName") or "Unknown Bill",
                bill_id=vote_data.get("billId") or "",
                date=vote_data.get("date") or "",
                vote=vote_data.get("vote") or "Not Voting",
                policy_area=vote_data.get("policyArea") or "PROCEDURAL",
                policy_areas=json.dumps(vote_data.get("policyAreas") or []),
                party_alignment_weight=vote_data.get("partyAlignmentWeight") or 0.0,
                stance=vote_data.get("stance") or "neutral",
                description=vote_data.get("description") or "",
                party_leaning=vote_data.get("partyLeaning"),
                opposing_party_unity_pct=vote_data.get("opposingPartyUnityPct"),
                voted_with_party=vote_data.get("votedWithParty"),
                vote_category=vote_data.get("voteCategory") or category,
                key_vote_reasoning=vote_data.get("keyVoteReasoning"),
            )
        )

    # Add lobbying matches
    for match_data in data.get("lobbyingMatches", []):
        db.add(
            LobbyingMatch(
                senator_id=senator_id,
                lobbyist_org=match_data.get("lobbyistOrg") or "Unknown",
                industry=match_data.get("industry") or "OTHER",
                lobbying_spend=match_data.get("lobbyingSpend") or 0,
                donation_to_senator=match_data.get("donationToSenator") or 0,
                bills_influenced=json.dumps(
                    match_data.get("billsInfluenced") or []
                ),
                senator_vote_aligned=match_data.get("senatorVoteAligned"),
                is_consensus_vote=match_data.get("isConsensusVote"),
                description=match_data.get("description") or "",
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
                promise_text=promise_data.get("promiseText") or "",
                category=promise_data.get("category") or "other",
                alignment=promise_data.get("alignment") or PromiseAlignment.UNCLEAR,
                related_votes=json.dumps(
                    promise_data.get("relatedVotes") or []
                ),
                related_bills=json.dumps(
                    promise_data.get("relatedBills") or []
                ),
                analysis=promise_data.get("analysis") or "",
                party_alignment=promise_data.get("partyAlignment"),
            )
        )

    # Add sponsored bills
    db.query(SponsoredBill).filter(
        SponsoredBill.senator_id == senator_id
    ).delete()
    for sp_data in data.get("sponsoredBills", []):
        db.add(
            SponsoredBill(
                senator_id=senator_id,
                bill_id=sp_data.get("billId") or "",
                title=sp_data.get("title") or "",
                introduced_date=sp_data.get("introducedDate") or "",
                latest_action=sp_data.get("latestAction") or "",
                latest_action_date=sp_data.get("latestActionDate") or "",
                policy_area=sp_data.get("policyArea") or "",
                policy_areas=json.dumps(sp_data.get("policyAreas") or []),
                party_leaning=sp_data.get("partyLeaning"),
                congress=sp_data.get("congress") or 0,
                bill_type=sp_data.get("billType") or "",
                is_law=sp_data.get("isLaw") or False,
                stage=sp_data.get("stage") or "",
            )
        )

    # Save partisan depth profile as JSON on the senator row
    partisan_depth_data = data.get("partisanDepth")
    if partisan_depth_data and existing:
        existing.partisan_depth = json.dumps(partisan_depth_data)

    # Save sponsorship analysis scores (PageRank leadership + SVD ideology)
    if existing:
        ls = data.get("leadershipScore")
        existing.leadership_score = ls if ls is not None else None
        bs = data.get("bipartisanshipScore")
        existing.bipartisanship_score = bs if bs is not None else None
        ids = data.get("ideologyScore")
        existing.ideology_score = ids if ids is not None else None
        existing.sponsorship_description = data.get("sponsorshipDescription") or ""

    db.flush()


def _record_score_snapshots(db: Session) -> None:
    """Snapshot today's scores for all senators so we can compute trends."""
    from app.pipeline.analyze.score_calculator import ALGORITHM_VERSION, compute_overall_score

    today = datetime.utcnow().strftime("%Y-%m-%d")
    existing = db.query(ScoreSnapshot).filter(
        ScoreSnapshot.entity_type == "senator",
        ScoreSnapshot.date == today,
    ).first()
    if existing:
        db.query(ScoreSnapshot).filter(
            ScoreSnapshot.entity_type == "senator",
            ScoreSnapshot.date == today,
        ).delete()

    senators = db.query(Senator).all()
    for s in senators:
        db.add(ScoreSnapshot(
            entity_type="senator",
            entity_id=s.id,
            date=today,
            overall_score=compute_overall_score(s),
            score_1=s.score_funding_independence,
            score_2=s.score_promise_persistence,
            score_3=s.score_independent_voting,
            score_4=s.score_funding_diversity,
            score_5=s.score_legislative_effectiveness,
            algorithm_version=ALGORITHM_VERSION,
        ))
    db.commit()
    logger.info("Recorded score snapshots for %d senators on %s", len(senators), today)


def _acquire_pipeline_lock(db: Session) -> PipelineRun | None:
    """Atomically check for a running pipeline and create a new locked run.

    Uses the shared SQLite database so the lock works across blue/green
    containers.  Returns the new PipelineRun if the lock was acquired,
    or None if another pipeline is already running.
    """
    running = (
        db.query(PipelineRun)
        .filter(PipelineRun.status == PipelineStatus.RUNNING)
        .first()
    )
    if running:
        age = (datetime.utcnow() - running.started_at).total_seconds()
        if age > STALE_PIPELINE_TIMEOUT_S:
            running.status = PipelineStatus.STALE
            running.completed_at = datetime.utcnow()
            running.error_message = "Marked stale: exceeded 12-hour timeout"
            db.commit()
            logger.warning("Cleaned up stale pipeline run #%d (age: %ds)", running.id, int(age))
        else:
            return None

    pipeline_run = PipelineRun(started_at=datetime.utcnow(), status=PipelineStatus.RUNNING)
    db.add(pipeline_run)
    db.commit()
    return pipeline_run


def _compute_analysis_code_hash() -> str:
    """SHA-256 fingerprint of all analysis-relevant source files.

    Covers pipeline modules (analyze, transform, assemble, orchestrator,
    vector_store, cache) and config_definitions.py (weights, prototypes,
    industry codes).  Excludes fetch modules — raw data retrieval does not
    affect how that data is classified or scored.
    """
    import hashlib
    import pathlib

    app_dir = pathlib.Path(__file__).resolve().parent.parent  # app/
    paths: list[pathlib.Path] = []
    for p in sorted((app_dir / "pipeline").rglob("*.py")):
        if "/fetch/" not in str(p):
            paths.append(p)
    cfg = app_dir / "config_definitions.py"
    if cfg.exists():
        paths.append(cfg)

    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def _clear_analysis_artifacts(db: Session) -> None:
    """Purge analysis artifacts only when pipeline code has changed.

    Computes a SHA-256 fingerprint of all analysis source files and
    compares it to the fingerprint stored from the last pipeline run.

    Same code  → preserves learning store, analysis cache, and ChromaDB
                 reference corpus so the self-training system accumulates
                 knowledge across runs.
    Changed    → clears all three persistence layers so updated algorithms
                 start fresh without stale classifications or narratives.

    The API cache (raw Congress.gov / FEC / GovInfo responses) is never
    cleared — it reflects source data, not processing logic.
    """
    from app.models import AnalysisCache, ApiCache, LearnedClassification

    current_hash = _compute_analysis_code_hash()

    stored_entry = (
        db.query(ApiCache)
        .filter(ApiCache.tier == "_internal", ApiCache.cache_key == "analysis_code_hash")
        .first()
    )
    stored_hash = json.loads(stored_entry.data_json) if stored_entry else None

    if stored_hash == current_hash:
        logger.info(
            "Pipeline code unchanged (hash=%s) — preserving learning data",
            current_hash,
        )
        return

    n_analysis = db.query(AnalysisCache).delete()
    n_learned = db.query(LearnedClassification).delete()
    db.commit()

    n_bills = 0
    try:
        client = get_chroma_client()
        try:
            coll = client.get_collection(name="bills")
            n_bills = coll.count()
            client.delete_collection(name="bills")
        except Exception:
            logger.debug("No existing bills collection to clear")
    except Exception:
        logger.debug("ChromaDB not available for cache clear")

    from app.pipeline.cache import api_cache_set
    api_cache_set(db, "_internal", "analysis_code_hash", current_hash)

    logger.info(
        "Pipeline code changed (%s -> %s) — cleared %d cached LLM results, "
        "%d learned classifications, %d reference bills",
        stored_hash or "none", current_hash, n_analysis, n_learned, n_bills,
    )


def _build_analysis_input(prepared: dict, platform_texts: dict) -> dict:
    """Build the analysis input dict for a senator's embedding pre-computation."""
    senator = prepared["senator"]
    funding = prepared["funding"]
    voting_record = prepared["votingRecord"]
    all_votes = (voting_record.get("keyVotes") or []) + (
        voting_record.get("recentVotes") or []
    )
    return {
        "senator": senator,
        "donors": funding.get("topDonors", []),
        "industryBreakdown": funding.get("industryBreakdown", []),
        "keyVotes": voting_record.get("keyVotes", []),
        "allVotes": all_votes,
        "platformText": platform_texts.get(senator["id"], ""),
        "sponsoredBills": prepared.get("sponsoredBills", []),
    }


async def run_senate_pipeline(
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
    db: Session = SessionLocal()

    # Acquire the run lock BEFORE any global/DB mutation. In run 69 the
    # next night's scheduled trigger fired while the run was finalizing:
    # it reset the shared LLM counters (the run recorded llm_calls=0) and
    # purged analysis artifacts mid-write before hitting the lock check.
    pipeline_run = _acquire_pipeline_lock(db)
    if pipeline_run is None:
        logger.warning("Pipeline already running in another process — skipping")
        db.close()
        return {"status": "skipped", "reason": "already_running"}

    reset_stats()
    reset_client()

    # Clear in-memory caches from prior runs to bound memory usage.
    clear_alignment_cache()
    clear_bill_embedding_cache()
    clear_reference_cache()
    clear_platform_cache()
    from app.pipeline.transform.industry_classifier import clear_industry_embedding_cache
    clear_industry_embedding_cache()

    # Purge all analysis-derived data from prior runs so updated
    # algorithms always produce fresh results. Preserves the API cache
    # (raw Congress.gov / FEC / GovInfo responses) since those reflect
    # source data, not our processing logic.
    _clear_analysis_artifacts(db)

    # Verify embedding model version — invalidate stored embeddings on change
    if not check_model_version():
        invalidate_on_model_change(db_session=db)
    else:
        _write_model_version()

    # Build party platform centroids from seeds + accumulated bill data.
    # This implements Bayesian self-training: seed descriptions act as a
    # prior, and real bill data from previous runs updates the posterior.
    initialize_platform_embeddings(db)

    progress = ProgressTracker(pipeline_run, PIPELINE_STEPS, db, start_time)

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
            discovered_bills = await fetch_significant_bills(client, db, max_bills=MAX_SIGNIFICANT_BILLS)
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
                titles = await fetch_bill_titles(
                    client, db, bill_ref["congress"], bill_ref["type"], bill_ref["number"]
                )

                # Fetch full bill text from GovInfo
                full_text = await fetch_bill_text(
                    client, db, bill_ref["congress"], bill_ref["type"], bill_ref["number"]
                )

                if bill:
                    sponsors = bill.get("sponsors", [])
                    sponsor_party = None
                    sponsor_bioguide = None
                    if sponsors:
                        sponsor_party = sponsors[0].get("party")
                        sponsor_bioguide = sponsors[0].get("bioguideId")

                    official_title = extract_official_title(titles)
                    crs_policy_area = (bill.get("policyArea") or {}).get("name", "")
                    # actions[0] is the most recent (Congress.gov returns
                    # newest-first) — same "becameLaw"/"Public Law" check
                    # used for sponsored bills below, so a significant
                    # bill's cosponsorship-edge weight (sponsorship_analysis.
                    # _cosponsorship_edge_weight) reflects the same notion
                    # of "advanced" everywhere in the pipeline.
                    latest_action_text = (actions or [{}])[0].get("text", "") if actions else ""
                    is_law = (
                        "becamelaw" in latest_action_text.lower()
                        or "public law" in latest_action_text.lower()
                    )

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
                            "officialTitle": official_title,
                            "crsPolicyArea": crs_policy_area,
                            "fullText": full_text or "",
                            "actions": actions or [],
                            "sponsorParty": sponsor_party,
                            "sponsorBioguide": sponsor_bioguide,
                            "isLaw": is_law,
                            "latestAction": latest_action_text,
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

            # 1d.2 Fetch recent roll calls from Senate.gov, current congress only —
            # "current term" is defined as the current congress; see AGENTS.md.
            logger.info("Fetching recent Senate roll calls (multi-session)...")
            progress.begin("fetch_recent_rcs", total=RECENT_RC_SESSIONS)
            all_recent_roll_calls: list[dict] = []
            seen_roll_ids: set[str] = set()

            fetch_sessions = [
                (settings.CURRENT_CONGRESS, 1),      # e.g. 119th sess 1 (2025)
                (settings.CURRENT_CONGRESS, 2),      # 119th sess 2 (2026, if started)
            ]

            for sess_idx, (congress_num, session_num) in enumerate(fetch_sessions):
                session_rcs = await fetch_recent_roll_calls(
                    client, db,
                    congress=congress_num,
                    session_number=session_num,
                    count=RECENT_RC_COUNT_PER_SESSION,
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

            # 1d.3 Fetch cosponsors for significant bills.
            # Cosponsorship is a proactive signal of political alignment —
            # a senator chooses to endorse a bill by cosponsoring it. Combined
            # with the bill's sponsor party, this builds a per-senator
            # cosponsorship profile for caucus inference (Fowler 2006).
            logger.info("Fetching bill cosponsors for cosponsorship analysis...")
            progress.begin("fetch_cosponsors", total=len(bills_data))
            # bill_id → list of cosponsor dicts (each has bioguideId, party)
            cosponsors_map: dict[str, list[dict]] = {}
            for cs_idx, bill_ref in enumerate(bills_data):
                bill_id = bill_ref.get("billId", "")
                # Parse bill type and number from the composite ID (e.g. "S.123")
                parts = bill_id.split(".")
                if len(parts) == 2 and parts[1].isdigit():
                    cosponsors = await fetch_bill_cosponsors(
                        client, db,
                        bill_ref["congress"],
                        parts[0].lower(),
                        int(parts[1]),
                    )
                    if cosponsors:
                        cosponsors_map[bill_id] = cosponsors
                progress.update("fetch_cosponsors", done=cs_idx + 1)
            total_cosponsors = sum(len(v) for v in cosponsors_map.values())
            logger.info(
                "Cosponsors fetched for %d/%d bills (%d total cosponsorships)",
                len(cosponsors_map), len(bills_data), total_cosponsors,
            )
            progress.complete(
                "fetch_cosponsors",
                detail=f"{len(cosponsors_map)} bills, {total_cosponsors} cosponsorships",
            )

            # Build per-senator cosponsorship profiles: for each senator, count
            # how many D-sponsored vs R-sponsored bills they cosponsored.
            cosponsorship_profiles: dict[str, dict] = {}
            for bill_ref in bills_data:
                bill_id = bill_ref.get("billId", "")
                sponsor_party = bill_ref.get("sponsorParty")
                if not sponsor_party or sponsor_party not in ("D", "R"):
                    continue
                for cosponsor in cosponsors_map.get(bill_id, []):
                    bio_id = cosponsor.get("bioguideId", "")
                    if not bio_id:
                        continue
                    profile = cosponsorship_profiles.setdefault(
                        bio_id, {"d_cosponsored": 0, "r_cosponsored": 0},
                    )
                    if sponsor_party == "D":
                        profile["d_cosponsored"] += 1
                    else:
                        profile["r_cosponsored"] += 1
            logger.info(
                "Cosponsorship profiles built for %d senators",
                len(cosponsorship_profiles),
            )

            # 1d.4 Fetch each senator's sponsored legislation.
            logger.info("Fetching sponsored legislation...")
            progress.begin("fetch_sponsored", total=len(senators))
            sponsored_map: dict[str, list[dict]] = {}
            for sp_idx, senator in enumerate(senators):
                bio_id = senator.get("bioguideId", "")
                if bio_id:
                    raw_sponsored = await fetch_member_sponsored(client, db, bio_id)
                    if raw_sponsored:
                        sponsored_map[bio_id] = raw_sponsored
                progress.update("fetch_sponsored", done=sp_idx + 1)
            total_sponsored = sum(len(v) for v in sponsored_map.values())
            logger.info(
                "Sponsored legislation fetched for %d senators (%d total bills)",
                len(sponsored_map), total_sponsored,
            )
            progress.complete(
                "fetch_sponsored",
                detail=f"{len(sponsored_map)} senators, {total_sponsored} bills",
            )

            # 1d.5 Fetch official titles for sponsored bills with short names.
            # Bills named after people (e.g., "Jaime's Law") or acronyms
            # (e.g., "ARMAS Act") carry no policy signal for the embedding
            # classifier. The official title from the titles endpoint
            # (e.g., "A bill to prevent the purchase of ammunition...")
            # provides the semantic content needed for accurate classification.
            min_congress_for_titles = MIN_CONGRESS_FOR_BILL_TITLES
            short_title_bills: dict[str, tuple[int, str, int]] = {}
            for sp_bills in sponsored_map.values():
                for sp in sp_bills:
                    title = sp.get("title", "")
                    bill_type = sp.get("type", "")
                    bill_number = sp.get("number", "")
                    congress = sp.get("congress", 0)
                    if (
                        title and len(title) < 50
                        and bill_type and bill_number
                        and congress >= min_congress_for_titles
                    ):
                        bid = f"{bill_type}.{bill_number}"
                        if bid not in short_title_bills:
                            short_title_bills[bid] = (
                                congress, bill_type.lower(), int(bill_number),
                            )

            official_titles_map: dict[str, str] = {}
            if short_title_bills:
                logger.info(
                    "Fetching official titles for %d short-title sponsored bills...",
                    len(short_title_bills),
                )
                # Tracked as its own progress step: in run 69 this loop ran
                # for 80 minutes between fetch_sponsored and fetch_fec with
                # nothing in progress_detail to show for it.
                progress.begin("fetch_official_titles", total=len(short_title_bills))
                for ot_idx, (bid, (cg, bt, bn)) in enumerate(short_title_bills.items()):
                    titles = await fetch_bill_titles(client, db, cg, bt, bn)
                    official = extract_official_title(titles)
                    if official:
                        official_titles_map[bid] = official
                    progress.update("fetch_official_titles", done=ot_idx + 1)
                progress.complete(
                    "fetch_official_titles",
                    detail=f"{len(official_titles_map)}/{len(short_title_bills)} titles",
                )
                logger.info(
                    "Official titles fetched for %d/%d bills",
                    len(official_titles_map), len(short_title_bills),
                )
            else:
                progress.skip("fetch_official_titles", detail="no short-title bills")

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

                # Match the receipt-detail and outside-spending windows to
                # the receipt-totals window (normalize_finance sums only the
                # most recent election, one deduped totals row).
                recent_cycles = compute_recent_election_cycles(financials)

                receipts: list = []
                pac_receipts_data: list = []
                aggregated: list = []
                if committee_id:
                    receipts = await fetch_committee_receipts(
                        client, db, committee_id, cycles=recent_cycles
                    )
                    pac_receipts_data = await fetch_pac_receipts(
                        client, db, committee_id, cycles=recent_cycles
                    )
                    aggregated = await fetch_aggregated_contributors(
                        client, db, committee_id, cycles=recent_cycles
                    )

                outside = await fetch_outside_spending(
                    client, db, candidate_id, cycles=recent_cycles
                )
                logger.info(
                    "Outside spending for %s: $%.0f",
                    senator["name"],
                    outside.get("totalFor", 0),
                )

                fec_data[senator["id"]] = {
                    "candidate": candidate,
                    "financials": financials,
                    "receipts": receipts,
                    "pacReceipts": pac_receipts_data,
                    "aggregated": aggregated,
                    "outsideSpending": outside,
                }
                progress.update("fetch_fec", done=fec_idx + 1)
            logger.info(
                "FEC data fetched for %d/%d senators",
                len(fec_data),
                len(senators),
            )
            progress.complete("fetch_fec", detail=f"{len(fec_data)}/{len(senators)} matched")

            # 1e-2. Resolve PAC committee types (multicandidate vs not) once per
            # unique contributing PAC across the whole run — feeds the
            # PAC-utilization signal in _funding_independence_core. A single
            # global pass here (rather than a per-senator lookup) means a PAC
            # that gives to 30 different senators is looked up exactly once,
            # not 30 times, on top of fetch_committee_type's own long-TTL cache.
            pac_committee_ids: set[str] = set()
            for fec in fec_data.values():
                for r in fec.get("pacReceipts") or []:
                    if r.get("entity_type") == "COM" and r.get("contributor_id"):
                        pac_committee_ids.add(r["contributor_id"])
            logger.info("Resolving committee type for %d unique contributing PACs...", len(pac_committee_ids))
            committee_type_map: dict[str, str | None] = {}
            for cid in pac_committee_ids:
                committee_type_map[cid] = await fetch_committee_type(client, db, cid)

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

        if fetch_only:
            logger.info("=== FETCH COMPLETE (fetch-only mode) ===")
            for sk in ("classify_bills", "classify_recent", "embed_bills",
                        "classify_donors", "prepare_senators", "analyze_senators",
                        "finalize"):
                progress.skip(sk, detail="fetch-only mode")
            elapsed = time.time() - start_time
            pipeline_run.status = PipelineStatus.COMPLETED
            pipeline_run.completed_at = datetime.utcnow()
            pipeline_run.elapsed_seconds = elapsed
            db.commit()
            return {
                "status": PipelineStatus.COMPLETED,
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

        # Invalidate stale bill classifications from prior runs where the
        # bill was classified with insufficient text (e.g., person-name
        # titles like "Jaime's Law" with no summary/official title).  This
        # forces reclassification with the richer text we now provide.
        from app.pipeline.analyze.bill_learning import invalidate_thin_classifications
        n_invalidated = invalidate_thin_classifications(db)
        if n_invalidated:
            clear_reference_cache()

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
                split = compute_party_vote_split(roll_call_data)
                vote_split = split["label"] if split else None
                bill["partyLeaning"] = refine_with_vote_data(
                    bill.get("partyLeaning", "bipartisan"), vote_split,
                )
                if split and bill["partyLeaning"] in ("R", "D"):
                    bill["opposingPartyUnityPct"] = opposing_party_unity(
                        bill["partyLeaning"], split["r_yea_pct"], split["d_yea_pct"],
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
                    logger.debug("Sponsor alignment failed for %s", bill_id, exc_info=True)

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

        # Refine content-based party alignment for recent votes with vote data.
        # Uses the same blended approach as key bills: content analysis is
        # the primary signal, vote tallies validate or adjust.
        for rc in classified_recent:
            rc_id = rc.get("billId", "")
            roll_call_data = recent_rc_map.get(rc_id)
            if roll_call_data:
                split = compute_party_vote_split(roll_call_data)
                computed_split = split["label"] if split else None
                if computed_split:
                    rc["partyLeaning"] = refine_with_vote_data(
                        rc.get("partyLeaning", "bipartisan"), computed_split,
                    )
                    if rc["partyLeaning"] in ("R", "D"):
                        rc["opposingPartyUnityPct"] = opposing_party_unity(
                            rc["partyLeaning"], split["r_yea_pct"], split["d_yea_pct"],
                        )

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

        # 3a.3 Hybrid donor classification (FEC metadata → rules → embeddings → kNN)
        logger.info("Collecting unique donors for hybrid classification...")
        progress.begin("classify_donors")
        all_donor_entries: list[dict] = []
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
                if employer:
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
                        outside_spending=fec.get("outsideSpending"),
                        committee_type_map=committee_type_map,
                    )
                else:
                    funding = senator.get("funding", {})

                # Use the pre-extracted last name that handles multi-word
                # surnames (e.g. "Cortez Masto", "Van Hollen") and accents.
                last_name = senator.get("lastNameForVoteMatch", "")
                if not last_name:
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
                senator_cosponsor_profile = cosponsorship_profiles.get(
                    senator.get("bioguideId", ""),
                )
                voting_record = normalize_votes(
                    senator.get("bioguideId", ""),
                    all_classified,
                    senator_votes,
                    senator_party=senator.get("party", "I"),
                    cosponsorship_profile=senator_cosponsor_profile,
                )

                # Normalize recent votes for display in the UI
                # Pass effective_party so Independents get correct party alignment
                recent_senator_votes = normalize_recent_votes(
                    classified_recent,
                    recent_rc_map,
                    last_name,
                    senator["state"],
                    senator.get("party", "I"),
                    effective_party=voting_record.get("effectiveParty"),
                )
                voting_record["recentVotes"] = recent_senator_votes

                # Collect this senator's sponsored bills
                bio_id = senator.get("bioguideId", "")
                raw_sponsored = sponsored_map.get(bio_id, [])
                senator_sponsored: list[dict] = []
                for sp in raw_sponsored:
                    title = sp.get("title", "")
                    if not title:
                        continue
                    bill_type = sp.get("type", "")
                    bill_number = sp.get("number", "")
                    bill_id = f"{bill_type}.{bill_number}" if bill_type and bill_number else ""
                    latest = sp.get("latestAction") or {}
                    pa = sp.get("policyArea") or {}
                    became_law = "becameLaw" in (latest.get("text") or "").lower() or (
                        "Public Law" in (latest.get("text") or "")
                    )
                    senator_sponsored.append({
                        "billId": bill_id,
                        "title": title,
                        "officialTitle": official_titles_map.get(bill_id, ""),
                        "introducedDate": sp.get("introducedDate", ""),
                        "latestAction": latest.get("text", ""),
                        "latestActionDate": latest.get("actionDate", ""),
                        "policyArea": pa.get("name", ""),
                        "congress": sp.get("congress", 0),
                        "billType": bill_type,
                        "isLaw": became_law,
                    })

                senator_prepared.append(
                    {
                        "senator": senator,
                        "funding": funding,
                        "votingRecord": voting_record,
                        "sponsoredBills": senator_sponsored,
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

        # 3f. Enrich cosponsorship data with senators' own sponsored bills
        # The significant-bills cosponsorship matrix (33 bills) is too sparse
        # for 100 senators. Sample each senator's recent sponsored bills
        # (118th-119th Congress) and fetch their cosponsors to build a richer
        # senator-senator cosponsorship graph for SVD ideology and PageRank.
        sponsored_bills_for_cosponsor: list[dict] = []
        max_per_senator = 10
        min_congress = 118
        for prep in senator_prepared:
            senator = prep["senator"]
            bio_id = senator.get("bioguideId", "")
            party = senator.get("party", "")
            if not bio_id:
                continue
            recent_sp = [
                sp for sp in prep.get("sponsoredBills", [])
                if sp.get("congress", 0) >= min_congress
                and sp.get("billId")
            ]
            for sp in recent_sp[:max_per_senator]:
                sponsored_bills_for_cosponsor.append({
                    "billId": sp["billId"],
                    "congress": sp["congress"],
                    "sponsorBioguide": bio_id,
                    "sponsorParty": party,
                    "isLaw": sp.get("isLaw", False),
                    "latestAction": sp.get("latestAction", ""),
                })

        if sponsored_bills_for_cosponsor:
            logger.info(
                "Enriching cosponsorship data: fetching cosponsors for %d recent sponsored bills...",
                len(sponsored_bills_for_cosponsor),
            )
            progress.begin("fetch_sponsored_cosponsors", total=len(sponsored_bills_for_cosponsor))
            enriched_count = 0
            async with httpx.AsyncClient() as enrich_client:
                for sc_idx, sp_bill in enumerate(sponsored_bills_for_cosponsor):
                    bill_id = sp_bill["billId"]
                    if bill_id in cosponsors_map:
                        progress.update("fetch_sponsored_cosponsors", done=sc_idx + 1)
                        continue
                    parts = bill_id.split(".")
                    if len(parts) == 2 and parts[1].isdigit():
                        cosponsors = await fetch_bill_cosponsors(
                            enrich_client, db,
                            sp_bill["congress"],
                            parts[0].lower(),
                            int(parts[1]),
                        )
                        if cosponsors:
                            cosponsors_map[bill_id] = cosponsors
                            enriched_count += 1
                    progress.update("fetch_sponsored_cosponsors", done=sc_idx + 1)
            enriched_total = sum(len(v) for v in cosponsors_map.values())
            logger.info(
                "Cosponsorship enrichment: added %d bills (%d total cosponsorships now)",
                enriched_count, enriched_total,
            )
            progress.complete(
                "fetch_sponsored_cosponsors",
                detail=f"{enriched_count} new bills, {enriched_total} total cosponsorships",
            )
            all_bills_for_analysis = bills_data + sponsored_bills_for_cosponsor
        else:
            all_bills_for_analysis = bills_data

        # 3f. Sponsorship analysis: PageRank leadership + SVD ideology
        from app.pipeline.analyze.sponsorship_analysis import (
            compute_bipartisanship_scores,
            compute_ideology_scores,
            compute_leadership_scores,
            describe_senator_position,
        )
        progress.begin("sponsorship_analysis")
        senator_bio_ids = {
            s.get("bioguideId", "")
            for s in senators
            if s.get("bioguideId")
        }
        senator_party_map = {
            s.get("bioguideId", ""): s.get("party", "")
            for s in senators
            if s.get("bioguideId")
        }
        leadership_scores = compute_leadership_scores(
            all_bills_for_analysis, cosponsors_map, senator_bio_ids, senator_party_map,
        )
        ideology_scores = compute_ideology_scores(
            all_bills_for_analysis, cosponsors_map, senator_bio_ids, senator_party_map,
        )
        bipartisanship_scores = compute_bipartisanship_scores(
            all_bills_for_analysis, cosponsors_map, senator_party_map,
        )
        logger.info(
            "Sponsorship analysis: %d leadership, %d ideology, %d bipartisanship scores",
            len(leadership_scores), len(ideology_scores), len(bipartisanship_scores),
        )
        progress.complete(
            "sponsorship_analysis",
            detail=f"{len(leadership_scores)} leadership, {len(ideology_scores)} ideology",
        )

        _flush = get_llm_stats()
        pipeline_run.llm_calls = _flush["total_calls"]
        pipeline_run.cache_hits = _flush["cache_hits"]
        pipeline_run.cache_misses = _flush["cache_misses"]
        pipeline_run.elapsed_seconds = round(time.time() - start_time, 1)
        db.commit()

        logger.info(
            "Processing %d senators (producer-consumer: embedding prefetch + LLM)...",
            len(senator_prepared),
        )

        progress.begin("analyze_senators", total=len(senator_prepared))
        # A fresh client — the Phase 1 client (opened at the top of this
        # function) is already closed by this point, and every sponsored
        # bill's fetch_bill_actions() call below needs a live one (observed
        # 2026-07: closed-client failures on every single call here, burning
        # ~6s of retry backoff each across up to ~12,600 sponsored bills).
        async with httpx.AsyncClient() as client:
            for senator_idx in range(len(senator_prepared)):
                prepared = senator_prepared[senator_idx]
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
                    analysis_input = _build_analysis_input(prepared, platform_texts)
                    precomputed = precompute_senator_analysis(analysis_input)

                    analysis_results = await analyze_senator_batch(
                        [analysis_input],
                        db_session=db,
                        precomputed=precomputed,
                    )
                    analysis = analysis_results[0] if analysis_results else {}
                    # Clean promises BEFORE scoring and persistence so the
                    # Promise Persistence score is computed from exactly the
                    # promises users will see (the read path applies the same
                    # rules only as a legacy safety net).
                    from app.pipeline.analyze.promise_quality import clean_promises
                    platform_data = {
                        "campaignPromises": clean_promises(
                            analysis.get("campaignPromises", [])
                        ),
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
                    # Preserve the actual recent roll call votes (from
                    # normalize_recent_votes) alongside the key bill leftovers.
                    # Without this, analyze_partisan_depth only sees the few
                    # key bill votes and misses the bulk of the voting record.
                    actual_recent = voting_record.get("recentVotes") or []
                    leftover_ids = {v["billId"] for v in final_recent_votes}
                    merged_recent = final_recent_votes + [
                        v for v in actual_recent if v["billId"] not in leftover_ids
                    ]
                    voting_record["recentVotes"] = merged_recent
                    voting_record["votingSummary"] = analysis.get("votingSummary", "")

                    lobbying_matches = analysis.get("lobbyingMatches", [])

                    # Enrich matches with real registered lobbying activity
                    # (LDA filings). lobbyingSpend was a hardcoded 0 for every
                    # match before 2026-07; now it reflects actual disclosed
                    # federal lobbying by the matched organization. Cached per
                    # org+year, so only the first pipeline run pays the fetch.
                    await enrich_lobbying_matches_with_lda(
                        lobbying_matches, db, datetime.utcnow().year - 1,
                    )

                    bio_id_for_score = senator.get("bioguideId", "")
                    temp_senator = {
                        **senator,
                        "funding": funding,
                        "votingRecord": voting_record,
                        "lobbyingMatches": lobbying_matches,
                        "campaignPromises": platform_data.get("campaignPromises", []),
                        "leadershipScore": leadership_scores.get(bio_id_for_score),
                        "bipartisanshipScore": bipartisanship_scores.get(bio_id_for_score),
                        "sponsoredBills": prepared.get("sponsoredBills", []),
                    }
                    corruption_score = calculate_scores(temp_senator)
                    corruption_score["confidence"] = calculate_confidence(temp_senator)

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
                    senator_ideology = ideology_scores.get(senator.get("bioguideId", ""))
                    partisan_profile = analyze_partisan_depth(
                        platform_data.get("campaignPromises", []),
                        senator.get("party", ""),
                        voting_record=voting_record,
                        ideology_score=senator_ideology,
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

                    # Classify policy areas for this senator's sponsored bills.
                    # Builds the richest possible text for the embedding model:
                    # official title (from pre-fetched titles), CRS policy area,
                    # and the short display title.
                    from app.pipeline.analyze.bill_analyzer import classify_policy_areas_multi
                    from app.pipeline.analyze.bill_stage import classify_bill_stage_from_actions
                    from app.pipeline.analyze.party_platform import classify_party_alignment_multi
                    raw_sponsored = prepared.get("sponsoredBills", [])
                    classified_sponsored: list[dict] = []
                    for sp in raw_sponsored:
                        title = sp.get("title", "")
                        api_policy = sp.get("policyArea", "")
                        bill_id = sp.get("billId", "")
                        bill_number_str = "".join(ch for ch in bill_id.split(".")[-1] if ch.isdigit())
                        bill_actions: list[dict] = []
                        if sp.get("billType") and bill_number_str and sp.get("congress"):
                            bill_actions = await fetch_bill_actions(
                                client, db, sp["congress"], sp["billType"].lower(), int(bill_number_str),
                            )
                        sp["stage"] = classify_bill_stage_from_actions(
                            bill_actions, sp.get("isLaw", False),
                        )
                        if api_policy:
                            sp["policyArea"] = api_policy.upper().replace(" ", "_")
                        parts = [title]
                        official = official_titles_map.get(bill_id, "")
                        if official and official.lower() != title.lower():
                            parts.append(official)
                        if api_policy:
                            parts.append(api_policy)
                        classify_text = " ".join(parts)
                        if classify_text and len(classify_text) > 10:
                            areas = classify_policy_areas_multi(classify_text, db_session=db)
                            if areas:
                                alignment = classify_party_alignment_multi(
                                    classify_text, areas, "pro",
                                )
                                sp["policyAreas"] = [
                                    {
                                        "area": a["area"],
                                        "confidence": a["confidence"],
                                        "party": {
                                            pa["area"]: pa["party"]
                                            for pa in alignment.get("areas", [])
                                        }.get(a["area"], "bipartisan"),
                                    }
                                    for a in areas
                                ]
                                sp["partyLeaning"] = alignment.get("overall", "bipartisan")
                                # Most sponsored bills never reach a floor
                                # vote, so there's usually no roll call to
                                # refine against — but when a sponsored bill
                                # *did* also get voted on (it's a key bill
                                # or recent vote elsewhere in this same
                                # pipeline run), refine with the real
                                # roll-call split the same way those two
                                # paths already do, instead of leaving this
                                # entry on content-only classification (was
                                # producing a different label for the same
                                # bill in different parts of the scorecard).
                                roll_call_data = roll_call_data_map.get(bill_id)
                                if roll_call_data:
                                    vote_split = compute_party_split(roll_call_data)
                                    sp["partyLeaning"] = refine_with_vote_data(
                                        sp["partyLeaning"], vote_split,
                                    )
                                if not sp["policyArea"] and areas:
                                    sp["policyArea"] = areas[0]["area"]
                        classified_sponsored.append(sp)
                    result["sponsoredBills"] = classified_sponsored
                    if classified_sponsored:
                        logger.info(
                            "    sponsored bills: %d (%d became law)",
                            len(classified_sponsored),
                            sum(1 for s in classified_sponsored if s.get("isLaw")),
                        )

                    bio_id = senator.get("bioguideId", "")
                    l_score = leadership_scores.get(bio_id)
                    i_score = ideology_scores.get(bio_id)
                    result["leadershipScore"] = round(l_score, 4) if l_score is not None else None
                    b_score = bipartisanship_scores.get(bio_id)
                    result["bipartisanshipScore"] = round(b_score, 4) if b_score is not None else None
                    result["ideologyScore"] = round(i_score, 4) if i_score is not None else None
                    if l_score is not None and i_score is not None:
                        result["sponsorshipDescription"] = describe_senator_position(
                            i_score, l_score, senator.get("party", ""),
                        )
                        logger.info(
                            "    sponsorship: leadership=%.2f ideology=%.2f (%s)",
                            l_score, i_score, result["sponsorshipDescription"],
                        )
                    else:
                        result["sponsorshipDescription"] = None

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
                    db.rollback()
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
        # PHASE 4: FINALIZE
        # ========================================
        pipeline_run.current_phase = "finalize"
        pipeline_run.elapsed_seconds = round(time.time() - start_time, 1)
        db.commit()

        _record_score_snapshots(db)

        try:
            from app.pipeline.analyze.score_calibration import generate_calibration_report
            report = generate_calibration_report("senator")
            if report and report["drift_events"]:
                for evt in report["drift_events"]:
                    logger.warning(
                        "SCORE DRIFT [%s] %s: %s",
                        evt["severity"], evt["dimension"], evt["message"],
                    )
            else:
                logger.info("Score calibration: no drift detected")
        except Exception:
            logger.exception("Score calibration check failed (non-fatal)")

        try:
            from app.pipeline.analyze.ground_truth import (
                check_ground_truth,
                check_score_distribution,
            )
            gt_report = check_ground_truth(db)
            # Persist on the run record so failures surface in the admin
            # dashboard instead of living only in logs (the 2026-06 audit
            # found reference senators failing across two algorithm
            # versions with no automated alarm).
            gt_failures = gt_report.get("failures", [])
            # Population-level distribution check (e.g. Promise Persistence
            # collapsing toward a neutral prior) — no individual senator's
            # promise record is independently verifiable, so this can't be
            # a named-reference check, but it catches the failure mode
            # those checks can't: everyone converging to the same score.
            gt_failures += check_score_distribution(db)
            pipeline_run.ground_truth_failures = json.dumps(gt_failures)
            db.commit()
            if gt_failures:
                from app.ops_alerts import send_ops_alert
                lines = "\n".join(
                    f"- {f.get('senator', '?')} {f.get('dimension', '?')}="
                    f"{f.get('score', '?')} expected {f.get('expected', '?')}"
                    for f in gt_failures
                )
                send_ops_alert(
                    f"Ground-truth gate failed ({len(gt_failures)})",
                    f"Reference senators outside expected score ranges "
                    f"(run #{pipeline_run.id}):\n{lines}",
                    dedupe_key=f"ground-truth-run-{pipeline_run.id}",
                )
        except Exception:
            logger.exception("Ground truth check failed (non-fatal)")

        logger.info("--- Phase 7: FINALIZE ---")
        progress.begin("finalize")

        llm_stats = get_llm_stats()
        elapsed = time.time() - start_time

        pipeline_run.status = PipelineStatus.COMPLETED
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
            "status": PipelineStatus.COMPLETED,
            "senators_processed": success_count,
            "senators_failed": fail_count,
            "bills_classified": len(classified_bills),
            "llm_stats": llm_stats,
            "elapsed_seconds": round(elapsed, 1),
        }

    except BaseException as e:
        logger.exception("Pipeline failed: %s", e)
        try:
            db.rollback()
            pipeline_run.status = PipelineStatus.FAILED
            pipeline_run.completed_at = datetime.utcnow()
            pipeline_run.error_message = "Senate pipeline failed — see server logs"
            pipeline_run.elapsed_seconds = round(time.time() - start_time, 1)
            db.commit()
        except Exception:
            logger.exception("Failed to record pipeline failure in DB")
        raise
    finally:
        db.close()
