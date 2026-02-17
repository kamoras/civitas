"""
PAC analyzer — uses LLM to identify the businesses, industries, and people
behind Political Action Committees.

PAC names often obscure their true sponsors. This module sends PAC donor data
to the LLM to identify what key businesses or influential people are behind
each PAC.
"""

import json
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
    # Filter to senators that have PAC-type donors
    needs_analysis = [
        b for b in batch
        if any(d.get("type") in ("PAC", "SuperPAC") for d in b.get("donors", []))
    ]

    if not needs_analysis:
        return [{"senatorId": b["senatorId"], "donors": b["donors"]} for b in batch]

    # Build prompt with all PAC donors
    senator_blocks = []
    for b in needs_analysis:
        pac_donors = [
            d for d in b["donors"]
            if d.get("type") in ("PAC", "SuperPAC")
        ]
        if not pac_donors:
            continue

        donors_json = json.dumps(
            [{"name": d["name"], "total": d["total"], "type": d["type"]}
             for d in pac_donors],
            indent=1,
        )
        senator_blocks.append(
            f"--- SENATOR: {b['senatorId']} ---\n"
            f"PAC DONORS:\n{donors_json}"
        )

    if not senator_blocks:
        return [{"senatorId": b["senatorId"], "donors": b["donors"]} for b in batch]

    result = await call_llm(
        prompt_version="pac-analysis-v1",
        system_prompt=(
            "You are a political finance analyst specializing in PAC structures. "
            "Given PAC names, identify the real businesses, industries, and influential "
            "people behind each PAC. Many PACs have innocuous-sounding names that obscure "
            "their true sponsors. Use your knowledge of well-known PACs, industry groups, "
            "and political organizations. Be factual — only state connections you're "
            "confident about. Return ONLY valid JSON."
        ),
        user_prompt=f"""Analyze these PAC donors and identify who is really behind them.

{chr(10).join(senator_blocks)}

For each PAC, identify:
- pacSponsor: The real business, trade group, or person behind this PAC (e.g., "Koch Industries" for "Americans for Prosperity PAC", or "Pharmaceutical Research and Manufacturers of America" for "PhRMA PAC"). If unknown, use null.
- pacIndustry: The industry code from: PHARMA, INSURANCE, OIL_GAS, DEFENSE, FINANCE, REAL_ESTATE, TECH, TELECOM, AGRIBUSINESS, ENERGY, CONSTRUCTION, TRANSPORT, LAWYERS, LOBBYISTS, GAMBLING, GUNS, TOBACCO, CRYPTO, PRIVATE_PRISON, OTHER
- pacAnalysis: 1-2 sentences explaining what interests this PAC represents and who benefits from its spending.

Return a JSON array with one object per senator:
[{{
  "senatorId": "<id>",
  "pacAnalysis": [
    {{"pacName": "<PAC name>", "pacSponsor": "<real sponsor or null>", "pacIndustry": "<code>", "pacAnalysis": "<explanation>"}}
  ]
}}]""",
        cache_key={
            "senatorIds": [b["senatorId"] for b in needs_analysis],
            "pacNames": [
                [d["name"] for d in b["donors"] if d.get("type") in ("PAC", "SuperPAC")]
                for b in needs_analysis
            ],
        },
        db_session=db_session,
        max_tokens=min(len(needs_analysis) * 2000, 16384),
    )

    if not result or not isinstance(result, list):
        logger.warning("PAC analysis failed")
        return [{"senatorId": b["senatorId"], "donors": b["donors"]} for b in batch]

    # Build lookup of PAC analysis by senator
    analysis_map: dict[str, dict[str, dict]] = {}
    for r in result:
        sid = r.get("senatorId", "")
        pac_map: dict[str, dict] = {}
        for p in r.get("pacAnalysis", []):
            pac_map[p.get("pacName", "")] = p
        analysis_map[sid] = pac_map

    # Enrich donors with PAC analysis
    enriched = []
    for b in batch:
        pac_data = analysis_map.get(b["senatorId"], {})
        updated_donors = []
        for d in b["donors"]:
            analysis = pac_data.get(d["name"])
            if analysis:
                d = {
                    **d,
                    "pacSponsor": analysis.get("pacSponsor"),
                    "pacIndustry": analysis.get("pacIndustry"),
                    "pacAnalysis": analysis.get("pacAnalysis", ""),
                }
            updated_donors.append(d)
        enriched.append({"senatorId": b["senatorId"], "donors": updated_donors})

    return enriched
