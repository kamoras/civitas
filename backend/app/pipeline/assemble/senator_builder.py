"""
Senator builder — assembles a complete Senator record from all pipeline data.
"""

import logging

from app.pipeline.assemble.validator import validate_senator

logger = logging.getLogger(__name__)


def build_senator(
    base_senator: dict,
    funding: dict | None,
    voting_record: dict | None,
    lobbying_matches: list[dict] | None,
    corruption_score: dict | None,
) -> dict:
    """
    Assemble a complete Senator record from all pipeline data.

    Args:
        base_senator: From normalize-members (base senator record).
        funding: From normalize-finance.
        voting_record: From normalize-votes (with cross-reference data).
        lobbying_matches: From cross-reference analysis.
        corruption_score: From score-calculator.

    Returns:
        Complete, validated Senator record.
    """
    senator = {
        **base_senator,
        "representationScore": (
            corruption_score or base_senator.get("representationScore")
        ),
        "funding": funding or base_senator.get("funding"),
        "votingRecord": voting_record or base_senator.get("votingRecord"),
        "lobbyingMatches": (
            lobbying_matches or base_senator.get("lobbyingMatches")
        ),
    }

    return validate_senator(senator)
