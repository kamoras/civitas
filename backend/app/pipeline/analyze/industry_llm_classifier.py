"""
LLM-powered industry classifier for donor organizations that the static
keyword classifier can't handle.

The static classifier in transform/industry_classifier.py catches well-known
organizations, but many FEC-reported employer names don't match any keywords.
This module sends batches of unknown organizations to the LLM for classification.
"""

import json
import logging
from typing import Any

from app.pipeline.analyze.ollama_client import call_llm

logger = logging.getLogger(__name__)


async def classify_unknown_industries(
    org_names: list[str], db_session: Any | None = None
) -> dict[str, str]:
    """Use LLM to classify organizations that the static classifier missed.

    Args:
        org_names: List of organization/employer names tagged as "OTHER".
        db_session: SQLAlchemy session for cache access.

    Returns:
        Dict mapping org name -> IndustryCode.
    """
    if not org_names:
        return {}

    # Deduplicate while preserving order
    unique_names = list(dict.fromkeys(org_names))

    # Process in chunks to avoid exceeding context
    CHUNK_SIZE = 50
    all_results: dict[str, str] = {}

    for i in range(0, len(unique_names), CHUNK_SIZE):
        chunk = unique_names[i:i + CHUNK_SIZE]

        result = await call_llm(
            prompt_version="industry-classify-v1",
            system_prompt=(
                "You are an expert at classifying businesses and organizations "
                "into industry sectors. Given a list of organization or employer names, "
                "classify each one into the most appropriate industry code. "
                "Return ONLY valid JSON."
            ),
            user_prompt=f"""Classify each organization into an industry code.

Industry codes: PHARMA, INSURANCE, OIL_GAS, DEFENSE, FINANCE, REAL_ESTATE, TECH, TELECOM, AGRIBUSINESS, ENERGY, CONSTRUCTION, TRANSPORT, LAWYERS, LOBBYISTS, GAMBLING, GUNS, TOBACCO, CRYPTO, PRIVATE_PRISON, OTHER

Only use OTHER if the organization truly doesn't fit any other category. Many organizations have names that don't obviously indicate their industry — use your knowledge of these companies.

Examples:
- "Berkshire Hathaway" -> FINANCE (investment conglomerate)
- "Raytheon Technologies" -> DEFENSE (defense contractor)
- "Walmart" -> OTHER (general retail, not in any specific tracked industry)
- "Ernst & Young" -> FINANCE (accounting/consulting for financial sector)
- "Blue Origin" -> DEFENSE (aerospace, related to defense industry)

Organizations to classify:
{json.dumps(chunk, indent=1)}

Return a JSON object mapping each name to its code:
{{{", ".join(f'"{name}": "<CODE>"' for name in chunk[:3])}, ...}}""",
            cache_key={"orgNames": sorted(chunk)},
            db_session=db_session,
            max_tokens=4096,
        )

        if result and isinstance(result, dict):
            # Validate codes
            valid_codes = {
                "PHARMA", "INSURANCE", "OIL_GAS", "DEFENSE", "FINANCE",
                "REAL_ESTATE", "TECH", "TELECOM", "AGRIBUSINESS", "ENERGY",
                "CONSTRUCTION", "TRANSPORT", "LAWYERS", "LOBBYISTS",
                "GAMBLING", "GUNS", "TOBACCO", "CRYPTO", "PRIVATE_PRISON", "OTHER",
            }
            for name, code in result.items():
                if code in valid_codes:
                    all_results[name] = code
                else:
                    all_results[name] = "OTHER"
        else:
            logger.warning("LLM industry classification failed for chunk of %d", len(chunk))

    return all_results
