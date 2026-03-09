"""
House pipeline — fetch, transform, analyze, and persist representative data.

Runs as a separate pipeline from the Senate orchestrator. Shares analysis
modules (bill classification, donor classification, score calculation) but
uses House-specific data sources (clerk.house.gov for votes, office=H for FEC).

Design choice: skip LLM narrative generation on the first pass because
435 reps at ~2 min/call = ~14.5 hours. Scores, funding, and vote data are
computed deterministically and don't need LLM calls.
"""

import json
import logging
import time
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import Representative, ScoreSnapshot
from app.services.representative_service import upsert_representative

from app.pipeline.fetch.congress import (
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
    fetch_senator_platform_text,
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
from app.pipeline.transform.normalize_members import normalize_house_members
from app.pipeline.transform.normalize_votes import (
    extract_representative_vote,
    find_house_roll_call,
    normalize_votes,
    normalize_recent_votes,
    compute_party_split,
)

logger = logging.getLogger(__name__)


async def run_house_pipeline() -> dict:
    """Run the full House representative pipeline."""
    db = SessionLocal()
    start_time = time.time()

    try:
        logger.info("=== HOUSE PIPELINE START ===")

        async with httpx.AsyncClient() as client:
            # ── PHASE 1: FETCH MEMBERS ──
            logger.info("--- House Phase 1: FETCH MEMBERS ---")
            raw_members = await fetch_representatives(client, db)
            logger.info("Fetched %d raw House members", len(raw_members))

            if not raw_members:
                logger.warning("No House members found — aborting")
                return {"status": "no_data", "elapsed_seconds": round(time.time() - start_time, 1)}

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

            # ── PHASE 2: NORMALIZE ──
            logger.info("--- House Phase 2: NORMALIZE ---")
            reps = normalize_house_members(raw_members, member_details)
            logger.info("Normalized %d representatives", len(reps))

            # Build bioguide -> rep mapping
            bio_to_rep: dict[str, dict] = {}
            for r in reps:
                bio_id = r.get("bioguideId", "")
                if bio_id:
                    bio_to_rep[bio_id] = r

            # ── PHASE 3: FETCH BILLS & VOTES ──
            logger.info("--- House Phase 3: FETCH BILLS & VOTES ---")

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

            # Fetch recent House roll calls
            current_year = datetime.now().year
            recent_rcs = await fetch_recent_house_roll_calls(client, db, year=current_year, count=15)
            logger.info("Fetched %d recent House roll calls", len(recent_rcs))

            # Map recent roll calls by a synthetic billId
            recent_rc_map: dict[str, dict] = {}
            for rc in recent_rcs:
                bill_id = f"HouseRC-{rc['year']}-{rc['rollNumber']}"
                recent_rc_map[bill_id] = rc

            # ── PHASE 4: CLASSIFY BILLS ──
            logger.info("--- House Phase 4: CLASSIFY BILLS ---")

            from app.pipeline.analyze.bill_analyzer import classify_all_bills

            bills_for_classification = []
            for b in bills_data:
                bill_key = f"{b['type'].upper()}.{b['number']}"
                detail = bill_details_map.get(bill_key, {})
                summaries = await fetch_bill_summaries(client, db, b["congress"], b["type"], b["number"])
                summary_text = ""
                if summaries:
                    summary_text = summaries[0].get("text", "")

                titles = await fetch_bill_titles(client, db, b["congress"], b["type"], b["number"])
                official_title = ""
                for t in (titles or []):
                    if t.get("titleTypeCode") in (6, "6"):
                        official_title = t.get("title", "")
                        break

                bills_for_classification.append({
                    "billId": bill_key,
                    "billName": official_title or b["name"],
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

            # Add vote split data from roll calls
            for bill in classified_bills:
                bill_id = bill.get("billId", "")
                rc = house_roll_calls.get(bill_id)
                if rc:
                    split = compute_party_split(rc)
                    if split and not bill.get("partyLeaning"):
                        bill["partyLeaning"] = split

            for bill in classified_recent:
                bill_id = bill.get("billId", "")
                rc = recent_rc_map.get(bill_id)
                if rc:
                    split = compute_party_split(rc)
                    if split and not bill.get("partyLeaning"):
                        bill["partyLeaning"] = split

            # ── PHASE 5: FEC DATA + SCORING ──
            logger.info("--- House Phase 5: FEC DATA + SCORING ---")

            from app.pipeline.transform.normalize_finance import normalize_finance
            from app.pipeline.analyze.score_calculator import calculate_scores

            success_count = 0
            fail_count = 0

            for idx, rep in enumerate(reps):
                try:
                    bio_id = rep.get("bioguideId", "")
                    rep_name = rep.get("name", "")
                    rep_state = rep.get("state", "")
                    rep_district = rep.get("district", 0)

                    if (idx + 1) % 25 == 0:
                        logger.info("Processing representative %d/%d: %s", idx + 1, len(reps), rep_name)

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
                    for bill in classified_recent:
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

                    # Normalize votes
                    voting_data = normalize_votes(
                        bio_id,
                        classified_bills,
                        rep_votes,
                        rep.get("party", "I"),
                    )

                    # Add recent votes
                    for rv in recent_votes_list:
                        vote_direction = rv["vote"].upper()
                        normalized = "Not Voting"
                        if vote_direction in ("YEA", "AYE", "YES"):
                            normalized = "Yea"
                        elif vote_direction in ("NAY", "NO"):
                            normalized = "Nay"
                        voting_data["recentVotes"].append({
                            "billName": rv.get("billName", ""),
                            "billId": rv.get("billId", ""),
                            "date": rv.get("date", ""),
                            "vote": normalized,
                            "policyArea": rv.get("policyArea", "PROCEDURAL"),
                            "policyAreas": rv.get("policyAreas", []),
                            "partyAlignmentWeight": rv.get("partyAlignmentWeight", 0.0),
                            "stance": rv.get("stance", "neutral"),
                            "description": rv.get("description", ""),
                            "partyLeaning": rv.get("partyLeaning"),
                            "votedWithParty": None,
                            "voteCategory": "recent",
                            "keyVoteReasoning": None,
                        })

                    rep["votingRecord"] = voting_data

                    # FEC data
                    district_str = str(rep_district).zfill(2) if rep_district else None
                    fec_candidate = await find_candidate(
                        client, db, rep_name, rep_state,
                        office="H", district=district_str,
                    )

                    if fec_candidate:
                        cand_id = fec_candidate.get("candidate_id", "")
                        financials = await fetch_candidate_financials(client, db, cand_id)
                        committees = await fetch_candidate_committees(client, db, cand_id)

                        raw_receipts = []
                        raw_pac_receipts = []
                        aggregated = []

                        for comm in committees:
                            comm_id = comm.get("committee_id", "")
                            if comm_id:
                                raw_receipts.extend(await fetch_committee_receipts(client, db, comm_id))
                                raw_pac_receipts.extend(await fetch_pac_receipts(client, db, comm_id))
                                aggregated.extend(await fetch_aggregated_contributors(client, db, comm_id))

                        finance_data = normalize_finance(
                            fec_candidate, financials, raw_receipts, raw_pac_receipts,
                            aggregated, db_session=db,
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

                    # Sponsored bills
                    sponsored = await fetch_member_sponsored(client, db, bio_id)
                    rep["sponsoredBills"] = [
                        {
                            "billId": f"{(sp.get('type') or 'hr').upper()}.{sp.get('number', '')}",
                            "title": sp.get("title", ""),
                            "introducedDate": sp.get("introducedDate", ""),
                            "latestAction": (sp.get("latestAction") or {}).get("text", ""),
                            "latestActionDate": (sp.get("latestAction") or {}).get("actionDate", ""),
                            "policyArea": "",
                            "policyAreas": [],
                            "partyLeaning": None,
                            "congress": sp.get("congress", 0),
                            "billType": sp.get("type", ""),
                            "isLaw": "became public law" in ((sp.get("latestAction") or {}).get("text", "")).lower(),
                        }
                        for sp in (sponsored or [])[:50]
                    ]

                    # Calculate scores
                    scores = calculate_scores(rep)
                    rep["representationScore"] = scores

                    # Set bioguideId for persistence
                    rep["bioguideId"] = bio_id

                    # Persist
                    upsert_representative(db, rep)
                    success_count += 1

                except Exception as e:
                    logger.error("Failed to process rep %s: %s", rep.get("name", "?"), e)
                    fail_count += 1

            # ── PHASE 6: SNAPSHOTS ──
            logger.info("--- House Phase 6: SNAPSHOTS ---")
            _record_rep_snapshots(db)

            elapsed = time.time() - start_time
            logger.info("=== HOUSE PIPELINE COMPLETE ===")
            logger.info("Representatives: %d success, %d failed", success_count, fail_count)
            logger.info("Time: %.1fs", elapsed)

            return {
                "status": "completed",
                "reps_processed": success_count,
                "reps_failed": fail_count,
                "elapsed_seconds": round(elapsed, 1),
            }

    except Exception as e:
        logger.exception("House pipeline failed: %s", e)
        return {"status": "failed", "error": str(e)[:500]}
    finally:
        db.close()


def _record_rep_snapshots(db: Session) -> None:
    """Snapshot today's scores for all representatives."""
    from app.config_definitions import SCORE_WEIGHTS

    today = datetime.utcnow().date().isoformat()
    reps = db.query(Representative).all()
    count = 0
    for r in reps:
        overall = round(
            r.score_funding_independence * SCORE_WEIGHTS["fundingIndependence"]
            + r.score_promise_persistence * SCORE_WEIGHTS["promisePersistence"]
            + r.score_independent_voting * SCORE_WEIGHTS["independentVoting"]
            + r.score_funding_diversity * SCORE_WEIGHTS["fundingDiversity"],
            2,
        )
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
            existing.score_3 = r.score_independent_voting
            existing.score_4 = r.score_funding_diversity
        else:
            db.add(ScoreSnapshot(
                entity_type="representative",
                entity_id=r.id,
                date=today,
                overall_score=overall,
                score_1=r.score_funding_independence,
                score_2=r.score_promise_persistence,
                score_3=r.score_independent_voting,
                score_4=r.score_funding_diversity,
            ))
            count += 1
    db.commit()
    logger.info("Recorded %d representative score snapshots", count)
