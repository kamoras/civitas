"""
Validator — validates and fixes a senator record to match the Senator type.
Ports all validation rules, valid sets, and the clamp function.
"""

import logging

logger = logging.getLogger(__name__)

from app.config_definitions import VALID_INDUSTRIES

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
    # Representation score
    cs = senator.get("representationScore") or {}
    senator["representationScore"] = {
        "fundingIndependence": clamp(cs.get("fundingIndependence", 0)),
        "promisePersistence": clamp(cs.get("promisePersistence", 0)),
        "independentVoting": clamp(cs.get("independentVoting", 0)),
        "fundingDiversity": clamp(cs.get("fundingDiversity", 0)),
    }

    # Funding
    f = senator.get("funding") or {}
    valid_donor_types = {
        "PAC",
        "Individual",
        "SuperPAC",
        "Org/Employees",
        "Party/Ideological",
        "CandidateAffiliated",
        "Self-Funded",
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
                    else "Org/Employees"
                ),
                "industry": (
                    d.get("industry")
                    if d.get("industry") in VALID_INDUSTRIES
                    else "OTHER"
                ),
                "pacSponsor": d.get("pacSponsor"),
                "pacIndustry": d.get("pacIndustry"),
                "pacAnalysis": d.get("pacAnalysis"),
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
        "scoreableVotes": max(0, vr.get("scoreableVotes", 0)),
        "donorAlignedVotes": max(0, vr.get("donorAlignedVotes", 0)),
        "donorOpposedVotes": max(0, vr.get("donorOpposedVotes", 0)),
        "policyBreakdown": vr.get("policyBreakdown", []),
        "votingSummary": vr.get("votingSummary", ""),
        "votedWithPartyCount": max(0, vr.get("votedWithPartyCount", 0)),
        "votedAgainstPartyCount": max(0, vr.get("votedAgainstPartyCount", 0)),
        "partyLoyaltyPct": max(0.0, vr.get("partyLoyaltyPct", 0.0)),
        "recentVotes": [
            {
                "billName": v.get("billName", "Unknown Bill"),
                "billId": v.get("billId", ""),
                "date": v.get("date", ""),
                "vote": (
                    v.get("vote")
                    if v.get("vote") in VALID_VOTES
                    else "Not Voting"
                ),
                "policyArea": v.get("policyArea", "PROCEDURAL"),
                "stance": v.get("stance", "neutral"),
                "stanceVote": (
                    v.get("stanceVote")
                    if v.get("stanceVote") in ("Yea", "Nay")
                    else None
                ),
                "impactedGroups": (
                    v["impactedGroups"]
                    if isinstance(v.get("impactedGroups"), list)
                    else []
                ),
                "affectedIndustries": (
                    v["affectedIndustries"]
                    if isinstance(v.get("affectedIndustries"), list)
                    else []
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
                "partyLeaning": (
                    v.get("partyLeaning")
                    if v.get("partyLeaning") in ("R", "D", "bipartisan")
                    else None
                ),
                "votedWithParty": v.get("votedWithParty"),
                "voteCategory": v.get("voteCategory", "recent"),
                "keyVoteReasoning": v.get("keyVoteReasoning"),
            }
            for v in (vr.get("recentVotes") or [])
        ],
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
                "policyArea": v.get("policyArea", "PROCEDURAL"),
                "stance": v.get("stance", "neutral"),
                "stanceVote": (
                    v.get("stanceVote")
                    if v.get("stanceVote") in ("Yea", "Nay")
                    else None
                ),
                "impactedGroups": (
                    v["impactedGroups"]
                    if isinstance(v.get("impactedGroups"), list)
                    else []
                ),
                "affectedIndustries": (
                    v["affectedIndustries"]
                    if isinstance(v.get("affectedIndustries"), list)
                    else []
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
                "partyLeaning": (
                    v.get("partyLeaning")
                    if v.get("partyLeaning") in ("R", "D", "bipartisan")
                    else None
                ),
                "votedWithParty": v.get("votedWithParty"),
                "voteCategory": v.get("voteCategory", "recent"),
                "keyVoteReasoning": v.get("keyVoteReasoning"),
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
            "senatorVoteAligned": m.get("senatorVoteAligned") if m.get("senatorVoteAligned") is not None else None,
            "description": m.get("description", ""),
        }
        for m in (senator.get("lobbyingMatches") or [])
    ]

    # Strip internal/pipeline-only fields not needed in the final output
    for _internal_key in ("bioguideId",):
        senator.pop(_internal_key, None)

    if warnings:
        logger.warning(
            "Validation warnings for %s: %s",
            senator.get("name", "unknown"),
            "; ".join(warnings),
        )

    return senator
