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
  members who broke sit, on average, nearer the other party (first-dimension
  position from the chamber's Voteview section, the current or the last
  Congress's, each weighted by its reliability and read from its party's
  mean since v6.27; until a member's new record reaches prior_until_votes,
  their last Congress's full record, where they have one, decides their
  side) than their party as a whole. Hardliners voting down their own
  party's bill from the flank vote against it too, but that is not
  independence toward the seat, and the member's flank position is measured
  by position congruence (scored once the Congress's section passes its
  gates). Such votes are kept as flankBreaks: shown, not counted.
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

from app.models import Representative, RollCall, RollCallPosition, Senator
from app.pipeline.analyze.score_calculator import _member_ideal_points, position_confidence
from app.pipeline.transform.committee_data import load_leadership_tenures
from app.pipeline.transform.normalize_votes import (
    _determine_party_alignment,
    _normalize_for_match,
    _same_first_name,
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
    Republicans positive). Each cast entry's position is (dim1, weight):
    means are weighted by the position's reliability (v6.27,
    score_calculator.position_confidence), so among several defectors one
    resting on a few roll calls barely moves their mean. A lone defector's
    side is its own position's sign against the party's, whatever its
    weight, which is why party_line_records reads a member's last-Congress
    full record until their new record reaches prior_until_votes (a full
    record by default)."""
    everyone = [d for _, p, _, _, d in cast if p == party and d is not None and d[1] > 0]
    broke = [d for _, p, _, with_party, d in cast if p == party and not with_party and d is not None and d[1] > 0]
    if not broke:
        # ponytail: no usable position for any defector (a member Voteview
        # hasn't estimated) counts the break, as every break did before
        # v6.20; mostly a Congress's first weeks.
        return True

    def mean(points):
        return sum(x * w for x, w in points) / sum(w for _, w in points)
    # Positions are read from their party's mean in their section
    # (party_line_records), so with no usable position for anyone who voted
    # with the party the party's own center, 0, stands in.
    reference = mean(everyone) if len(everyone) > len(broke) else 0.0
    return mean(broke) > reference if party == "D" else mean(broke) < reference


def _words(text: str) -> str:
    """A name as space-padded words, accents stripped and uppercased, with
    punctuation dropped, so one name can be found as whole words in another."""
    return f" {' '.join(re.sub(r'[^\w ]', ' ', _normalize_for_match(text)).split())} "


def _departed_senators(db: Session, members: list[dict], positions: dict[int, list]) -> list[dict]:
    """Stored senators `members` leaves out (one who left during the
    Congress is off the sitting roster), each with the roll calls' own
    spelling of their last name: the one surname among their state's
    voters that is a whole word of their stored name, and only when their
    first name matches one LIS id that voted under it which no member
    passed has the first name of (a predecessor who never voted, or a
    successor who hasn't yet, takes no one's votes; each LIS id goes to one
    senator, whatever order they are stored in). Anyone else is left out,
    as before."""
    have = {m.get("bioguideId") for m in members}
    voters: dict[str, set[str]] = defaultdict(set)
    people: dict[tuple, dict[str, str]] = defaultdict(dict)
    for ps in positions.values():
        for p in ps:
            if p.last_name and p.state:
                voters[p.state.upper()].add(p.last_name)
                people[(_normalize_for_match(p.last_name), p.state.upper())][p.member_id] = p.first_name or ""
    firsts: dict[tuple, list[str]] = defaultdict(list)
    for m in members:
        firsts[(_normalize_for_match(m.get("lastNameForVoteMatch") or ""), (m.get("state") or "").upper())].append(
            (m.get("name") or "").split(" ")[0])
    free = {k: {lis: first for lis, first in ids.items()
                if not any(_same_first_name(own, first) for own in firsts.get(k, ()))}
            for k, ids in people.items()}
    out = []
    for bioguide, name, state, party in db.query(
            Senator.bioguide_id, Senator.name, Senator.state, Senator.party).order_by(Senator.bioguide_id):
        if not bioguide or bioguide in have:
            continue
        words = _words(name or "")
        last = {ln for ln in voters.get((state or "").upper(), ()) if _words(ln) in words}
        if len(last) != 1:
            continue
        surname = last.pop()
        k = (_normalize_for_match(surname), (state or "").upper())
        mine = [lis for lis, first in free.get(k, {}).items() if _same_first_name((name or "").split(" ")[0], first)]
        if len(mine) == 1:
            del free[k][mine[0]]
            out.append({"bioguideId": bioguide, "name": name, "state": state, "party": party,
                        "lastNameForVoteMatch": surname})
    return out


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

    positions: dict[int, list] = defaultdict(list)
    for p in db.query(
        RollCallPosition.roll_call_id, RollCallPosition.member_id, RollCallPosition.last_name,
        RollCallPosition.first_name, RollCallPosition.state, RollCallPosition.party, RollCallPosition.position,
    ).filter(RollCallPosition.roll_call_id.in_(list(rolls))):
        positions[p.roll_call_id].append(p)

    # The Senate's roll calls tie a vote to a position only through these
    # members, and the roster lists sitting senators only: a senator who
    # left during the Congress is added (records not returned), so their
    # votes are read with their position like everyone else's. The House's
    # roll calls carry the bioguide id and need nothing added.
    scored = len(members)
    if chamber == "senate":
        members = members + _departed_senators(db, members, positions)

    index: dict = defaultdict(list)
    for i, m in enumerate(members):
        index[key(m.get("bioguideId") or "", m.get("lastNameForVoteMatch") or "", m.get("state") or "")].append(i)

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
            found_at = [i for i in found_at
                        if _same_first_name((members[i].get("name") or "").split(" ")[0], p.first_name or "")]
        return found_at[0] if len(found_at) == 1 else None

    # The chamber's stored members' parties (an independent's caucus party):
    # every member without a voting record (the rest of the chamber, and a
    # departed senator added above) reads its party from here, and each
    # section's party centers cover the whole chamber however few members
    # this run scores.
    model = Senator if chamber == "senate" else Representative
    stored: dict[str, str] = {
        b: caucus or p for b, p, caucus in db.query(model.bioguide_id, model.party, model.caucus_party) if b}
    parties = [(m.get("votingRecord") or {}).get("effectiveParty") or stored.get(m.get("bioguideId") or "")
               or m.get("party") for m in members]
    tenures = load_leadership_tenures()
    spans = [majority_leader_spans(m.get("leadershipTitle"), tenures.get(m.get("bioguideId"))) for m in members]
    # Any Congress's section: this rule needs only which side of their party
    # the defectors sit, and positions carry from one Congress to the next,
    # so early in a new Congress the last positions classify its breaks
    # rather than every flank break counting (stale beats punitive). Once
    # the new Congress's section is in, its positions rest on a few roll
    # calls at first, so a member's last-Congress full record ("prior",
    # voteview.previous_positions) decides their side until their new record
    # reaches prior_until_votes (position_confidence.json: a full record,
    # unless a shorter switch is shown to place members better; a section
    # without the key falls back to a full record's count, or without that
    # to the more reliable). Each section's positions are read from their
    # own party's mean in that section, so a party-wide shift between the
    # two Congresses can't move a member who is read from one against a
    # party read from the other.
    ideal = _member_ideal_points(chamber) or {}

    party_of: dict[str, str] = {}
    if chamber == "house":
        # The House's roll calls name every member's bioguide and party; a
        # stored caucus party (an independent's) reads over them.
        party_of.update({p.member_id: p.party for ps in positions.values() for p in ps if p.member_id})
    party_of.update(stored)
    party_of.update({m.get("bioguideId"): parties[i] for i, m in enumerate(members) if m.get("bioguideId")})

    def weighted(section) -> dict:
        # bioguide -> (position from its party's mean, reliability weight)
        if not isinstance(section, dict):
            return {}
        reliability = section.get("reliability") if isinstance(section.get("reliability"), dict) else None
        counts = section.get("votes") or {}
        points = {b: (float(x), position_confidence(counts.get(b), reliability))
                  for b, x in (section.get("members") or {}).items()}
        # A position the section records under the other major party (a
        # switch since) is not that party's, nor evidence of the member's
        # side of their new one: it stays out of the party's mean and is
        # never read for the member, in any section (a stated choice).
        cast = section.get("parties") or {}
        other = {b for b in points if {cast.get(b), party_of.get(b)} == {"R", "D"}}
        points = {b: xw for b, xw in points.items() if b not in other}

        def party(b):
            # A member no longer stored (deleted after the grace period) and
            # absent from the roll calls reads the party the section records.
            return party_of.get(b) or cast.get(b)
        center = {}
        for side in ("R", "D"):
            mine = [(x, w) for b, (x, w) in points.items() if party(b) == side and w > 0]
            if mine:
                center[side] = sum(x * w for x, w in mine) / sum(w for _, w in mine)
        return {b: (x - center.get(party(b), 0.0), w) for b, (x, w) in points.items()}
    # The positions of the roll calls' Congress lead: the section's, or, when
    # the section is already the next Congress's (refreshed after Jan 3,
    # before its first roll call is stored), the prior it keeps; a section
    # carried from the last Congress (its successor not yet in) is itself
    # the last Congress's, and its own prior would be older still.
    kept = ideal.get("prior") if isinstance(ideal.get("prior"), dict) else {}

    def of(section) -> int | None:
        return int(section["congress"]) if section.get("congress") is not None else None
    if kept and of(ideal) != congress and of(kept) == congress:
        main, prior = kept, {}
    else:
        main, prior = ideal, kept if of(ideal) == congress else {}
    dim1 = weighted(main)
    for b, xw in weighted(ideal if main is kept else {}).items():
        dim1.setdefault(b, xw)
    reliability = ideal.get("reliability") if isinstance(ideal.get("reliability"), dict) else {}
    full = reliability.get("reference_votes")
    until = reliability.get("prior_until_votes", full)
    counts = main.get("votes") or {}
    prior_counts = prior.get("votes") or {}
    # A member who switched parties, during this Congress or between the
    # two: their last record was cast in another party. A stated choice
    # (nothing measured such a record as evidence of their side of the new
    # one): it is never read for them, so with no usable position this
    # Congress they have none, and their breaks are classified on the
    # other defectors' positions (counting when no defector has one).
    now, then = main.get("parties") or {}, prior.get("parties") or {}
    switched = set(main.get("switched") or ()) | {b for b in then if b in now and then[b] != now[b]}
    for b, (x, w) in weighted(prior).items():
        if b in switched:
            continue
        if b not in dim1:
            dim1[b] = (x, w)
        elif full:
            # A last full record decides until the new record reaches
            # prior_until_votes (calibrate_position_confidence: a full record,
            # unless a switch short of one is shown to place members better
            # out of bag). Only a full last record was measured, so a thin
            # one doesn't replace this Congress's.
            n, last = counts.get(b), prior_counts.get(b)
            if (n is None or n < float(until)) and last is not None and float(last) >= float(full):
                dim1[b] = (x, w)
            elif dim1[b][1] == 0 and w > 0:
                # This Congress's position counts for nothing yet (no votes):
                # read on the last one, however short (a stated choice; the
                # alternative wasn't measured).
                dim1[b] = (x, w)
        elif w > dim1[b][1]:
            dim1[b] = (x, w)

    found: set[int] = set()
    # member -> measure -> [(date, session, number, ref, vote, kind)], kind "with" /
    # "break" / "flank".
    stages: list[dict] = [defaultdict(list) for _ in members]
    for rid, rc in rolls.items():
        ps = positions.get(rid, [])
        # The roll call's own parties, as its stored partySplit reads them.
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
            # A member the run doesn't score: their stored caucus party where
            # the roll call names them by bioguide (the House), else its own.
            party = parties[i] if i is not None else (
                party_of.get(p.member_id) if chamber == "house" and p.member_id else None) or p.party
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
    for i in range(scored):
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
    missing = [members[i].get("name") or members[i].get("bioguideId") for i in range(scored) if i not in found]
    if missing:
        logger.warning(
            "%d %s members matched no stored roll-call position (scored on stored votes): %s",
            len(missing), chamber, ", ".join(map(str, missing[:10])),
        )
    return out
