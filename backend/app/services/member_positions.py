"""Every position a member recorded on the chamber's roll calls this
Congress, for the public API's /senators/{id}/votes and
/representatives/{id}/votes.

Read from the roll calls congress_activity stores, every one with every
member's position: the same record Constituent Alignment's party-line
record is measured on (party_line_record). The member's stored vote rows
(key_votes) are a selection, about a fifth of the record, and served
only on the profile; they lend a position its policy area where the
scoring run classified it.

Each position says whether it sided with the member's party and, where the
score has a party-line record, whether the score counted it as a break.
The first is read the way the scoring run reads every vote: the roll call's
own party split (normalize_votes.compute_party_split, not for housekeeping
questions), the member's caucus party, and the majority leader's reconsider
switch (is_reconsider_switch). The second is the stored record's own list:
a measure counts once, at its latest roll call, and only a break toward the
other party counts, so most votes against the party are not counted breaks.
"""

from collections import defaultdict

from sqlalchemy.orm import Session

from app.config import settings
from app.models import KeyVote, RepKeyVote, Representative, RollCall, RollCallPosition, Senator
from app.pipeline.analyze.party_line_record import load_record
from app.pipeline.transform.committee_data import load_leadership_tenures
from app.pipeline.transform.normalize_votes import (
    _determine_party_alignment,
    compute_party_split,
    is_housekeeping,
    is_reconsider_switch,
    majority_leader_spans,
)
from app.services.bill_record import MemberLinks, roll_call_summaries
from app.services.pagination import paginate_bounds

_MODEL = {"senate": Senator, "house": Representative}
_STORED_VOTES = {"senate": (KeyVote, KeyVote.senator_id), "house": (RepKeyVote, RepKeyVote.representative_id)}
# The House records "Aye"/"No" on a recorded vote: the same Yea and Nay.
_VOTE_WORD = {"yea": "Yea", "aye": "Yea", "nay": "Nay", "no": "Nay", "present": "Present", "not voting": "Not Voting"}


def roll_call_member_ids(db: Session, chamber: str, congress: int, member_ids: dict[str, str]) -> dict[str, str]:
    """{Civitas id: the id the chamber's roll calls name them by}: the
    bioguide id for the House; for the Senate, the LIS id, found the way
    the bill pages link a Senate vote to a member (MemberLinks)."""
    if chamber == "house":
        rows = db.query(Representative.id, Representative.bioguide_id).filter(
            Representative.id.in_(list(member_ids.values()))).all()
        return {i: b for i, b in rows if b}
    links = MemberLinks(db)
    people = db.query(
        RollCallPosition.member_id, RollCallPosition.last_name, RollCallPosition.state,
    ).join(RollCall, RollCall.id == RollCallPosition.roll_call_id).filter(
        RollCall.chamber == "senate", RollCall.congress == congress,
    ).distinct().all()
    wanted = set(member_ids.values())
    out: dict[str, str] = {}
    for lis, last, state in people:
        page = links.page(last_name=last, state=state, lis_id=lis)
        sid = page.rsplit("/", 1)[-1] if page else None
        if sid in wanted:
            out[sid] = lis
    return out


def vote_word(position: str) -> str:
    """Yea, Nay, Present or Not Voting; any other recorded word as the
    chamber wrote it ("Guilty" on an impeachment article, a candidate's name
    in an election of the Speaker)."""
    p = (position or "").strip()
    return _VOTE_WORD.get(p.lower(), p)


def member_positions(db: Session, chamber: str, member_id: str, page: int, per_page: int) -> dict | None:
    """One page of the member's positions, newest first, or None for no
    such member. `chamberRollCalls` is how many roll calls Civitas holds for
    the chamber this Congress, so a total below it reads as what it is
    (votes cast before the member took office, or missed), not as a record
    that ends early."""
    member = db.get(_MODEL[chamber], member_id)
    if member is None:
        return None
    congress = settings.CURRENT_CONGRESS
    held = db.query(RollCall.id).filter(RollCall.chamber == chamber, RollCall.congress == congress).count()
    vote_id = roll_call_member_ids(db, chamber, congress, {"m": member_id}).get(member_id)
    query = db.query(RollCall, RollCallPosition).join(
        RollCallPosition, RollCallPosition.roll_call_id == RollCall.id,
    ).filter(RollCall.chamber == chamber, RollCall.congress == congress, RollCallPosition.member_id == (vote_id or ""))
    total = query.count() if vote_id else 0
    total_pages, page = paginate_bounds(total, page, per_page)
    rows = (query.order_by(RollCall.date.desc(), RollCall.session.desc(), RollCall.number.desc())
            .offset((page - 1) * per_page).limit(per_page).all()) if total else []

    refs = {rc.id: f"{rc.chamber}-{rc.congress}-{rc.session}-{rc.number}" for rc, _ in rows}
    summaries = roll_call_summaries(db, list(refs.values()))
    everyone: dict[int, list] = defaultdict(list)
    for p in db.query(RollCallPosition.roll_call_id, RollCallPosition.party, RollCallPosition.position).filter(
        RollCallPosition.roll_call_id.in_(list(refs)),
    ):
        everyone[p.roll_call_id].append({"party": p.party, "voteCast": p.position})
    model, owner = _STORED_VOTES[chamber]
    stored = {v.roll_call: v for v in db.query(model).filter(owner == member_id, model.roll_call.in_(list(refs.values())))}
    record = load_record(member.party_line_record)
    breaks = {b["rollCall"] for b in (record or {}).get("breaks") or []}
    party = member.caucus_party or member.party
    spans = majority_leader_spans(member.leadership_title, load_leadership_tenures().get(member.bioguide_id))

    entries = []
    for rc, pos in rows:
        ref = refs[rc.id]
        vote = vote_word(pos.position)
        label = None if is_housekeeping(rc.question, chamber) else compute_party_split({"members": everyone[rc.id]})
        switch = is_reconsider_switch({"motionRejected": rc.rejected, "rollCallDate": rc.date}, spans)
        with_party = (_determine_party_alignment(party, vote, label, reconsider_switch=switch)
                      if label in ("R", "D") and vote in ("Yea", "Nay") else None)
        kept = stored.get(ref)
        summary = summaries.get(ref) or {}
        entries.append({
            "billId": rc.bill_id or (kept.bill_id if kept else None),
            "billName": (kept.bill_name if kept else None) or summary.get("billShortTitle") or rc.title or rc.question,
            "date": rc.date, "vote": vote, "votedWithParty": with_party,
            "partyLeaning": label,
            "countedAsBreak": (ref in breaks) if record else None,
            "policyArea": kept.policy_area if kept else None,
            "rollCall": summary,
        })
    return {"total": total, "page": page, "perPage": per_page, "totalPages": total_pages,
            "congress": congress, "chamberRollCalls": held, "votes": entries}
