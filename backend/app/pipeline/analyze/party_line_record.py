"""Each member's party-line record over the whole current Congress (v6.20).

Constituent Alignment's vote part compares how often a member breaks with
their party against how often members of their party in similar seats do.
Until v6.20 that rate was read off the member's stored votes, a sample of
the chamber's latest 120 roll calls plus key bills, and every vote against
the party counted. It is now measured on every roll call the chamber
recorded this Congress (congress_activity stores each one with every
member's position), under two rules the research note tests against
election results (docs/research/constituent-alignment.md, sections 11-12):

- A break counts only toward the other party. On that roll call, the party
  members who broke sit, on average, nearer the other party (DW-NOMINATE
  first dimension) than their party as a whole. Hardliners voting down their
  own party's bill from the flank vote against it too, but that is not
  independence toward the seat, and the member's flank position is already
  scored, by position congruence. Such votes are kept as flankBreaks: shown,
  not counted.
- Each measure counts once. A nominee's cloture and confirmation votes, or a
  bill's motion to proceed, cloture and passage, are one decision voted on
  several times; in the 119th Senate 37% of roll calls repeat a measure
  already voted on, nearly all of them cloture votes. An amendment, a motion
  to commit or to waive, is its own question.

Housekeeping questions (normalize_votes.is_housekeeping) and the majority
leader's reconsider switch are left out, as everywhere else.
"""

import json
import logging
import re
from collections import defaultdict

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import RollCall, RollCallPosition
from app.pipeline.analyze.score_calculator import _member_ideal_points
from app.pipeline.transform.committee_data import load_leadership_tenures
from app.pipeline.transform.normalize_votes import (
    _determine_party_alignment,
    _normalize_for_match,
    resolve_senate_lis_ids,
    compute_party_split,
    is_housekeeping,
    is_reconsider_switch,
    majority_leader_spans,
)

logger = logging.getLogger(__name__)

# Questions that are a stage of the measure itself. Anything else (an
# amendment, a motion to commit, to waive, a point of order) is its own
# question, even when it names the bill.
_MEASURE_STAGE_RE = re.compile(
    r"cloture|nomination|motion to proceed|passage|pass\b|joint resolution|concurrent resolution"
    r"|the resolution|conference report|ratification|veto|concur",
    re.I,
)
_AMENDMENT_RE = re.compile(r"amdt|amendment", re.I)
# Senate nominations carry their PN number in the question ("On the Cloture
# Motion PN11-19"), not in bill_id.
_NOMINATION_RE = re.compile(r"\bPN\d+(?:-\d+)?")
_VOTES = {"yea": "Yea", "aye": "Yea", "yes": "Yea", "nay": "Nay", "no": "Nay"}


def measure_key(question: str | None, bill_id: str | None) -> str | None:
    """The measure a roll call is one stage of (a nomination's PN number or
    the bill), or None when the question stands alone. A vote to concur in
    the other chamber's amendment is a stage of the bill; any other
    amendment question is its own."""
    q = question or ""
    if not _MEASURE_STAGE_RE.search(q):
        return None
    if _AMENDMENT_RE.search(q) and "concur" not in q.lower():
        return None
    nomination = _NOMINATION_RE.search(q)
    return nomination.group(0) if nomination else (bill_id or None)


def load_record(text: str | None) -> dict | None:
    """A stored party_line_record column, or None."""
    try:
        record = json.loads(text) if text else None
    except (TypeError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _toward_other_party(party: str, cast: list[tuple]) -> bool:
    """Whether the members of `party` who broke on this roll call sit nearer
    the other party than their party does (mean dim1: Democrats negative,
    Republicans positive)."""
    everyone = [d for _, p, _, _, d in cast if p == party and d is not None]
    broke = [d for _, p, _, with_party, d in cast if p == party and not with_party and d is not None]
    if not everyone or not broke:
        # ponytail: no NOMINATE position for any defector (a member Voteview
        # hasn't estimated yet) counts the break, as every break did before
        # v6.20; only reachable in a Congress's first weeks.
        return True
    mean, broke_mean = sum(everyone) / len(everyone), sum(broke) / len(broke)
    return broke_mean > mean if party == "D" else broke_mean < mean


def party_line_records(db: Session, chamber: str, members: list[dict]) -> list[dict | None]:
    """The party-line record of each member dict (bioguideId,
    lastNameForVoteMatch, state, party, leadershipTitle, votingRecord's
    effectiveParty), aligned with `members`:

        {"congress", "votes": measures voted on,
         "breaks": [{"rollCall", "vote"}], "flankBreaks": [...]}

    each break named by its latest roll call on the measure, newest first.
    None for a member none of whose positions the roll calls hold, and for
    everyone when the chamber has no stored roll calls: a name the roll calls
    spell differently must not read as a member who never voted, so the
    score then reads the member's stored votes as it did before v6.20."""
    congress = db.query(func.max(RollCall.congress)).filter(RollCall.chamber == chamber).scalar()
    if congress is None:
        logger.warning("No %s roll calls stored: Constituent Alignment reads stored votes", chamber)
        return [None] * len(members)
    rolls = {rc.id: rc for rc in db.query(RollCall).filter_by(chamber=chamber, congress=congress)}

    def key(member_id: str, last_name: str, state: str):
        # The House's roll calls carry the bioguide id; the Senate's only a
        # name and state (extract_senator_vote matches the same way).
        return member_id if chamber == "house" else (_normalize_for_match(last_name or ""), (state or "").upper())

    index: dict = defaultdict(list)
    for i, m in enumerate(members):
        index[key(m.get("bioguideId") or "", m.get("lastNameForVoteMatch") or "", m.get("state") or "")].append(i)

    positions: dict[int, list] = defaultdict(list)
    for p in db.query(
        RollCallPosition.roll_call_id, RollCallPosition.member_id, RollCallPosition.last_name,
        RollCallPosition.first_name, RollCallPosition.state, RollCallPosition.party, RollCallPosition.position,
    ).filter(RollCallPosition.roll_call_id.in_(list(rolls))):
        positions[p.roll_call_id].append(p)

    # A seat passed to someone of the same surname (resolve_senate_lis_ids):
    # only the member's own LIS id is theirs.
    lis_of: dict[int, str] = {}
    if chamber == "senate":
        resolved = resolve_senate_lis_ids(
            [{**m, "id": i} for i, m in enumerate(members)],
            [{"lisId": p.member_id, "firstName": p.first_name, "lastName": p.last_name, "state": p.state}
             for ps in positions.values() for p in ps],
        )
        lis_of = {int(i): lis for i, lis in resolved.items()}

    def find(p) -> int | None:
        found_at = index.get(key(p.member_id, p.last_name, p.state)) or []
        found_at = [i for i in found_at if i not in lis_of or lis_of[i] == p.member_id]
        if len(found_at) > 1:
            # Two senators of one state can share a last name: the roll
            # call's first name tells them apart.
            first = _normalize_for_match(p.first_name or "")
            found_at = [i for i in found_at if _normalize_for_match((members[i].get("name") or "").split(" ")[0]) == first]
        return found_at[0] if len(found_at) == 1 else None

    parties = [(m.get("votingRecord") or {}).get("effectiveParty") or m.get("party") for m in members]
    tenures = load_leadership_tenures()
    spans = [majority_leader_spans(m.get("leadershipTitle"), tenures.get(m.get("bioguideId"))) for m in members]
    dim1 = (_member_ideal_points(chamber) or {}).get("members") or {}

    found: set[int] = set()
    # member -> measure -> [(date, session, number, ref, vote, kind)], kind "with" /
    # "break" / "flank".
    stages: list[dict] = [defaultdict(list) for _ in members]
    for rid, rc in rolls.items():
        ps = positions.get(rid, [])
        label = None if is_housekeeping(rc.question) else compute_party_split(
            {"members": [{"party": p.party, "voteCast": p.position} for p in ps]},
        )
        cast = []
        for p in ps:
            i = find(p)
            if i is not None:
                found.add(i)
            vote = _VOTES.get((p.position or "").strip().lower())
            if label not in ("R", "D") or vote is None:
                continue
            party = parties[i] if i is not None else p.party
            if party not in ("R", "D"):
                # A party line is either party's: a member of neither (a
                # roll call's "I" with no caucus resolved, an unusual code)
                # has none to break with, and toward[] below knows only these.
                continue
            switch = is_reconsider_switch({"motionRejected": rc.rejected, "rollCallDate": rc.date}, spans[i]) if i is not None else None
            with_party = _determine_party_alignment(party, vote, label, reconsider_switch=switch)
            if with_party is None:
                continue
            bioguide = p.member_id if chamber == "house" else (members[i].get("bioguideId") if i is not None else None)
            cast.append((i, party, vote, with_party, dim1.get(bioguide)))
        if not cast:
            continue
        toward = {party: _toward_other_party(party, cast) for party in ("R", "D")}
        unit = measure_key(rc.question, rc.bill_id) or f"rc-{rid}"
        ref = f"{rc.chamber}-{rc.congress}-{rc.session}-{rc.number}"
        for i, party, vote, with_party, _ in cast:
            if i is not None:
                kind = "with" if with_party else ("break" if toward[party] else "flank")
                stages[i][unit].append((rc.date, rc.session, rc.number, ref, vote, kind))

    out: list[dict | None] = []
    for i in range(len(members)):
        if i not in found:
            out.append(None)
            continue
        breaks, flank = [], []
        for votes in stages[i].values():
            counted = [v for v in votes if v[5] == "break"]
            if counted:
                breaks.append(max(counted))
            elif any(v[5] == "flank" for v in votes):
                flank.append(max(v for v in votes if v[5] == "flank"))
        out.append({
            "congress": congress,
            "votes": len(stages[i]),
            "breaks": [{"rollCall": v[3], "vote": v[4]} for v in sorted(breaks, reverse=True)],
            "flankBreaks": [{"rollCall": v[3], "vote": v[4]} for v in sorted(flank, reverse=True)],
        })
    missing = [members[i].get("name") or members[i].get("bioguideId") for i in range(len(members)) if i not in found]
    if missing:
        logger.warning(
            "%d %s members matched no stored roll-call position (scored on stored votes): %s",
            len(missing), chamber, ", ".join(map(str, missing[:10])),
        )
    return out
