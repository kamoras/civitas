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

    For Independents, uses their inferred caucus party (see
    _infer_caucus_party). This ensures that senators like Sanders (I-VT)
    and King (I-ME) — who caucus with Democrats — are measured against
    the D party line rather than being excluded entirely.

    Args:
        senator_party: "R", "D", or "I" (Independents use inferred caucus)
        vote: "Yea", "Nay", or "Not Voting"
        party_leaning: "R", "D", "bipartisan", or None

    Returns:
        True = voted with party, False = voted against party, None = N/A
    """
    if vote == "Not Voting" or not party_leaning or party_leaning == "bipartisan":
        return None

    effective_party = senator_party
    if effective_party == "I":
        return None  # caller must resolve caucus first

    if effective_party == party_leaning:
        return vote == "Yea"
    else:
        return vote == "Nay"


def _infer_caucus_from_votes(
    bill_classifications: list[dict],
    senator_votes: dict[str, str],
) -> tuple[str | None, int, int]:
    """Infer caucus party from roll-call voting patterns.

    Returns:
        (party_or_None, d_support_count, r_support_count)
    """
    d_support = 0
    r_support = 0

    for bill in bill_classifications:
        party_leaning = bill.get("partyLeaning")
        if not party_leaning or party_leaning == "bipartisan":
            continue

        bill_id = bill.get("billId", "")
        vote = senator_votes.get(bill_id, "")
        vote_upper = vote.upper()
        is_yea = vote_upper in ("YEA", "AYE", "YES")
        is_nay = vote_upper in ("NAY", "NO")

        if not is_yea and not is_nay:
            continue

        if party_leaning == "D":
            if is_yea:
                d_support += 1
            else:
                r_support += 1
        elif party_leaning == "R":
            if is_yea:
                r_support += 1
            else:
                d_support += 1

    total = d_support + r_support
    if total < 5:
        return None, d_support, r_support

    if d_support > r_support:
        return "D", d_support, r_support
    elif r_support > d_support:
        return "R", d_support, r_support
    return None, d_support, r_support


def _infer_caucus_from_cosponsorship(
    cosponsorship_profile: dict,
) -> tuple[str | None, int, int]:
    """Infer caucus party from cosponsorship patterns.

    Cosponsorship is a proactive signal — a senator chooses to cosponsor a
    bill, making it a stronger alignment indicator than voting, which is
    subject to whip pressure and tactical compromises (Fowler 2006,
    "Legislative Cosponsorship Networks," Social Networks 28:4).

    Args:
        cosponsorship_profile: {"d_cosponsored": int, "r_cosponsored": int}

    Returns:
        (party_or_None, d_count, r_count)
    """
    d_count = cosponsorship_profile.get("d_cosponsored", 0)
    r_count = cosponsorship_profile.get("r_cosponsored", 0)
    total = d_count + r_count

    if total < 3:
        return None, d_count, r_count

    if d_count > r_count:
        return "D", d_count, r_count
    elif r_count > d_count:
        return "R", d_count, r_count
    return None, d_count, r_count


def _infer_caucus_party(
    bill_classifications: list[dict],
    senator_votes: dict[str, str],
    cosponsorship_profile: dict | None = None,
) -> str | None:
    """Infer an Independent senator's caucus party from behavior.

    Combines two independent signals via weighted evidence fusion:
      1. Roll-call voting patterns (how they vote on party-line bills)
      2. Cosponsorship patterns (which party's bills they actively endorse)

    Cosponsorship is weighted more heavily because it's a voluntary act of
    endorsement — free of whip pressure and procedural constraints that
    affect voting. Following Fowler (2006), cosponsorship networks reveal
    genuine policy alignment better than roll-call votes alone.

    When signals agree, confidence is high. When they disagree, the stronger
    signal (by count) wins, but requires a larger margin.

    Returns:
        "R" or "D" if a clear pattern exists, None otherwise.
    """
    vote_party, vote_d, vote_r = _infer_caucus_from_votes(
        bill_classifications, senator_votes,
    )

    cosponsor_party: str | None = None
    cosponsor_d = 0
    cosponsor_r = 0
    if cosponsorship_profile:
        cosponsor_party, cosponsor_d, cosponsor_r = (
            _infer_caucus_from_cosponsorship(cosponsorship_profile)
        )

    # Weighted evidence fusion: cosponsorship gets 1.5x weight because
    # it's a proactive endorsement, not a constrained binary choice.
    combined_d = vote_d + cosponsor_d * 1.5
    combined_r = vote_r + cosponsor_r * 1.5
    combined_total = combined_d + combined_r

    if combined_total < 5:
        return vote_party  # fall back to votes-only inference

    if combined_d > combined_r:
        result = "D"
    elif combined_r > combined_d:
        result = "R"
    else:
        return None

    if vote_party and cosponsor_party and vote_party != cosponsor_party:
        # Signals disagree — require a stronger margin to commit.
        margin = abs(combined_d - combined_r) / combined_total
        if margin < 0.2:
            logger.warning(
                "Caucus signals disagree (votes=%s, cosponsorship=%s, "
                "margin=%.2f) — insufficient confidence",
                vote_party, cosponsor_party, margin,
            )
            return None

    logger.info(
        "Caucus inference: %s (votes: %dD/%dR, cosponsorship: %dD/%dR)",
        result, vote_d, vote_r, cosponsor_d, cosponsor_r,
    )
    return result


def normalize_votes(
    bioguide_id: str,
    bill_classifications: list[dict],
    senator_votes: dict[str, str],
    senator_party: str = "I",
    cosponsorship_profile: dict | None = None,
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
        cosponsorship_profile: {"d_cosponsored": int, "r_cosponsored": int}
            for caucus inference (optional).

    Returns:
        Normalized voting record.
    """
    key_votes: list[dict] = []
    voted_with_party = 0
    voted_against_party = 0
    total_tracked = 0

    # For Independents, infer their caucus party from voting + cosponsorship
    effective_party = senator_party
    if senator_party == "I":
        inferred = _infer_caucus_party(
            bill_classifications, senator_votes, cosponsorship_profile,
        )
        if inferred:
            effective_party = inferred
            logger.info(
                "Inferred caucus party for Independent: %s",
                inferred,
            )

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

        # Party alignment (uses effective_party for Independents)
        party_leaning = bill.get("partyLeaning")
        party_aligned = _determine_party_alignment(
            effective_party, normalized_vote, party_leaning
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
            "policyAreas": bill.get("policyAreas", []),
            "partyAlignmentWeight": bill.get("partyAlignmentWeight", 0.0),
            "stance": bill.get("stance", "neutral"),
            "description": bill.get("description", ""),
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

    return {
        "totalVotes": total_tracked,
        "votedWithPartyCount": voted_with_party,
        "votedAgainstPartyCount": voted_against_party,
        "partyLoyaltyPct": party_loyalty_pct,
        "effectiveParty": effective_party,
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
    effective_party: str | None = None,
) -> list[dict]:
    """Normalize recent roll call votes for a senator.

    Args:
        classified_recent: LLM-classified recent roll call votes.
        roll_call_data_map: Map of billId -> parsed roll call data.
        senator_last_name: Senator's last name for vote matching.
        senator_state: Senator's state code.
        senator_party: Senator's party.
        effective_party: Inferred caucus party for Independents (from normalize_votes).

    Returns:
        List of normalized vote dicts for the senator.
    """
    party_for_alignment = effective_party or senator_party
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
            party_for_alignment, normalized_vote, party_leaning
        )

        votes.append({
            "billName": bill.get("billName", ""),
            "billId": bill_id,
            "date": bill.get("date", ""),
            "vote": normalized_vote,
            "policyArea": bill.get("policyArea", "PROCEDURAL"),
            "policyAreas": bill.get("policyAreas", []),
            "partyAlignmentWeight": bill.get("partyAlignmentWeight", 0.0),
            "stance": bill.get("stance", "neutral"),
            "description": bill.get("description", ""),
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
    Handles multi-word last names (e.g. "Cortez Masto", "Van Hollen") and
    accented characters (e.g. "Luján" vs "Lujan") via Unicode normalization.

    Args:
        roll_call_data: Parsed roll call vote data from senate.gov.
        bioguide_id: Senator's Bioguide ID (unused, kept for signature compat).
        last_name: Senator's last name for matching (may be multi-word).
        state: Senator's state code for matching.

    Returns:
        Vote position ("Yea", "Nay", "Not Voting") or None.
    """
    if not roll_call_data or not roll_call_data.get("members"):
        return None

    if last_name and state:
        target = _normalize_for_match(last_name)
        state_upper = state.upper()
        for member in roll_call_data["members"]:
            if member.get("state", "").upper() != state_upper:
                continue
            member_ln = _normalize_for_match(member.get("lastName", ""))
            if member_ln == target:
                return member.get("voteCast") or None

    return None


def _normalize_for_match(text: str) -> str:
    """Normalize a name for comparison: strip accents and uppercase."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).upper()


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
