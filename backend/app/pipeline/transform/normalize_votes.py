"""Normalize voting data for senators.

Combines bill classification data with actual senator votes,
and provides utilities for extracting vote data from roll call records.
"""

import logging

logger = logging.getLogger(__name__)


def normalize_votes(
    bioguide_id: str,
    bill_classifications: list[dict],
    senator_votes: dict[str, str],
) -> dict:
    """Normalize voting data for a senator.

    Combines bill classification data with the senator's actual votes.

    Args:
        bioguide_id: Senator's Bioguide ID.
        bill_classifications: LLM-classified bills with vote data.
        senator_votes: Map of billId -> senator's vote on that bill.

    Returns:
        Normalized voting record matching Senator.votingRecord type.
    """
    key_votes: list[dict] = []
    pro_corporate_votes = 0
    pro_consumer_votes = 0
    total_tracked = 0

    for bill in bill_classifications:
        vote = senator_votes.get(bill.get("billId", ""))
        if not vote:
            continue  # Senator didn't vote on this bill

        total_tracked += 1

        # Normalize vote value
        vote_direction = vote.upper()
        is_yea = vote_direction in ("YEA", "AYE", "YES")
        is_nay = vote_direction in ("NAY", "NO")

        normalized_vote = "Not Voting"
        if is_yea:
            normalized_vote = "Yea"
        elif is_nay:
            normalized_vote = "Nay"

        # Determine alignment using the LLM-provided proBusinessVote field
        # This tells us which vote direction (Yea/Nay) serves corporate interests
        pro_business_vote = bill.get("proBusinessVote")
        if pro_business_vote and normalized_vote != "Not Voting":
            voted_pro_business = (
                (normalized_vote == "Yea" and pro_business_vote == "Yea")
                or (normalized_vote == "Nay" and pro_business_vote == "Nay")
            )
            if voted_pro_business:
                pro_corporate_votes += 1
            else:
                pro_consumer_votes += 1
        # Bills without proBusinessVote or "Not Voting" -- don't count toward either side

        key_votes.append({
            "billName": bill.get("billName", ""),
            "billId": bill.get("billId", ""),
            "date": bill.get("date", ""),
            "vote": normalized_vote,
            "proBusinessVote": pro_business_vote or None,
            "classification": bill.get("classification", "mixed"),
            "description": bill.get("description", ""),
            "corporateInterest": bill.get("corporateInterest", ""),
            "publicImpact": bill.get("publicImpact", ""),
            "relevantDonors": [],  # Populated by cross-reference
            "relevantDonorTotal": 0,
        })

    # Estimate total votes from tracked sample
    # Real senators vote on hundreds of bills; our tracked set is a curated sample
    estimated_total = round(total_tracked * 15) if total_tracked > 0 else 300

    return {
        "totalVotes": estimated_total,
        "proCorporateVotes": round(
            (pro_corporate_votes / max(total_tracked, 1)) * estimated_total
        ),
        "proConsumerVotes": round(
            (pro_consumer_votes / max(total_tracked, 1)) * estimated_total
        ),
        "keyVotes": key_votes,
    }


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
