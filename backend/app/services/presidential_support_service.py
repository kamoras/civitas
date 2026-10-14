"""Read side of "votes with the president's stated position"
(pipeline/analyze/presidential_support.py writes it). ORM reads and
arithmetic only: no model is loaded here."""

from sqlalchemy.orm import Session

from app.models import PolicyStatement, PresidentialSupport, Representative, RollCall, RollCallPosition, Senator
from app.pipeline.analyze.presidential_support import (
    PRESIDENTIAL_SUPPORT_REFERENCE,
    counted_votes,
    wilson_interval,
    with_president,
)
from app.pipeline.fetch.omb_statements import INDEX_URL
from app.services.bill_record import MemberLinks

METHOD = {
    "source": "Statements of Administration Policy, Office of Management and Budget",
    "sourceUrl": INDEX_URL,
    "counted": (
        "A roll call counts when it is the chamber's final vote on a measure (passage, adoption, concurrence, "
        "a veto override), or a vote on an amendment a statement names, and a Statement of Administration "
        "Policy addressed to that chamber, issued on or before the vote and not followed by a later statement "
        "on the measure, supports or opposes it. Procedural votes (cloture, motions to proceed, discharge, "
        "table or recommit) are not counted: no statement addresses them. Present and not voting are not votes."
    ),
    "position": (
        "Read from the statement's opening and closing paragraphs by embedding similarity to written "
        "descriptions of support, opposition and neither (zero-shot classification; Yin, Hay & Roth 2019). "
        "The sentence each position was read from is shown with the vote."
    ),
    "expected": (
        "Per chamber and party, a fractional-response logit (Papke & Wooldridge 1996) of members' rates on their "
        "seat's partisan lean (Cook PVI, signed toward the member's party), each member weighted by their "
        "counted votes and fitted on this Congress's members every night. A member's expected rate is their "
        "party's fit at their seat's lean. No shrinkage is applied."
    ),
    "interval": (
        "95% Wilson score interval for the member's rate (Wilson 1927; Agresti & Coull 1998). The difference's "
        "range is that interval minus the expected rate; the expected rate's own uncertainty is not included."
    ),
    "neutral": "Voting with or against the president's stated position is neither good nor bad.",
}


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 4)


def summary(row: PresidentialSupport) -> dict:
    """Every number for one member: their rate and its interval, the
    expected rate, the difference and its range, and which side of the
    expectation the interval sits on ("more" / "less"), or "within" when it
    includes it."""
    rate = row.with_president / row.votes if row.votes else None
    interval = wilson_interval(row.with_president, row.votes)
    out = {
        "withPresident": row.with_president,
        "votes": row.votes,
        "rate": _r(rate),
        "rateLow": _r(interval[0]) if interval else None,
        "rateHigh": _r(interval[1]) if interval else None,
        "expected": _r(row.expected),
        "difference": None,
        "differenceLow": None,
        "differenceHigh": None,
        "comparedWithExpected": None,
    }
    if row.expected is not None and interval is not None:
        out["difference"] = _r(rate - row.expected)
        out["differenceLow"] = _r(interval[0] - row.expected)
        out["differenceHigh"] = _r(interval[1] - row.expected)
        out["comparedWithExpected"] = (
            "more" if interval[0] > row.expected else "less" if interval[1] < row.expected else "within"
        )
    return out


def _vote_row(rc: RollCall, statement: PolicyStatement) -> dict:
    return {
        "chamber": rc.chamber,
        "congress": rc.congress,
        "session": rc.session,
        "rollCall": rc.number,
        "date": rc.date,
        "question": rc.question,
        "title": rc.title,
        "billId": rc.bill_id,
        "result": rc.result,
        "sourceUrl": rc.source_url,
        "presidentSide": "Yea" if statement.position == "support" else "Nay",
        "statement": {
            "title": statement.title,
            "url": statement.url,
            "issued": statement.issued,
            "position": statement.position,
            "sentence": statement.position_sentence,
        },
    }


def _method(chamber: str) -> dict:
    reference = PRESIDENTIAL_SUPPORT_REFERENCE.load().get(chamber) or {}
    expected = {
        party: {"a": fit["a"], "b": fit["b"], "bOpposed": fit.get("b_opposed", 0.0),
                "members": fit["members"], "votes": fit["votes"]}
        for party, fit in (reference.get("expected") or {}).items()
    }
    return {**METHOD, "fits": expected, "computedAt": reference.get("computed_at")}


def chamber_view(db: Session, chamber: str) -> dict | None:
    """Every serving member of the chamber with a counted vote, the counted
    roll calls, and the method. None before the first run."""
    model = Senator if chamber == "senate" else Representative
    rows = db.query(PresidentialSupport, model).join(model, model.id == PresidentialSupport.member_id).filter(
        PresidentialSupport.chamber == chamber, model.is_current.is_(True),
    ).order_by(model.name).all()
    if not rows:
        return None
    congress = rows[0][0].congress
    return {
        "chamber": chamber,
        "congress": congress,
        "members": [
            {
                "id": m.id, "name": m.name, "party": row.party, "state": m.state,
                "district": getattr(m, "district", None) if chamber == "house" else None,
                **summary(row),
            }
            for row, m in rows
        ],
        "rollCalls": [_vote_row(rc, s) for rc, s in counted_votes(db, chamber, congress)],
        "method": _method(chamber),
    }


def member_view(db: Session, member_id: str) -> dict | None:
    """One member's numbers and every counted roll call with their vote.
    None for a member with no row (none of their votes counted yet)."""
    row = db.query(PresidentialSupport).filter_by(member_id=member_id).first()
    if row is None:
        return None
    model = Senator if row.chamber == "senate" else Representative
    member = db.get(model, member_id)
    counted = counted_votes(db, row.chamber, row.congress)
    cast: dict[int, str] = {}
    if member is not None and counted:
        ids = [rc.id for rc, _ in counted]
        query = db.query(RollCallPosition).filter(RollCallPosition.roll_call_id.in_(ids))
        if row.chamber == "house":
            cast = {p.roll_call_id: p.position for p in query.filter(RollCallPosition.member_id == member.bioguide_id)}
        else:
            links = MemberLinks(db)
            cast = {
                p.roll_call_id: p.position
                for p in query.filter(RollCallPosition.state == member.state)
                if links.page(last_name=p.last_name, state=p.state, lis_id=p.member_id) == f"/politicians/{member_id}"
            }
    votes = []
    for rc, statement in counted:
        position = cast.get(rc.id)
        votes.append({
            **_vote_row(rc, statement),
            "memberVote": position,
            "withPresident": with_president(position, statement.position),
        })
    return {
        "id": member_id,
        "name": member.name if member else None,
        "chamber": row.chamber,
        "congress": row.congress,
        "party": row.party,
        **summary(row),
        "rollCalls": votes,
        "method": _method(row.chamber),
    }
