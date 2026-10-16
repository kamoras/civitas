"""What kind of record a roll call is, and how its count is stated.

Most roll calls put a question, and the count is yeas to nays. Three
records don't read that way, and printing "yeas–nays" and the result for
them states something false:

- **A Senate tie the Vice President broke.** The Vice President votes only
  to break a tie (Constitution, Article I, section 3) and is not a member,
  so the yeas and nays stay equal and the record names the tie-breaker's
  vote apart (RollCall.tie_breaker_*): "Bill Passed 50–50" without it.
- **An election of the Speaker.** Each member's recorded vote is a
  candidate's name (the Clerk's "Doe (TX)"), which a yea/nay tally reads
  as not voting: 0–0 with 434 not voting. The Clerk's result names the
  candidate with the most votes, elected or not (it names the leader of a
  ballot no one won, and the House then votes again), so whether anyone was elected is read
  from the count, by the House's rule: a majority of the votes cast for a
  person by name (present and not voting are not votes for anyone).
- **A quorum call** ("Call by States", "Call of the House"): members answer
  present and nothing is decided, so it has no yeas, no nays and no result,
  whatever the record's result field says ("Passed").

Every figure comes from the stored record and its positions; nothing here
is classified. A kind is read from the positions' own vocabulary: a
position outside floor_logs' yea/nay words, "Present" and "Not Voting" is
a candidate's name.
"""

from collections import Counter

from sqlalchemy.orm import Session

from app.models import RollCall, RollCallPosition
from app.pipeline.fetch.floor_logs import NAY_POSITIONS, YEA_POSITIONS

_NOT_A_CANDIDATE = YEA_POSITIONS | NAY_POSITIONS | {"present", "not voting"}


def candidate_of(position: str | None) -> str | None:
    """The candidate a recorded vote names, or None for a yea, nay, present
    or not voting."""
    p = (position or "").strip()
    return p if p and p.lower() not in _NOT_A_CANDIDATE else None


def _maybe_election(rc: RollCall) -> bool:
    """A candidate vote is tallied as not voting (floor_logs.tally), so an
    election stores no yeas, nays or present; only those need positions."""
    return not (rc.yeas or rc.nays or rc.present) and bool(rc.not_voting)


def shape(rc: RollCall, positions: list[RollCallPosition] | None = None) -> dict:
    """The fields every surface that shows a roll call adds to it:

    kind: "question" | "election" | "quorum"
    tally: the count in words ("50–50, Vice President of the United States
        voted Yea", "433 present", "Doe (TX) 218, Roe 215, Poe 1")
    resultLabel: what was decided ("Bill Passed", "Doe (TX) elected",
        "No one elected", "Quorum call")
    rejected: the record's own, except null where nothing was put to a vote
    tieBreaker: {"byWhom", "vote"} or null
    candidates: [{"name", "votes"}], most votes first; [] unless an election
    notVoting: members recorded not voting (an election's candidate votes
        are not counted here, as the stored tally counts them)

    `positions` are needed only for an election; without them one reads as
    a question with no votes."""
    candidates: list[dict] = []
    if positions and _maybe_election(rc):
        counted = Counter(c for c in (candidate_of(p.position) for p in positions) if c)
        candidates = [{"name": n, "votes": v} for n, v in sorted(counted.items(), key=lambda kv: (-kv[1], kv[0]))]
    tie = ({"byWhom": rc.tie_breaker_by, "vote": rc.tie_breaker_vote}
           if rc.tie_breaker_by and rc.tie_breaker_vote else None)
    out = {"tieBreaker": tie, "candidates": candidates, "rejected": rc.rejected, "notVoting": rc.not_voting}
    if candidates:
        cast = sum(c["votes"] for c in candidates)
        top = candidates[0]
        # A majority of the votes for a person, not of the House: members
        # answering present don't count, so 216 of 428 such votes elects
        # even with 6 present.
        elected = top["votes"] * 2 > cast
        return {
            **out, "kind": "election", "rejected": None, "notVoting": rc.not_voting - cast,
            "tally": ", ".join(f"{c['name']} {c['votes']}" for c in candidates),
            "resultLabel": f"{top['name']} elected" if elected else "No one elected",
        }
    if not (rc.yeas or rc.nays) and rc.present:
        return {**out, "kind": "quorum", "rejected": None, "tally": f"{rc.present} present",
                "resultLabel": "Quorum call"}
    tally = f"{rc.yeas}–{rc.nays}"
    if tie:
        tally += f", {tie['byWhom']} voted {tie['vote']}"
    return {**out, "kind": "question", "tally": tally, "resultLabel": rc.result or ""}


def shapes(db: Session, rows: list[RollCall],
           positions: dict[int, list[RollCallPosition]] | None = None) -> dict[int, dict]:
    """{roll call id: shape} for many roll calls, reading positions only for
    the few that may be elections when the caller hasn't loaded them."""
    positions = dict(positions or {})
    need = [rc.id for rc in rows if _maybe_election(rc) and rc.id not in positions]
    if need:
        for p in db.query(RollCallPosition).filter(RollCallPosition.roll_call_id.in_(need)):
            positions.setdefault(p.roll_call_id, []).append(p)
    return {rc.id: shape(rc, positions.get(rc.id)) for rc in rows}
