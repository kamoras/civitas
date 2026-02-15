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

    # Build batch prompt
    senator_blocks = []
    for idx, b in enumerate(needs_analysis):
        senator = b["senator"]
        donors = b["donors"]
        key_votes = b["keyVotes"]

        donors_json = json.dumps(
            [
                {"name": d["name"], "total": d["total"], "type": d["type"]}
                for d in donors[:6]
            ],
            indent=1,
        )
        votes_json = json.dumps(
            [
                {
                    "billId": v["billId"],
                    "billName": v["billName"],
                    "vote": v["vote"],
                    "proBusinessVote": v.get("proBusinessVote", ""),
                    "classification": v.get("classification", ""),
                    "corporateInterest": v.get("corporateInterest", ""),
                    "affectedIndustries": v.get("affectedIndustries", []),
                }
                for v in key_votes[:8]
            ],
            indent=1,
        )

        block = (
            f"--- SENATOR {idx + 1}: {senator['name']} "
            f"({senator['party']}-{senator['state']}), "
            f"{senator.get('yearsInOffice', 0)} yrs ---\n"
            f"ID: {senator['id']}\n"
            f"TOP DONORS ({len(donors)}):\n{donors_json}\n"
            f"KEY VOTES ({len(key_votes)}):\n{votes_json}"
        )
        senator_blocks.append(block)

    senator_blocks_str = "\n\n".join(senator_blocks)

    result = await call_llm(
        prompt_version="senator-batch-analysis-v2",
        system_prompt=(
            "You are a factual political data analyst. Given multiple senators' donor "
            "lists and key votes, provide a comprehensive analysis for EACH senator. "
            "Be strictly factual \u2014 correlation is not causation. Return ONLY valid JSON."
        ),
        user_prompt=f"""Analyze {len(needs_analysis)} senators. For EACH senator, produce cross-references, lobbying matches, a flip-flop score, and a punk nickname.

{senator_blocks_str}

IMPORTANT ANALYSIS RULES:
- Each vote has a "proBusinessVote" field showing which vote direction (Yea/Nay) serves corporate interests on that bill.
- For "senatorVoteAligned": compare the senator's actual "vote" to the "proBusinessVote" field. If they match, the senator voted with corporate interests on that bill.
- Only consider donors with type "PAC" or "Org/Employees" as corporate donors for alignment analysis. Ignore donors with type "Party/Ideological" \u2014 they represent broad party funding, not specific corporate interests.
- Focus cross-references on industry-specific donors whose business interests directly relate to the bill's affectedIndustries.

Return a JSON array with one object per senator, in the same order:
[
  {{
    "senatorId": "<senator ID from above>",
    "crossReferences": [
      {{"billId": "<bill>", "relevantDonors": ["<donor names matching affectedIndustries>"], "relevantDonorTotal": <sum>}}
    ],
    "lobbyingMatches": [
      {{
        "lobbyistOrg": "<corporate PAC or org from donor list, NOT party PACs>",
        "industry": "<PHARMA|OIL_GAS|FINANCE|DEFENSE|TECH|etc>",
        "lobbyingSpend": <realistic estimate>,
        "donationToSenator": <from donor list>,
        "billsInfluenced": ["<bill IDs>"],
        "senatorVoteAligned": <true if senator vote === proBusinessVote on those bills>,
        "description": "<2-3 factual sentences, no causation claims>"
      }}
    ],
    "flipFlopScore": <0-100>,
    "punkNickname": "<2-4 word edgy nickname>"
  }}
]

Generate 2-3 lobbying matches per senator from the most notable CORPORATE donor-vote relationships.
Only use donors and bills from the data provided. Do not fabricate.""",
        cache_key={
            "senatorIds": [b["senator"]["id"] for b in needs_analysis],
            "donorCounts": [len(b["donors"]) for b in needs_analysis],
            "voteCounts": [len(b["keyVotes"]) for b in needs_analysis],
        },
        db_session=db_session,
        max_tokens=min(len(needs_analysis) * 3000, 65536),
    )

    if not result or not isinstance(result, list):
        logger.warning(
            "Batch analysis failed for %d senators", len(needs_analysis)
        )
        for item in needs_analysis:
            senator = item["senator"]
            results[senator["id"]] = {
                "keyVotes": item["keyVotes"],
                "lobbyingMatches": [],
                "flipFlopScore": 25,
                "punkNickname": "TBD",
            }
        return [
            {"senatorId": b["senator"]["id"], **results[b["senator"]["id"]]}
            for b in batch
        ]

    # Process each senator's result
    for i, item in enumerate(needs_analysis):
        senator = item["senator"]
        donors = item["donors"]
        key_votes = item["keyVotes"]

        # Match by senatorId or by index
        senator_result = next(
            (r for r in result if r.get("senatorId") == senator["id"]),
            result[i] if i < len(result) else None,
        )

        if not senator_result:
            results[senator["id"]] = {
                "keyVotes": key_votes,
                "lobbyingMatches": [],
                "flipFlopScore": 25,
                "punkNickname": "TBD",
            }
            continue

        # Merge cross-references into keyVotes
        cross_ref_map = {
            r["billId"]: r
            for r in (senator_result.get("crossReferences") or [])
        }
        valid_donor_names = {d["name"] for d in donors}

        updated_votes = []
        for vote in key_votes:
            cross_ref = cross_ref_map.get(vote.get("billId"))
            if cross_ref:
                valid_relevant = [
                    name
                    for name in (cross_ref.get("relevantDonors") or [])
                    if name in valid_donor_names
                ]
                actual_total = sum(
                    next(
                        (d["total"] for d in donors if d["name"] == name), 0
                    )
                    for name in valid_relevant
                )
                updated_vote = {
                    **vote,
                    "relevantDonors": valid_relevant,
                    "relevantDonorTotal": actual_total,
                }
                updated_votes.append(updated_vote)
            else:
                updated_votes.append(vote)

        # Clean lobbying matches
        lobbying_matches = [
            {
                "lobbyistOrg": m.get("lobbyistOrg", ""),
                "industry": m.get("industry", ""),
                "lobbyingSpend": round(m.get("lobbyingSpend") or 0),
                "donationToSenator": round(m.get("donationToSenator") or 0),
                "billsInfluenced": (
                    m["billsInfluenced"]
                    if isinstance(m.get("billsInfluenced"), list)
                    else []
                ),
                "senatorVoteAligned": bool(m.get("senatorVoteAligned")),
                "description": m.get("description", ""),
            }
            for m in (senator_result.get("lobbyingMatches") or [])
            if m.get("lobbyistOrg") and m.get("industry")
        ]

        flip_flop_score = senator_result.get("flipFlopScore", 25)
        flip_flop_score = max(0, min(100, flip_flop_score))

        results[senator["id"]] = {
            "keyVotes": updated_votes,
            "lobbyingMatches": lobbying_matches,
            "flipFlopScore": flip_flop_score,
            "punkNickname": senator_result.get("punkNickname", "TBD"),
        }

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
    Fallback: analyze a single senator individually.

    Args:
        senator: Base senator record.
        donors: Top donors list.
        key_votes: Classified key votes.
        db_session: SQLAlchemy session for cache access.

    Returns:
        Dict with keyVotes, lobbyingMatches, flipFlopScore, punkNickname.
    """
    if not donors and not key_votes:
        return {
            "keyVotes": key_votes,
            "lobbyingMatches": [],
            "flipFlopScore": 25,
            "punkNickname": "TBD",
        }

    donors_json = json.dumps(
        [
            {"name": d["name"], "total": d["total"], "type": d["type"]}
            for d in donors[:8]
        ],
        indent=1,
    )
    votes_json = json.dumps(
        [
            {
                "billId": v["billId"],
                "billName": v["billName"],
                "vote": v["vote"],
                "corporateInterest": v.get("corporateInterest", ""),
                "affectedIndustries": v.get("affectedIndustries", []),
            }
            for v in key_votes[:12]
        ],
        indent=1,
    )

    result = await call_llm(
        prompt_version="senator-analysis-v2",
        system_prompt=(
            "You are a factual political data analyst. Given a senator's donor list, "
            "key votes, and bill classifications, provide a comprehensive analysis. "
            "Be strictly factual \u2014 correlation is not causation. Return ONLY valid JSON."
        ),
        user_prompt=f"""Analyze Senator {senator['name']} ({senator['party']}-{senator['state']}), {senator.get('yearsInOffice', 0)} years in office.

TOP DONORS ({len(donors)}):
{donors_json}

KEY VOTES ({len(key_votes)}):
{votes_json}

Return a single JSON object with ALL of these sections:

{{
  "crossReferences": [
    {{"billId": "<bill>", "relevantDonors": ["<donor names from list>"], "relevantDonorTotal": <sum>}}
  ],
  "lobbyingMatches": [
    {{
      "lobbyistOrg": "<org from donor list>",
      "industry": "<PHARMA|OIL_GAS|FINANCE|DEFENSE|TECH|etc>",
      "lobbyingSpend": <realistic estimate>,
      "donationToSenator": <from donor list>,
      "billsInfluenced": ["<bill IDs>"],
      "senatorVoteAligned": <true/false>,
      "description": "<2-3 factual sentences, no causation claims>"
    }}
  ],
  "flipFlopScore": <0-100, 0=consistent, 100=inconsistent>,
  "punkNickname": "<2-4 word edgy nickname based on their top industry/donors>"
}}

Generate 2-4 lobbying matches from the most notable donor-vote relationships.
Only use donors and bills from the data provided. Do not fabricate.""",
        cache_key={
            "senatorId": senator["id"],
            "donorCount": len(donors),
            "voteCount": len(key_votes),
        },
        db_session=db_session,
        max_tokens=4096,
    )

    if not result:
        logger.warning("Full analysis failed for %s", senator["name"])
        return {
            "keyVotes": key_votes,
            "lobbyingMatches": [],
            "flipFlopScore": 25,
            "punkNickname": "TBD",
        }

    # Merge cross-references into keyVotes
    cross_ref_map = {
        r["billId"]: r for r in (result.get("crossReferences") or [])
    }
    valid_donor_names = {d["name"] for d in donors}

    updated_votes = []
    for vote in key_votes:
        cross_ref = cross_ref_map.get(vote.get("billId"))
        if cross_ref:
            valid_relevant = [
                name
                for name in (cross_ref.get("relevantDonors") or [])
                if name in valid_donor_names
            ]
            actual_total = sum(
                next((d["total"] for d in donors if d["name"] == name), 0)
                for name in valid_relevant
            )
            updated_votes.append(
                {
                    **vote,
                    "relevantDonors": valid_relevant,
                    "relevantDonorTotal": actual_total,
                }
            )
        else:
            updated_votes.append(vote)

    # Clean lobbying matches
    lobbying_matches = [
        {
            "lobbyistOrg": m.get("lobbyistOrg", ""),
            "industry": m.get("industry", ""),
            "lobbyingSpend": round(m.get("lobbyingSpend") or 0),
            "donationToSenator": round(m.get("donationToSenator") or 0),
            "billsInfluenced": (
                m["billsInfluenced"]
                if isinstance(m.get("billsInfluenced"), list)
                else []
            ),
            "senatorVoteAligned": bool(m.get("senatorVoteAligned")),
            "description": m.get("description", ""),
        }
        for m in (result.get("lobbyingMatches") or [])
        if m.get("lobbyistOrg") and m.get("industry")
    ]

    flip_flop_score = result.get("flipFlopScore", 25)
    flip_flop_score = max(0, min(100, flip_flop_score))

    return {
        "keyVotes": updated_votes,
        "lobbyingMatches": lobbying_matches,
        "flipFlopScore": flip_flop_score,
        "punkNickname": result.get("punkNickname", "TBD"),
    }
