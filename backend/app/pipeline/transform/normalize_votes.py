"""Normalize voting data for senators.

Combines bill classification data with actual senator votes,
and provides utilities for extracting vote data from roll call records.
Includes party alignment analysis.
"""

import logging
import re
from collections import defaultdict
from difflib import SequenceMatcher
from datetime import datetime

logger = logging.getLogger(__name__)

# The majority leader votes Nay on a motion that is about to fail even when
# they support it, because only a member who voted on the prevailing side
# may move to reconsider (Senate Rule XIII; House Rule XIX clause 2) — the
# switch keeps the motion alive for another try. It is a documented
# procedural convention of the office, not a break with party, and counting
# it as one made the Senate Majority Leader's 16 "breaks" in the 119th
# Congress (every one a Nay on a rejected cloture vote his own conference
# supported) read as a maverick record. The same rule covers the mirror
# case — a Yea on a motion that carried over the leader's own party's
# opposition — and any rejected or carried question, not only cloture: the
# roll call can't tell a leader's switch from a decisive vote of conscience
# on the same side. The evidence is the Nay case: in the 119th Congress every
# off-party vote by either majority leader was a Nay on a rejected motion
# (all but one on cloture). No mirror-case vote occurred; that half follows
# from the rule itself, not from observed votes. Scoped to the majority leader only:
# it is the leader's job to make that motion, and the Speaker and the
# minority leader have no such practice (the Speaker voted Aye on the same
# failed House rule the Majority Leader voted No on). Exact titles as
# unitedstates/congress-legislators prints them (committee_leadership.py) —
# "Assistant Senate Majority Leader" is a different office and must not
# match, so don't loosen this to a substring test.
MAJORITY_LEADER_TITLES = frozenset({"Senate Majority Leader", "House Majority Leader"})

# A member with no tenure dates but a current majority-leader title (the
# tenure file hasn't been refreshed since they took the job) is treated as
# holding it throughout — the scoring window is the current congress, which
# a current leader almost always led from its first day.
_ALWAYS = (None, None)


def majority_leader_spans(
    current_title: str | None, tenures: list[dict] | None,
) -> list[tuple[str | None, str | None]]:
    """The [start, end) date spans (ISO strings; end None = still held)
    during which a member was their chamber's majority leader, from their
    leadership_tenures entry. Falls back to the current title, held
    throughout, when the tenure data has no majority-leader span for them.
    Empty for everyone else."""
    spans = [
        (t.get("start"), t.get("end"))
        for t in tenures or []
        if t.get("title") in MAJORITY_LEADER_TITLES
    ]
    if spans:
        return spans
    if current_title in MAJORITY_LEADER_TITLES:
        return [_ALWAYS]
    return []


def vote_date_iso(raw: str | None) -> str | None:
    """A roll call's date as YYYY-MM-DD, from either the ISO form (House
    parser, key-bill action dates) or Senate.gov's "October 14, 2025,
    05:34 PM". None if it is neither."""
    text = " ".join((raw or "").split())
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        pass
    parts = text.split(",")
    if len(parts) >= 2:
        try:
            return datetime.strptime(
                f"{parts[0].strip()}, {parts[1].strip()}", "%B %d, %Y",
            ).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def roll_call_ref(roll_call: dict) -> str | None:
    """"house-119-2-221": which roll call this is, in the form the stored
    vote rows keep (key_votes.roll_call) and the vote API resolves to the
    Congress record's RollCall. None without a congress, session and number."""
    chamber = "house" if roll_call.get("chamber") == "House" else "senate"
    parts = [roll_call.get(k) for k in ("congress", "session", "rollNumber")]
    if not all(parts):
        return None
    return f"{chamber}-{parts[0]}-{parts[1]}-{parts[2]}"


# Questions that run the chamber rather than decide anything (v6.19): quorum
# calls, adjourning, approving the Journal, the House's previous question,
# and motions to table or to recommit. They split on party lines as a
# matter of course — a motion to recommit is the minority's messaging vote,
# the previous question the majority's hold on the floor — so a vote with
# the other side on one says little about the member. They never count as
# a break with the party (or as voting with it). Rule votes, cloture and
# nominations are not here: those decide whether a bill reaches the floor,
# whether it gets a vote, and who serves.
_HOUSEKEEPING_QUESTION_RE = re.compile(
    r"quorum|call of the house|adjourn|journal|previous question|motion to table|to table the|recommit",
    re.IGNORECASE,
)
# The House's motion to commit (v6.28) is the motion to recommit's twin for
# a bill no committee reported (House Rule XIX): the same minority motion,
# and it splits on party lines the same way. The Senate's motion to commit
# carries instructions that amend the bill, so it is a vote on substance
# and stays counted there.
_HOUSE_HOUSEKEEPING_QUESTION_RE = re.compile(r"motion to commit", re.IGNORECASE)


def is_housekeeping(question: str | None, chamber: str | None = None) -> bool:
    """A roll call on running the chamber rather than on a bill, nominee or
    rule (see _HOUSEKEEPING_QUESTION_RE). `chamber` ("house" or "senate",
    either case) adds the House-only questions."""
    if not question:
        return False
    if _HOUSEKEEPING_QUESTION_RE.search(question):
        return True
    return (chamber or "").lower() == "house" and bool(_HOUSE_HOUSEKEEPING_QUESTION_RE.search(question))


def stamp_roll_call_outcome(bill: dict, roll_call: dict) -> None:
    """Copy the roll call's own record onto the classified vote dict that
    represents it: motionRejected (the chamber's result, True / False /
    None = unknown — see congress.roll_call_rejected), rollCallDate,
    rollCall (roll_call_ref), and partySplit — how the parties actually
    voted (compute_party_split). A vote counts toward party loyalty only
    through partySplit: with no roll call there is no split, and a vote
    is never marked with or against the party from what the bill says.
    A housekeeping question (is_housekeeping) has no split for loyalty
    either, and is marked procedural (`housekeeping`, policy area
    PROCEDURAL, no per-area labels or party label): a motion to recommit
    carries its bill's title, so classified as the bill it read as a vote
    on it, and a Yea on the minority's motion counted toward the bill's
    party in partisan depth and taught the party-position centroids the
    bill under the other party's label (v6.28). Callers skip the split
    refinement for it (refine_with_vote_data)."""
    bill["motionRejected"] = roll_call.get("rejected")
    bill["rollCallDate"] = vote_date_iso(roll_call.get("voteDate"))
    bill["rollCall"] = roll_call_ref(roll_call)
    if is_housekeeping(roll_call.get("question"), roll_call.get("chamber")):
        bill["housekeeping"] = True
        bill["partySplit"] = None
        bill["partyLeaning"] = None
        bill["policyArea"] = "PROCEDURAL"
        bill["policyAreas"] = []
        bill["partyAlignmentWeight"] = 0.0
    else:
        bill["partySplit"] = compute_party_split(roll_call)


def is_reconsider_switch(
    bill: dict, leader_spans: list[tuple[str | None, str | None]] | None,
) -> str | None:
    """The prevailing side ("Nay" if the question was rejected, "Yea" if it
    carried) when a vote on it by this member could be the majority leader's
    reconsider switch (MAJORITY_LEADER_TITLES): the member held the office
    on the vote's date and the chamber recorded the outcome. None otherwise
    — an unknown result or date is not enough; the exemption needs the
    chamber's own word on which side prevailed."""
    rejected = bill.get("motionRejected")
    if not leader_spans or rejected not in (True, False):
        return None
    prevailing = "Nay" if rejected else "Yea"
    date = vote_date_iso(bill.get("rollCallDate") or bill.get("date"))
    for start, end in leader_spans:
        if (start, end) == _ALWAYS:
            return prevailing
        if date is None:
            continue
        if (start is None or start <= date) and (end is None or date < end):
            return prevailing
    return None


def vote_identity(vote: dict) -> str:
    """The unique identity of the roll call a normalized vote records.

    billId (a Senate documentName) is NOT unique — the chamber votes on the
    same document repeatedly (motion to proceed, cloture, passage), so it
    can't tell two different votes apart, and one roll call can also reach
    a member's record through two paths (as a key bill's vote and again as
    a recent roll call). rcKey (congress-session-roll for the Senate,
    HouseRC-year-roll for the House) is the roll call itself. Every place
    that dedupes votes or picks key votes must use this, never billId —
    keying on billId is what let the same party break be counted twice in
    Constituent Alignment.
    """
    return vote.get("rcKey") or vote.get("billId", "")


def stored_vote(row_id: int, bill_id: str, voted_with_party: bool | None) -> dict:
    """A stored key_votes / rep_key_votes row as the dict party_break_rate
    reads — the one shape the ground-truth gate, the score-breakdown API and
    rescore.py rebuild stored votes in.

    Storage holds each roll call once (the Senate splits one deduped pool
    into key and recent votes; the House drops recent roll calls a key bill
    covers), but the roll call's rcKey is not stored and bill_id is not
    unique per roll call (a Senate key bill's documentName covers its
    cloture and passage votes). So the row is the identity: with billId
    alone, dedupe_votes would merge those votes."""
    return {"rcKey": f"row-{row_id}", "billId": bill_id, "votedWithParty": voted_with_party}


def house_roll_call_id(rc: dict) -> str:
    """Unique id for one House roll call — the House analog of
    bill_analyzer.recent_roll_call_key, used as both billId and rcKey for
    recent House votes."""
    return f"HouseRC-{rc.get('year', '')}-{rc.get('rollNumber', '')}"


def dedupe_votes(votes: list[dict]) -> list[dict]:
    """Drop repeat entries for the same roll call (see vote_identity),
    keeping the first occurrence. A vote with no identity at all can't be
    shown to repeat anything, so it is kept."""
    seen: set[str] = set()
    out: list[dict] = []
    for v in votes:
        ident = vote_identity(v)
        if ident and ident in seen:
            continue
        seen.add(ident)
        out.append(v)
    return out


def reconsider_switch_applied(
    party: str, vote: str, party_leaning: str | None, reconsider_switch: str | None,
) -> bool:
    """Whether the reconsider-switch exemption turns this vote from a break
    into no party signal: an eligible roll call (is_reconsider_switch gives
    the prevailing side), the member voting with the prevailing side, and
    their own party on the losing one — a Nay on a rejected question their
    party backed (party_leaning, the side that voted Yea, is theirs), or a
    Yea on a carried one it opposed (the other party was the Yea side)."""
    if not reconsider_switch or vote != reconsider_switch or party not in ("R", "D"):
        return False
    if reconsider_switch == "Nay":
        return party_leaning == party
    return party_leaning in ("R", "D") and party_leaning != party


def _determine_party_alignment(
    senator_party: str,
    vote: str,
    party_leaning: str | None,
    *,
    reconsider_switch: str | None = None,
) -> bool | None:
    """Determine if a senator voted with or against their party.

    reconsider_switch (see is_reconsider_switch): the side that prevailed,
    when the member was majority leader and the chamber recorded the
    outcome. A vote with the prevailing side against the member's own
    party is the procedural switch, not a break — it returns None (no
    party signal) rather than False (reconsider_switch_applied). It never
    turns a vote into a party-line one, and a vote against the party on
    the losing side stays a break.

    For Independents, uses their inferred caucus party (see
    _infer_caucus_party). This ensures that Independent senators who caucus
    with Democrats are measured against
    the D party line rather than being excluded entirely.

    Args:
        senator_party: "R", "D", or "I" (Independents use inferred caucus)
        vote: "Yea", "Nay", "Present" or "Not Voting"
        party_leaning: "R", "D", "bipartisan", or None

    Returns:
        True = voted with party, False = voted against party, None = N/A
    """
    # "Present" is a recorded answer but takes no side: no party signal.
    if vote not in ("Yea", "Nay") or not party_leaning or party_leaning == "bipartisan":
        return None

    effective_party = senator_party
    if effective_party == "I":
        return None  # caller must resolve caucus first

    if reconsider_switch_applied(effective_party, vote, party_leaning, reconsider_switch):
        return None

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

        # Recent roll calls carry a unique "rcKey" (congress-session-roll);
        # senator_votes is keyed by it because billId (documentName) is not
        # unique across multiple votes on the same document. Key bills have
        # no rcKey and keep their billId key.
        vote = senator_votes.get(bill.get("rcKey") or bill.get("billId", ""), "")
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
    leader_spans: list[tuple[str | None, str | None]] | None = None,
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
        leader_spans: majority_leader_spans() for this member — when they
            were majority leader, for the reconsider-switch exemption.

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
        # rcKey-first for the same reason as _analyze's lookup above.
        vote = senator_votes.get(bill.get("rcKey") or bill.get("billId", ""))
        if not vote:
            continue

        total_tracked += 1

        entry = member_vote_entry(bill, vote, effective_party, leader_spans)
        if entry["votedWithParty"] is True:
            voted_with_party += 1
        elif entry["votedWithParty"] is False:
            voted_against_party += 1
        key_votes.append(entry)

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
        "recentVotes": [],
        "keyVotes": key_votes,
    }


def member_vote_entry(
    bill: dict,
    member_vote: str,
    party: str,
    leader_spans: list[tuple[str | None, str | None]] | None,
) -> dict:
    """One member's vote on a classified roll call, in the stored shape:
    key votes and recent votes, both chambers. The House used to build its
    recent votes inline and drifted, counting loyalty from partyLeaning (the
    split even on housekeeping questions, and the bill's content label
    wherever the roll call gave no split) instead of partySplit
    (stamp_roll_call_outcome).

    `party` is the member's party for alignment (an Independent's caucus).
    """
    direction = member_vote.upper()
    vote = "Not Voting"
    if direction in ("YEA", "AYE", "YES"):
        vote = "Yea"
    elif direction in ("NAY", "NO"):
        vote = "Nay"
    elif direction.startswith("PRESENT"):
        # Answered present (the Senate's "Present, Giving Live Pair" too):
        # attended and declined to take a side, which is not a missed vote.
        vote = "Present"

    party_split = bill.get("partySplit")
    reconsider_switch = is_reconsider_switch(bill, leader_spans)
    return {
        "billName": bill.get("billName", ""),
        "billId": bill.get("billId", ""),
        "date": bill.get("date") or bill.get("rollCallDate") or "",
        "vote": vote,
        "policyArea": bill.get("policyArea", "PROCEDURAL"),
        "policyAreas": bill.get("policyAreas", []),
        "partyAlignmentWeight": bill.get("partyAlignmentWeight", 0.0),
        "stance": bill.get("stance", "neutral"),
        "description": bill.get("description", ""),
        "partyLeaning": bill.get("partyLeaning"),
        # From how the parties actually voted on this roll call, never the
        # bill's content (stamp_roll_call_outcome).
        "votedWithParty": _determine_party_alignment(
            party, vote, party_split, reconsider_switch=reconsider_switch,
        ),
        "reconsiderSwitch": reconsider_switch_applied(
            party, vote, party_split, reconsider_switch,
        ),
        "voteCategory": "recent",
        "rcKey": bill.get("rcKey"),
        # What the roll call decided (passage, cloture, amendment ...;
        # classify_recent_votes): lets a display say which vote it shows.
        "motionType": bill.get("motionType"),
        "rollCall": bill.get("rollCall"),
    }


def normalize_recent_votes(
    classified_recent: list[dict],
    roll_call_data_map: dict[str, dict],
    senator_last_name: str,
    senator_state: str,
    senator_party: str,
    effective_party: str | None = None,
    leader_spans: list[tuple[str | None, str | None]] | None = None,
    lis_id: str | None = None,
) -> list[dict]:
    """Normalize recent roll call votes for a senator.

    Args:
        classified_recent: LLM-classified recent roll call votes.
        roll_call_data_map: Map of billId -> parsed roll call data.
        senator_last_name: Senator's last name for vote matching.
        senator_state: Senator's state code.
        senator_party: Senator's party.
        effective_party: Inferred caucus party for Independents (from normalize_votes).
        leader_spans: see normalize_votes.

    Returns:
        List of normalized vote dicts for the senator.
    """
    party_for_alignment = effective_party or senator_party
    votes = []
    for bill in classified_recent:
        bill_id = bill.get("billId", "")
        # roll_call_data_map is keyed by the unique rcKey (congress-session-
        # roll) since documentName repeats across multiple votes on the same
        # measure; billId remains the display identifier.
        roll_call = roll_call_data_map.get(bill.get("rcKey") or bill_id)
        if not roll_call:
            continue

        # Find this senator's vote in the roll call
        senator_vote = extract_senator_vote(
            roll_call, senator_last_name, senator_state, lis_id=lis_id,
        )
        if not senator_vote:
            continue

        votes.append(member_vote_entry(bill, senator_vote, party_for_alignment, leader_spans))

    return votes


def compute_party_vote_split(roll_call_data: dict) -> dict | None:
    """Compute the full party split from roll call member votes: the
    same R/D/bipartisan label compute_party_split() returns, plus the two
    raw yea percentages it's derived from.

    Uses actual party vote distributions to determine if a roll call was a
    Republican bill, Democratic bill, or bipartisan vote — without relying on
    LLM classification.

    The shares are of the members who voted Yea or Nay, as in CQ's party
    unity votes: an absent or "present" member took no side, and counting
    them as not-Yea let a party's absences decide the label (a 143-73 vote
    read bipartisan beside the other party's 0-212).

    Returns:
        {"label": "R"|"D"|"bipartisan", "r_yea_pct": float, "d_yea_pct": float},
        or None if either party has fewer than 3 Yea/Nay votes.
    """
    members = roll_call_data.get("members", [])
    r_yea = r_total = d_yea = d_total = 0
    for m in members:
        party = m.get("party", "")
        vote = (m.get("voteCast") or "").upper()
        yea = vote in ("YEA", "AYE", "YES")
        if not yea and vote not in ("NAY", "NO"):
            continue
        if party == "R":
            r_total += 1
            r_yea += yea
        elif party == "D":
            d_total += 1
            d_yea += yea

    if r_total < 3 or d_total < 3:
        return None  # Not enough party data

    r_yea_pct = r_yea / r_total
    d_yea_pct = d_yea / d_total

    if r_yea_pct >= 0.65 and d_yea_pct <= 0.35:
        label = "R"
    elif d_yea_pct >= 0.65 and r_yea_pct <= 0.35:
        label = "D"
    else:
        label = "bipartisan"

    return {"label": label, "r_yea_pct": r_yea_pct, "d_yea_pct": d_yea_pct}


def compute_party_split(roll_call_data: dict) -> str | None:
    """Compute party alignment from roll call member votes.

    Thin wrapper over compute_party_vote_split() for callers that only
    need the label. See that function for the underlying percentages.

    Returns:
        "R" if 65%+ of Republicans voted Yea and 35%- of Democrats did,
        "D" if 65%+ of Democrats voted Yea and 35%- of Republicans did,
        "bipartisan" otherwise, or None if insufficient data.
    """
    result = compute_party_vote_split(roll_call_data)
    return result["label"] if result else None


def first_name_matches(first: str, names: dict) -> list:
    """The keys of `names` whose first name is `first`: the exact matches
    (accents and case aside) when there are any, else the looser ones
    (_same_first_name: a nickname, a dropped accent). An exact match wins,
    so "Rob" and "Robert" each keep their own. An empty first name matches
    nothing."""
    if not _normalize_for_match(first or "").strip(" ."):
        return []
    exact = [k for k, name in names.items() if _normalize_for_match(name or "") == _normalize_for_match(first or "")]
    return exact or [k for k, name in names.items() if _same_first_name(first, name)]


def resolve_senate_lis_ids(members: list[dict], seen: list[dict]) -> dict[str, str]:
    """{member id: LIS id} for each member whose last name and state the
    roll calls give to more than one person, told apart by first name.

    Senate roll calls name a senator by last name and state, which is one
    person until a seat passes to someone of the same surname. When a
    senator's seat went to an appointee of the same surname, every vote
    the predecessor cast in the Congress was credited to the appointee
    (live, 2026-10-03: the appointee's first roll call on the site predated
    the appointment, cast under the predecessor's LIS id). The roll call's
    own member id separates them.

    `members`: dicts with "id", "name", "lastNameForVoteMatch", "state".
    `seen`: roll-call members, dicts with "lisId", "firstName", "lastName",
    "state". A member whose key matches one LIS id is left out (matching
    by name is exact there and needs no first-name agreement, which a
    nickname would break). A member with several takes the one whose first
    name matches theirs, an exact match over a looser one
    (first_name_matches); with no single match it maps to "": no vote is
    credited rather than someone else's.
    """
    people: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    for m in seen:
        lis = (m.get("lisId") or "").strip()
        if lis:
            key = (_normalize_for_match(m.get("lastName") or ""), (m.get("state") or "").upper())
            people[key][lis] = m.get("firstName") or ""
    out: dict[str, str] = {}
    for member in members:
        key = (_normalize_for_match(member.get("lastNameForVoteMatch") or ""), (member.get("state") or "").upper())
        candidates = people.get(key) or {}
        if len(candidates) < 2:
            continue
        first = (member.get("name") or "").split(" ")[0]
        matched = first_name_matches(first, candidates)
        out[member["id"]] = matched[0] if len(matched) == 1 else ""
    return out


def _same_first_name(a: str, b: str) -> bool:
    """One first name written two ways ("Tim" / "Timothy", an accent
    dropped), by prefix or Ratcliff-Obershelp similarity."""
    a, b = _normalize_for_match(a).strip("."), _normalize_for_match(b).strip(".")
    if not a or not b:
        return False
    return a.startswith(b) or b.startswith(a) or SequenceMatcher(None, a, b).ratio() >= 0.8


def extract_senator_vote(
    roll_call_data: dict | None,
    last_name: str | None = None,
    state: str | None = None,
    lis_id: str | None = None,
) -> str | None:
    """Extract a senator's vote from roll call vote data.

    Matches by last name + state since senate.gov XML doesn't include bioguideId.
    Handles multi-word last names (e.g. "De la Rosa", "Van Doren") and
    accented characters (e.g. "Núñez" vs "Nunez") via Unicode normalization.

    Args:
        roll_call_data: Parsed roll call vote data from senate.gov.
        last_name: Senator's last name for matching (may be multi-word).
        state: Senator's state code for matching.
        lis_id: from resolve_senate_lis_ids, when the name and state are
            shared with someone else: only that member id's vote is theirs
            ("" matches nothing).

    Returns:
        Vote position as recorded (e.g. "Yea", "Nay", "Present",
        "Not Voting") or None.
    """
    if not roll_call_data or not roll_call_data.get("members"):
        return None

    if lis_id is not None:
        if not lis_id:
            return None
        for member in roll_call_data["members"]:
            if member.get("lisId") == lis_id:
                return member.get("voteCast") or None
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
    from app.pipeline.transform.normalize_members import strip_accents
    return strip_accents(text).upper()


def extract_representative_vote(
    roll_call_data: dict | None,
    bioguide_id: str,
    last_name: str | None = None,
    state: str | None = None,
) -> str | None:
    """Extract a representative's vote from House roll call data.

    House Clerk XML includes bioguide IDs, so we prefer matching on that.
    Falls back to last_name + state like the Senate matcher.
    """
    if not roll_call_data or not roll_call_data.get("members"):
        return None

    if bioguide_id:
        for member in roll_call_data["members"]:
            if member.get("bioguideId") == bioguide_id:
                return member.get("voteCast") or None

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


def _unwrap_list(value: object) -> list[dict]:
    """Normalize Congress.gov API responses that may be dicts with an 'item' key."""
    if isinstance(value, dict):
        return value.get("item", []) if "item" in value else []
    return value if isinstance(value, list) else []


def find_house_roll_call(actions: list[dict] | None) -> dict | None:
    """Try to find the House roll call vote for a bill from its actions.

    Returns:
        Dict with year and rollCallNumber keys, or None.
    """
    if not actions:
        return None

    import re as _re

    for action in (_unwrap_list(actions) if not isinstance(actions, list) else actions):
        text = (action.get("text") or "").lower()
        if (
            "passed house" in text
            or "house agreed" in text
            or "roll no" in text
        ) and action.get("recordedVotes"):
            for rv in _unwrap_list(action["recordedVotes"]):
                if rv.get("chamber") == "House" and rv.get("rollNumber"):
                    url = rv.get("url", "")
                    year_match = _re.search(r"/evs/(\d{4})/", url)
                    if year_match:
                        year = int(year_match.group(1))
                    else:
                        # No year in the URL — fall back to the recorded
                        # vote's or action's own date. Never guess a fixed
                        # year: House Clerk roll numbers restart every year,
                        # so a wrong year resolves to a REAL, different roll
                        # call and silently attributes the wrong votes.
                        date_match = _re.match(
                            r"(\d{4})-",
                            rv.get("date") or action.get("actionDate") or "",
                        )
                        if not date_match:
                            continue
                        year = int(date_match.group(1))
                    return {
                        "year": year,
                        "rollCallNumber": rv.get("rollNumber"),
                    }

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

    for action in (_unwrap_list(actions) if not isinstance(actions, list) else actions):
        text = (action.get("text") or "").lower()
        if (
            "passed senate" in text
            or "senate agreed" in text
            or "cloture" in text
            or "roll call vote" in text
        ) and action.get("recordedVotes"):
            for rv in _unwrap_list(action["recordedVotes"]):
                if rv.get("chamber") == "Senate" and rv.get("rollNumber"):
                    return {
                        "congress": rv.get("congress"),
                        "session": rv.get("sessionNumber"),
                        "rollCallNumber": rv.get("rollNumber"),
                    }

    return None
