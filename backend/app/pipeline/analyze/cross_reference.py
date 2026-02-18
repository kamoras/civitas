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

    Returns dict with keyVotes, lobbyingMatches, flipFlopScore, punkNickname.
    """
    if not donors and not key_votes:
        return {
            "keyVotes": key_votes,
            "lobbyingMatches": [],
            "flipFlopScore": 25,
            "punkNickname": "TBD",
        }

    # Compact JSON: short field names and minimal data to reduce token count
    donors_compact = json.dumps(
        [
            {"n": d["name"], "t": d["total"], "type": d["type"]}
            for d in donors[:4]
        ],
        separators=(",", ":"),
    )
    votes_compact = json.dumps(
        [
            {
                "id": v["billId"],
                "vote": v["vote"],
                "pbv": v.get("proBusinessVote", ""),
                "ind": v.get("affectedIndustries", [])[:2],
            }
            for v in key_votes[:6]
        ],
        separators=(",", ":"),
    )

    result = await call_llm(
        prompt_version="senator-analysis-v3",
        system_prompt="Political analyst. Return ONLY valid JSON.",
        user_prompt=f"""Senator {senator['name']} ({senator['party']}-{senator['state']}), {senator.get('yearsInOffice', 0)} yrs.
DONORS:{donors_compact}
VOTES:{votes_compact}
Return JSON with these fields:
{{"flipFlopScore":<0-100 consistency score>,"punkNickname":"<2-4 word edgy nickname>","lobbyingMatches":[{{"lobbyistOrg":"<donor name>","industry":"<OIL_GAS|FINANCE|PHARMA|DEFENSE|TECH|OTHER>","donationToSenator":<amount>,"billsInfluenced":["<bill id>"],"senatorVoteAligned":<true/false>,"description":"<1 sentence>"}}]}}
Include 1-2 lobbyingMatches for the most notable donor-vote relationships. Only use donors and bills from above.""",
        cache_key={
            "senatorId": senator["id"],
            "donorCount": len(donors),
            "voteCount": len(key_votes),
            "v": 3,
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
            "punkNickname": "TBD",
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
        "punkNickname": result.get("punkNickname", "TBD"),
    }
