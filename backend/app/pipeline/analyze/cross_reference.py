"""
Cross-reference analyzer — runs all senator-level analysis in batched LLM calls:
- Cross-reference donors with votes
- Generate lobbying matches
- Generate flip-flop score
- Generate punk nickname
"""

import json
import logging
from typing import Any

from app.pipeline.analyze.ollama_client import call_llm
from app.pipeline.vector_store import search_bills

logger = logging.getLogger(__name__)


async def analyze_senator_batch(
    batch: list[dict], db_session: Any | None = None
) -> list[dict]:
    """
    Batch-analyze multiple senators in a single LLM call to stay within RPD limits.
    Each senator's data is included in one big prompt, and the LLM returns an array
    of results.

    Args:
        batch: List of dicts with keys: senator, donors, keyVotes.
        db_session: SQLAlchemy session for cache access.

    Returns:
        List of dicts with keys: senatorId, keyVotes, lobbyingMatches,
        flipFlopScore, punkNickname.
    """
    # Separate senators with no data (skip LLM for them)
    needs_analysis = [
        b for b in batch if len(b["donors"]) > 0 or len(b["keyVotes"]) > 0
    ]
    no_data = [
        b for b in batch if len(b["donors"]) == 0 and len(b["keyVotes"]) == 0
    ]

    results: dict[str, dict] = {}

    # Default results for senators with no data
    for item in no_data:
        senator = item["senator"]
        results[senator["id"]] = {
            "keyVotes": item["keyVotes"],
            "lobbyingMatches": [],
            "flipFlopScore": 25,
            "punkNickname": "TBD",
        }

    if len(needs_analysis) == 0:
        return [
            {"senatorId": b["senator"]["id"], **results[b["senator"]["id"]]}
            for b in batch
        ]

    # Process each senator individually to stay within the model's context window
    for item in needs_analysis:
        senator = item["senator"]
        senator_result = await analyze_senator(
            senator=senator,
            donors=item["donors"],
            key_votes=item["keyVotes"],
            db_session=db_session,
        )
        results[senator["id"]] = senator_result

    return [
        {"senatorId": b["senator"]["id"], **results[b["senator"]["id"]]}
        for b in batch
    ]


async def analyze_senator(
    senator: dict,
    donors: list[dict],
    key_votes: list[dict],
    db_session: Any | None = None,
) -> dict:
    """
    Analyze a single senator with a compact prompt sized for gemma2:2b (4096 ctx).
    Uses vector search to find bills semantically relevant to each donor's industry.

    Returns dict with keyVotes, lobbyingMatches, flipFlopScore.
    """
    if not donors and not key_votes:
        return {
            "keyVotes": key_votes,
            "lobbyingMatches": [],
            "flipFlopScore": 25,
        }

    # Exclude the senator's own affiliated PACs — they are not external industry lobbyists
    # and their presence distorts donor-vote connection analysis.
    external_donors = [d for d in donors if d.get("type") != "CandidateAffiliated"]

    # Build map of bill IDs to votes for quick lookup
    vote_map = {v["billId"]: v for v in key_votes}

    # For each donor, use vector search to find bills related to their industry
    # Then check if senator voted on those bills
    donor_vote_pairs = []
    for donor in external_donors[:4]:  # Top 4 donors
        donor_name = donor.get("name", "")
        donor_industry = donor.get("industry", "OTHER")

        # Search for bills related to this donor's industry/business
        query = f"{donor_name} {donor_industry.replace('_', ' ')} industry interests policy legislation"
        relevant_bills = search_bills(query=query, n_results=5)

        # Filter to bills the senator actually voted on
        for bill in relevant_bills:
            bill_id = bill["billId"]
            if bill_id in vote_map:
                vote = vote_map[bill_id]
                donor_vote_pairs.append({
                    "donor": donor,
                    "vote": vote,
                    "relevance": bill.get("distance", 1.0),
                })

    # Sort by relevance (lower distance = more relevant) and take top 10
    donor_vote_pairs.sort(key=lambda x: x["relevance"])
    top_pairs = donor_vote_pairs[:10]

    # If vector search didn't find enough matches, fall back to top recent votes
    if len(top_pairs) < 6:
        logger.info(
            "Vector search found %d donor-bill matches for %s, adding recent votes",
            len(top_pairs),
            senator["name"],
        )
        # Add recent votes that aren't already included
        included_bill_ids = {p["vote"]["billId"] for p in top_pairs}
        for vote in key_votes:
            if vote["billId"] not in included_bill_ids and len(top_pairs) < 10:
                # Associate with top donor as fallback
                top_pairs.append({
                    "donor": external_donors[0] if external_donors else {},
                    "vote": vote,
                    "relevance": 1.0,
                })
                included_bill_ids.add(vote["billId"])

    # Build compact data for LLM
    donors_compact = json.dumps(
        [
            {"n": d["name"], "t": d["total"], "type": d["type"], "ind": d.get("industry", "OTHER")}
            for d in external_donors[:4]
        ],
        separators=(",", ":"),
    )

    # Include votes with policy context (using new stance fields if available)
    votes_compact = json.dumps(
        [
            {
                "id": p["vote"]["billId"],
                "vote": p["vote"]["vote"],
                "policy": p["vote"].get("policyArea", ""),
                "stance": p["vote"].get("stance", ""),
                "groups": p["vote"].get("impactedGroups", [])[:2],
            }
            for p in top_pairs[:10]
        ],
        separators=(",", ":"),
    )

    result = await call_llm(
        prompt_version="senator-analysis-v7",
        system_prompt="Political analyst. Return ONLY valid JSON.",
        user_prompt=f"""Senator {senator['name']} ({senator['party']}-{senator['state']}), {senator.get('yearsInOffice', 0)} yrs.
DONORS (with industry):{donors_compact}
VOTES (semantically matched to donor industries):{votes_compact}
Votes are pre-filtered using semantic search to match each donor's industry interests.
Return JSON with these fields:
{{"flipFlopScore":<0-100 consistency score>,"lobbyingMatches":[{{"lobbyistOrg":"<donor name>","industry":"<donor industry code>","donationToSenator":<amount>,"billsInfluenced":["<bill id>"],"senatorVoteAligned":<true/false>,"description":"<2-3 sentences: (1) what industry interest this donor represents, (2) what the bill does and how it affects that industry, (3) how the senator voted and whether it aligned with the donor's interest>"}}]}}
Only include a lobbyingMatch if there is a CLEAR industry connection between the donor's business sector and the bill's policy area. For example:
- Finance/banking donor + banking regulation bill
- Defense contractor + defense spending bill
- Pharma company + healthcare/drug pricing bill
- Oil/gas company + energy/environmental bill
Do NOT include matches for: judicial/executive nominations, procedural votes, or bills with unclear industry connection. If no clear match exists, return empty lobbyingMatches array. Only use donors and bills from above.""",
        cache_key={
            "senatorId": senator["id"],
            "donorCount": len(external_donors),
            "voteCount": len(key_votes),
            "v": 7,
        },
        db_session=db_session,
        max_tokens=512,
    )

    if not result or not isinstance(result, dict):
        logger.warning("Analysis failed for %s", senator["name"])
        return {
            "keyVotes": key_votes,
            "lobbyingMatches": [],
            "flipFlopScore": 25,
        }

    # Clean lobbying matches
    valid_donor_names = {d["name"] for d in donors}
    lobbying_matches = []
    for m in (result.get("lobbyingMatches") or []):
        org = m.get("lobbyistOrg", "")
        industry = m.get("industry", "")
        if not org or not industry:
            continue
        lobbying_matches.append({
            "lobbyistOrg": org,
            "industry": industry,
            "lobbyingSpend": 0,
            "donationToSenator": round(m.get("donationToSenator") or 0),
            "billsInfluenced": (
                m["billsInfluenced"]
                if isinstance(m.get("billsInfluenced"), list)
                else []
            ),
            "senatorVoteAligned": bool(m.get("senatorVoteAligned")),
            "description": m.get("description", ""),
        })

    flip_flop_score = max(0, min(100, result.get("flipFlopScore", 25)))

    return {
        "keyVotes": key_votes,
        "lobbyingMatches": lobbying_matches,
        "flipFlopScore": flip_flop_score,
    }
