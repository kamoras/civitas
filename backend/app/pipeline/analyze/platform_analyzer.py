"""
Platform analyzer — uses LLM to identify campaign promises and cross-reference
them with actual voting records.

Identifies what positions a politician ran on, then highlights when their
votes align with or contradict their stated platform.
"""

import json
import logging
from typing import Any

from app.pipeline.analyze.ollama_client import call_llm

logger = logging.getLogger(__name__)


async def analyze_platform_batch(
    batch: list[dict], db_session: Any | None = None
) -> list[dict]:
    """Analyze campaign platforms and cross-reference with votes for a batch.

    Args:
        batch: List of dicts with keys:
            - senator: base senator record (name, party, state, yearsInOffice)
            - allVotes: all normalized vote dicts for this senator

    Returns:
        List of dicts with: senatorId, platformSummary, campaignPromises.
    """
    if not batch:
        return []

    senator_blocks = []
    for b in batch:
        senator = b["senator"]
        votes = b.get("allVotes", [])

        votes_json = json.dumps(
            [
                {
                    "billId": v.get("billId", ""),
                    "billName": v.get("billName", ""),
                    "vote": v.get("vote", ""),
                    "classification": v.get("classification", ""),
                    "description": v.get("description", ""),
                }
                for v in votes[:20]
            ],
            indent=1,
        )

        senator_blocks.append(
            f"--- SENATOR: {senator['name']} ({senator['party']}-{senator['state']}), "
            f"{senator.get('yearsInOffice', 0)} yrs ---\n"
            f"ID: {senator['id']}\n"
            f"VOTES ({len(votes)}):\n{votes_json}"
        )

    result = await call_llm(
        prompt_version="platform-analysis-v1",
        system_prompt=(
            "You are a nonpartisan political analyst who tracks campaign promises "
            "and legislative records. Given a senator's voting record, identify their "
            "known campaign platform positions and evaluate whether their votes align "
            "with their stated promises. Be strictly factual. Focus on major, well-known "
            "campaign positions. Return ONLY valid JSON."
        ),
        user_prompt=f"""For each senator below, identify their known campaign platform positions and evaluate alignment with their voting record.

{chr(10).join(senator_blocks)}

For each senator, identify 3-6 major campaign promises or platform positions they are publicly known for. Then evaluate whether their voting record supports or contradicts each position.

Categories: "healthcare", "economy", "defense", "environment", "immigration", "education", "guns", "tech", "finance", "civil_rights", "other"

Alignment values:
- "kept": Votes clearly support this campaign position
- "broken": Votes clearly contradict this campaign position (MOST INTERESTING — highlight these)
- "partial": Mixed record — some votes support, some contradict
- "unclear": Not enough vote data to evaluate

Return a JSON array with one object per senator:
[{{
  "senatorId": "<senator ID>",
  "platformSummary": "<2-3 sentence summary of how well their votes match their campaign positions>",
  "campaignPromises": [
    {{
      "promiseText": "<specific campaign position, e.g. 'Promised to lower prescription drug costs'>",
      "category": "<category>",
      "alignment": "<kept|broken|partial|unclear>",
      "relatedVotes": ["<billId1>", "<billId2>"],
      "analysis": "<1-2 sentences explaining the alignment/contradiction with specific vote references>"
    }}
  ]
}}]

Focus on the most notable and verifiable campaign positions. Especially highlight "broken" promises where the senator voted against their stated platform — these are the most newsworthy.""",
        cache_key={
            "senatorIds": [b["senator"]["id"] for b in batch],
            "voteCounts": [len(b.get("allVotes", [])) for b in batch],
        },
        db_session=db_session,
        max_tokens=min(len(batch) * 3000, 16384),
    )

    if not result or not isinstance(result, list):
        logger.warning("Platform analysis failed for batch of %d", len(batch))
        return [
            {
                "senatorId": b["senator"]["id"],
                "platformSummary": "",
                "campaignPromises": [],
            }
            for b in batch
        ]

    # Validate and clean results
    cleaned = []
    for i, b in enumerate(batch):
        senator_result = next(
            (r for r in result if r.get("senatorId") == b["senator"]["id"]),
            result[i] if i < len(result) else None,
        )

        if not senator_result:
            cleaned.append({
                "senatorId": b["senator"]["id"],
                "platformSummary": "",
                "campaignPromises": [],
            })
            continue

        valid_alignments = {"kept", "broken", "partial", "unclear"}
        promises = []
        for p in senator_result.get("campaignPromises", []):
            alignment = p.get("alignment", "unclear")
            if alignment not in valid_alignments:
                alignment = "unclear"
            promises.append({
                "promiseText": p.get("promiseText", ""),
                "category": p.get("category", "other"),
                "alignment": alignment,
                "relatedVotes": (
                    p["relatedVotes"]
                    if isinstance(p.get("relatedVotes"), list)
                    else []
                ),
                "analysis": p.get("analysis", ""),
            })

        cleaned.append({
            "senatorId": b["senator"]["id"],
            "platformSummary": senator_result.get("platformSummary", ""),
            "campaignPromises": promises,
        })

    return cleaned
