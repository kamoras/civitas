"""
Validator — validates and fixes a senator record to match the Senator type.
Ports all validation rules, valid sets, and the clamp function.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

VALID_INDUSTRIES = {
    "PHARMA",
    "INSURANCE",
    "OIL_GAS",
    "DEFENSE",
    "FINANCE",
    "REAL_ESTATE",
    "TECH",
    "TELECOM",
    "AGRIBUSINESS",
    "ENERGY",
    "CONSTRUCTION",
    "TRANSPORT",
    "LAWYERS",
    "LOBBYISTS",
    "GAMBLING",
    "GUNS",
    "TOBACCO",
    "CRYPTO",
    "PRIVATE_PRISON",
    "OTHER",
}

VALID_PARTIES = {"D", "R", "I"}
VALID_VOTES = {"Yea", "Nay", "Not Voting"}


def clamp(value: float, min_val: int = 0, max_val: int = 100) -> int:
    """Clamp a value to [min_val, max_val] and round to int."""
    return max(min_val, min(max_val, round(value)))


def validate_senator(senator: dict) -> dict:
    """
    Validate and fix a senator record to match the Senator type.
    Mutates the dict in place and returns it.

    Args:
        senator: Assembled senator record.

    Returns:
        Validated senator record.
    """
    warnings: list[str] = []

    # Basic fields
    if not senator.get("id"):
        warnings.append("Missing id")
    if not senator.get("name"):
        warnings.append("Missing name")
    state = senator.get("state", "")
    if not state or len(state) != 2:
        warnings.append(f"Invalid state: {state}")
    if senator.get("party") not in VALID_PARTIES:
        warnings.append(
            f"Invalid party: {senator.get('party')}, defaulting to I"
        )
        senator["party"] = "I"
    if (
        not isinstance(senator.get("yearsInOffice"), (int, float))
        or senator["yearsInOffice"] < 0
    ):
        senator["yearsInOffice"] = 0
    if not senator.get("initials"):
        name = senator.get("name", "")
        parts = name.split()
        senator["initials"] = "".join(
            w[0].upper() for w in parts[:2] if w
        )
    if not senator.get("punkNickname"):
        senator["punkNickname"] = "TBD"

    # Corruption score
    cs = senator.get("corruptionScore") or {}
    senator["corruptionScore"] = {
        "corporateFunding": clamp(cs.get("corporateFunding", 0)),
        "lobbyistAlignment": clamp(cs.get("lobbyistAlignment", 0)),
        "industryConcentration": clamp(cs.get("industryConcentration", 0)),
        "flipFlopIndex": clamp(cs.get("flipFlopIndex", 0)),
        "revolvingDoor": clamp(cs.get("revolvingDoor", 0)),
    }

    # Funding
    f = senator.get("funding") or {}
    valid_donor_types = {
        "PAC",
        "Individual",
        "SuperPAC",
        "Org/Employees",
        "Party/Ideological",
    }
    senator["funding"] = {
        "totalRaised": max(0, round(f.get("totalRaised", 0))),
        "totalFromPACs": max(0, round(f.get("totalFromPACs", 0))),
        "smallDonorPercentage": clamp(f.get("smallDonorPercentage", 0)),
        "topDonors": [
            {
                "name": d.get("name", "Unknown"),
                "total": max(0, round(d.get("total", 0))),
                "type": (
                    d.get("type")
                    if d.get("type") in valid_donor_types
                    else "PAC"
                ),
            }
            for d in (f.get("topDonors") or [])
        ],
        "industryBreakdown": [
            {
                "industry": (
                    ind.get("industry")
                    if ind.get("industry") in VALID_INDUSTRIES
                    else "OTHER"
                ),
                "name": ind.get("name") or ind.get("industry", "Other"),
                "total": max(0, round(ind.get("total", 0))),
                "percentage": clamp(ind.get("percentage", 0)),
            }
            for ind in (f.get("industryBreakdown") or [])
        ],
    }

    # Voting record
    vr = senator.get("votingRecord") or {}
    senator["votingRecord"] = {
        "totalVotes": max(0, vr.get("totalVotes", 0)),
        "proCorporateVotes": max(0, vr.get("proCorporateVotes", 0)),
        "proConsumerVotes": max(0, vr.get("proConsumerVotes", 0)),
        "keyVotes": [
            {
                "billName": v.get("billName", "Unknown Bill"),
                "billId": v.get("billId", ""),
                "date": v.get("date", ""),
                "vote": (
                    v.get("vote")
                    if v.get("vote") in VALID_VOTES
                    else "Not Voting"
                ),
                "proBusinessVote": (
                    v.get("proBusinessVote")
                    if v.get("proBusinessVote") in ("Yea", "Nay")
                    else None
                ),
                "classification": (
                    v.get("classification")
                    if v.get("classification")
                    in ("pro-corporate", "pro-consumer", "mixed")
                    else "mixed"
                ),
                "description": v.get("description", ""),
                "corporateInterest": v.get("corporateInterest", ""),
                "publicImpact": v.get("publicImpact", ""),
                "relevantDonors": (
                    v["relevantDonors"]
                    if isinstance(v.get("relevantDonors"), list)
                    else []
                ),
                "relevantDonorTotal": max(
                    0, round(v.get("relevantDonorTotal", 0))
                ),
            }
            for v in (vr.get("keyVotes") or [])
        ],
    }

    # Lobbying matches
    senator["lobbyingMatches"] = [
        {
            "lobbyistOrg": m.get("lobbyistOrg", "Unknown"),
            "industry": (
                m.get("industry")
                if m.get("industry") in VALID_INDUSTRIES
                else "OTHER"
            ),
            "lobbyingSpend": max(0, round(m.get("lobbyingSpend", 0))),
            "donationToSenator": max(
                0, round(m.get("donationToSenator", 0))
            ),
            "billsInfluenced": (
                m["billsInfluenced"]
                if isinstance(m.get("billsInfluenced"), list)
                else []
            ),
            "senatorVoteAligned": bool(m.get("senatorVoteAligned")),
            "description": m.get("description", ""),
        }
        for m in (senator.get("lobbyingMatches") or [])
    ]

    # Remove the bioguideId field (internal, not in Senator type)
    senator.pop("bioguideId", None)

    if warnings:
        logger.warning(
            "Validation warnings for %s: %s",
            senator.get("name", "unknown"),
            "; ".join(warnings),
        )

    return senator
