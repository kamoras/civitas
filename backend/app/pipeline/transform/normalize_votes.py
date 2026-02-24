"""Normalize voting data for senators.

Combines bill classification data with actual senator votes,
and provides utilities for extracting vote data from roll call records.
Includes party alignment analysis.
"""

import logging

logger = logging.getLogger(__name__)


def _determine_party_alignment(
    senator_party: str,
    vote: str,
    party_leaning: str | None,
) -> bool | None:
    """Determine if a senator voted with or against their party.

    Args:
        senator_party: "R", "D", or "I"
        vote: "Yea", "Nay", or "Not Voting"
        party_leaning: "R", "D", "bipartisan", or None

    Returns:
        True = voted with party, False = voted against party, None = N/A
    """
    if vote == "Not Voting" or not party_leaning or party_leaning == "bipartisan":
        return None

    if senator_party == "I":
        return None  # Independents don't have a party line

    # If the bill leans toward the senator's party, voting Yea = with party
    # If the bill leans toward the other party, voting Nay = with party
    if senator_party == party_leaning:
        return vote == "Yea"
    else:
        return vote == "Nay"


def normalize_votes(
    bioguide_id: str,
    bill_classifications: list[dict],
    senator_votes: dict[str, str],
    senator_party: str = "I",
) -> dict:
    """Normalize voting data for a senator.

    Combines bill classification data with the senator's actual votes.
    Produces a nuanced policy-area breakdown instead of a binary
    pro-corporate/pro-consumer split.

    Args:
        bioguide_id: Senator's Bioguide ID.
        bill_classifications: LLM-classified bills with vote data.
        senator_votes: Map of billId -> senator's vote on that bill.
        senator_party: Senator's party ("R", "D", "I").

    Returns:
        Normalized voting record.
    """
    key_votes: list[dict] = []
    voted_with_party = 0
    voted_against_party = 0
    total_tracked = 0

    # Policy area breakdown: {area: {total, withStance, againstStance}}
    area_stats: dict[str, dict] = {}
    # Donor-alignment tracking: how often votes favor bills with industry ties
    donor_aligned_votes = 0
    donor_opposed_votes = 0

    for bill in bill_classifications:
        vote = senator_votes.get(bill.get("billId", ""))
        if not vote:
            continue

        total_tracked += 1

        vote_direction = vote.upper()
        is_yea = vote_direction in ("YEA", "AYE", "YES")
        is_nay = vote_direction in ("NAY", "NO")

        normalized_vote = "Not Voting"
        if is_yea:
            normalized_vote = "Yea"
        elif is_nay:
            normalized_vote = "Nay"

        policy_area = bill.get("policyArea", "PROCEDURAL")
        stance_vote = bill.get("stanceVote")

        # Track policy area breakdown for non-procedural, scoreable votes
        if (
            stance_vote
            and normalized_vote != "Not Voting"
            and policy_area != "PROCEDURAL"
        ):
            if policy_area not in area_stats:
                area_stats[policy_area] = {
                    "total": 0, "withStance": 0, "againstStance": 0,
                }
            area_stats[policy_area]["total"] += 1

            voted_with_stance = normalized_vote == stance_vote
            if voted_with_stance:
                area_stats[policy_area]["withStance"] += 1
            else:
                area_stats[policy_area]["againstStance"] += 1

            # Donor-alignment: votes on bills that have identifiable industry
            # stakeholders. This replaces the old binary corporate/consumer count.
            affected_industries = bill.get("affectedIndustries") or []
            corporate_interest = bill.get("corporateInterest", "")
            if bool(affected_industries) or bool(corporate_interest):
                if voted_with_stance:
                    donor_aligned_votes += 1
                else:
                    donor_opposed_votes += 1

        # Party alignment
        party_leaning = bill.get("partyLeaning")
        party_aligned = _determine_party_alignment(
            senator_party, normalized_vote, party_leaning
        )
        if party_aligned is True:
            voted_with_party += 1
        elif party_aligned is False:
            voted_against_party += 1

        key_votes.append({
            "billName": bill.get("billName", ""),
            "billId": bill.get("billId", ""),
            "date": bill.get("date", ""),
            "vote": normalized_vote,
            "policyArea": policy_area,
            "stance": bill.get("stance", "neutral"),
            "stanceVote": stance_vote,
            "impactedGroups": bill.get("impactedGroups", []),
            "affectedIndustries": bill.get("affectedIndustries", []),
            "description": bill.get("description", ""),
            "corporateInterest": bill.get("corporateInterest", ""),
            "publicImpact": bill.get("publicImpact", ""),
            "relevantDonors": [],
            "relevantDonorTotal": 0,
            "partyLeaning": party_leaning,
            "votedWithParty": party_aligned,
            "voteCategory": "recent",
            "keyVoteReasoning": None,
        })

    party_total = voted_with_party + voted_against_party
    party_loyalty_pct = (
        round(voted_with_party / party_total * 100, 1)
        if party_total > 0
        else 0.0
    )

    # Build policy area breakdown list sorted by vote count
    policy_breakdown = sorted(
        [
            {
                "policyArea": area,
                "totalVotes": stats["total"],
                "withStance": stats["withStance"],
                "againstStance": stats["againstStance"],
            }
            for area, stats in area_stats.items()
        ],
        key=lambda x: x["totalVotes"],
        reverse=True,
    )

    scoreable = donor_aligned_votes + donor_opposed_votes

    return {
        "totalVotes": total_tracked,
        "scoreableVotes": scoreable,
        "donorAlignedVotes": donor_aligned_votes,
        "donorOpposedVotes": donor_opposed_votes,
        "policyBreakdown": policy_breakdown,
        "votedWithPartyCount": voted_with_party,
        "votedAgainstPartyCount": voted_against_party,
        "partyLoyaltyPct": party_loyalty_pct,
        "votingSummary": "",
        "recentVotes": [],
        "keyVotes": key_votes,
    }


def normalize_recent_votes(
    classified_recent: list[dict],
    roll_call_data_map: dict[str, dict],
    senator_last_name: str,
    senator_state: str,
    senator_party: str,
) -> list[dict]:
    """Normalize recent roll call votes for a senator.

    Args:
        classified_recent: LLM-classified recent roll call votes.
        roll_call_data_map: Map of billId -> parsed roll call data.
        senator_last_name: Senator's last name for vote matching.
        senator_state: Senator's state code.
        senator_party: Senator's party.

    Returns:
        List of normalized vote dicts for the senator.
    """
    votes = []
    for bill in classified_recent:
        bill_id = bill.get("billId", "")
        roll_call = roll_call_data_map.get(bill_id)
        if not roll_call:
            continue

        # Find this senator's vote in the roll call
        senator_vote = extract_senator_vote(
            roll_call, "", senator_last_name, senator_state
        )
        if not senator_vote:
            continue

        # Normalize
        vote_direction = senator_vote.upper()
        is_yea = vote_direction in ("YEA", "AYE", "YES")
        is_nay = vote_direction in ("NAY", "NO")
        normalized_vote = "Not Voting"
        if is_yea:
            normalized_vote = "Yea"
        elif is_nay:
            normalized_vote = "Nay"

        party_leaning = bill.get("partyLeaning")
        party_aligned = _determine_party_alignment(
            senator_party, normalized_vote, party_leaning
        )

        votes.append({
            "billName": bill.get("billName", ""),
            "billId": bill_id,
            "date": bill.get("date", ""),
            "vote": normalized_vote,
            # New policy stance fields
            "policyArea": bill.get("policyArea", "PROCEDURAL"),
            "stance": bill.get("stance", "neutral"),
            "stanceVote": bill.get("stanceVote"),
            "impactedGroups": bill.get("impactedGroups", []),
            # Legacy fields
            "proBusinessVote": bill.get("proBusinessVote"),
            "classification": bill.get("classification", "mixed"),
            "description": bill.get("description", ""),
            "corporateInterest": bill.get("corporateInterest", ""),
            "publicImpact": bill.get("publicImpact", ""),
            "relevantDonors": [],
            "relevantDonorTotal": 0,
            "partyLeaning": party_leaning,
            "votedWithParty": party_aligned,
            "voteCategory": "recent",
            "keyVoteReasoning": None,
        })

    return votes


def compute_party_split(roll_call_data: dict) -> str | None:
    """Compute party alignment from roll call member votes.

    Uses actual party vote distributions to determine if a roll call was a
    Republican bill, Democratic bill, or bipartisan vote — without relying on
    LLM classification.

    Returns:
        "R" if 75%+ of Republicans voted Yea and 25%- of Democrats did,
        "D" if 75%+ of Democrats voted Yea and 25%- of Republicans did,
        "bipartisan" otherwise, or None if insufficient data.
    """
    members = roll_call_data.get("members", [])
    r_yea = r_total = d_yea = d_total = 0
    for m in members:
        party = m.get("party", "")
        vote = (m.get("voteCast") or "").upper()
        if party == "R":
            r_total += 1
            if vote in ("YEA", "AYE", "YES"):
                r_yea += 1
        elif party == "D":
            d_total += 1
            if vote in ("YEA", "AYE", "YES"):
                d_yea += 1

    if r_total < 3 or d_total < 3:
        return None  # Not enough party data

    r_yea_pct = r_yea / r_total
    d_yea_pct = d_yea / d_total

    if r_yea_pct >= 0.65 and d_yea_pct <= 0.35:
        return "R"
    if d_yea_pct >= 0.65 and r_yea_pct <= 0.35:
        return "D"
    return "bipartisan"


def extract_senator_vote(
    roll_call_data: dict | None,
    bioguide_id: str,
    last_name: str | None = None,
    state: str | None = None,
) -> str | None:
    """Extract a senator's vote from roll call vote data.

    Matches by last name + state since senate.gov XML doesn't include bioguideId.

    Args:
        roll_call_data: Parsed roll call vote data from senate.gov.
        bioguide_id: Senator's Bioguide ID (unused, kept for signature compat).
        last_name: Senator's last name for matching.
        state: Senator's state code for matching.

    Returns:
        Vote position ("Yea", "Nay", "Not Voting") or None.
    """
    if not roll_call_data or not roll_call_data.get("members"):
        return None

    # Match by last name + state
    if last_name and state:
        for member in roll_call_data["members"]:
            if (
                member.get("lastName", "").upper() == last_name.upper()
                and member.get("state", "").upper() == state.upper()
            ):
                return member.get("voteCast") or None

    return None


def find_senate_roll_call(actions: list[dict] | None) -> dict | None:
    """Try to find the Senate roll call vote for a bill from its actions.

    Args:
        actions: Bill actions from Congress.gov.

    Returns:
        Dict with congress, session, rollCallNumber keys, or None.
    """
    if not actions:
        return None

    for action in actions:
        # Look for Senate roll call vote actions
        text = (action.get("text") or "").lower()
        if (
            "passed senate" in text
            or "senate agreed" in text
            or "cloture" in text
            or "roll call vote" in text
        ) and action.get("recordedVotes"):
            for rv in action["recordedVotes"]:
                if rv.get("chamber") == "Senate" and rv.get("rollNumber"):
                    return {
                        "congress": rv.get("congress"),
                        "session": rv.get("sessionNumber"),
                        "rollCallNumber": rv.get("rollNumber"),
                    }

    return None
