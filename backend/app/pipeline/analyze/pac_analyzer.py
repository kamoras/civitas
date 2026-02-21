"""
PAC analyzer — uses LLM to identify the businesses, industries, and people
behind Political Action Committees.

PAC names often obscure their true sponsors. This module sends PAC donor data
to the LLM to identify what key businesses or influential people are behind
each PAC and what policy outcomes they lobby for.
"""

import logging
from typing import Any

from app.pipeline.analyze.ollama_client import call_llm

logger = logging.getLogger(__name__)


async def analyze_pacs_batch(
    batch: list[dict], db_session: Any | None = None
) -> list[dict]:
    """Analyze PAC donors for a batch of senators to identify true sponsors.

    Args:
        batch: List of dicts with keys: senatorId, donors (list of donor dicts).
        db_session: SQLAlchemy session for cache access.

    Returns:
        List of dicts with senatorId and enriched donors list.
    """
    results = []
    for b in batch:
        pac_donors = [
            d for d in b["donors"]
            if d.get("type") == "PAC" and d.get("total", 0) > 0
        ]

        if not pac_donors:
            results.append({"senatorId": b["senatorId"], "donors": b["donors"]})
            continue

        enriched = await _enrich_pacs(pac_donors, db_session)
        enriched_map = {d["name"].upper().strip(): d for d in enriched}

        final_donors = []
        for d in b["donors"]:
            key = d["name"].upper().strip()
            if key in enriched_map:
                final_donors.append({**d, **enriched_map[key]})
            else:
                final_donors.append(d)

        results.append({"senatorId": b["senatorId"], "donors": final_donors})

    return results


async def _enrich_pacs(
    pac_donors: list[dict],
    db_session: Any | None,
) -> list[dict]:
    """Use LLM to identify the sponsors behind PAC names.

    Processes in batches of 5 to keep prompts small for local LLM.
    """
    if not pac_donors:
        return []

    all_enriched: list[dict] = []
    BATCH_SIZE = 5

    for i in range(0, len(pac_donors), BATCH_SIZE):
        batch = pac_donors[i : i + BATCH_SIZE]

        pac_lines = "\n".join(
            f"- {d['name']} (${d.get('total', 0):,.0f}, industry: {d.get('industry', 'OTHER')})"
            for d in batch
        )

        result = await call_llm(
            prompt_version="pac-analysis-v1",
            system_prompt="Campaign finance expert. Return ONLY valid JSON array.",
            user_prompt=(
                f"For each PAC, identify the sponsor organization or key businesses behind it.\n\n"
                f"PACs:\n{pac_lines}\n\n"
                f"Return JSON array:\n"
                f'[{{"name":"<PAC name>","pacSponsor":"<parent company or sponsoring org>","pacIndustry":"<specific industry>","pacAnalysis":"<1 sentence: what policy outcomes does this PAC lobby for?>"}}]\n\n'
                f"If you don't know the sponsor, use the PAC name itself as pacSponsor.\n"
                f"pacIndustry should be more specific than the broad category (e.g., 'pharmaceutical manufacturing' not just 'PHARMA')."
            ),
            cache_key={
                "pacs": sorted(d["name"].upper().strip() for d in batch),
                "v": 1,
            },
            db_session=db_session,
            max_tokens=400,
        )

        if not result or not isinstance(result, list):
            continue

        for item in result:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            all_enriched.append({
                "name": item["name"],
                "pacSponsor": str(item.get("pacSponsor", ""))[:200],
                "pacIndustry": str(item.get("pacIndustry", ""))[:100],
                "pacAnalysis": str(item.get("pacAnalysis", ""))[:300],
            })

    return all_enriched
