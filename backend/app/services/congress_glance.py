"""/congress at a glance: the days each chamber sat this Congress, how far
its bills have got, and, for a state the reader picks, that state's members'
record this Congress. All read from stored rows; nothing is fetched or
estimated here.
"""

from collections import defaultdict
from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.config_definitions import BillStage
from app.models import CongressDay, Representative, RepSponsoredBill, RollCall, RollCallPosition, Senator, SponsoredBill
from app.photos import bioguide_photo
from app.pipeline.congress_activity import eastern_today
from app.pipeline.fetch.bill_display_titles import cached_display_titles, titles_key
from app.pipeline.fetch.congressional_record import congress_start
from app.services import congress_service
from app.services.bill_service import stage_milestones
from app.services.member_positions import member_positions, roll_call_member_ids

_CHAMBERS = ("senate", "house")
# A member's latest few votes and bills: enough to show what they have been
# doing, short enough that three members fit a phone screen or two.
RECENT = 3
# What a vote row needs to name and link its roll call.
_ROLL_CALL_KEYS = ("chamber", "congress", "session", "number", "question", "result")


def sitting_calendar(db: Session, congress: int) -> dict:
    """Every day of the Congress so far, by chamber: whether it sat (the
    Record's day in session, or a roll call that day) and how many record
    votes it took. `days` holds only days a chamber sat ({date: {chamber:
    votes}}; a chamber absent from a day did not sit, or is not on record);
    `recordFrom` is the first day Civitas holds the Record for, so a page
    can tell "did not sit" from "not held here". `months` totals each
    month, the table a page shows beside the picture."""
    first = congress_start(congress)
    last = min(eastern_today(), congress_start(congress + 1) - timedelta(days=1))
    lo, hi = first.isoformat(), last.isoformat()
    sat: dict[str, dict[str, int]] = defaultdict(dict)
    for chamber, day in db.query(CongressDay.chamber, CongressDay.date).filter(
        CongressDay.in_session.is_(True), CongressDay.date >= lo, CongressDay.date <= hi,
    ):
        sat[day].setdefault(chamber, 0)
    for chamber, day, n in db.query(RollCall.chamber, RollCall.date, func.count(RollCall.id)).filter(
        RollCall.congress == congress, RollCall.date >= lo, RollCall.date <= hi,
    ).group_by(RollCall.chamber, RollCall.date):
        sat[day][chamber] = n
    span = congress_service.record_span(db)
    months: dict[str, dict] = {}
    cursor = first.replace(day=1)
    while cursor <= last:
        months[f"{cursor:%Y-%m}"] = {c: {"days": 0, "votes": 0} for c in _CHAMBERS}
        cursor = (cursor + timedelta(days=32)).replace(day=1)
    for day, chambers in sat.items():
        for chamber, votes in chambers.items():
            m = months[day[:7]][chamber]
            m["days"] += 1
            m["votes"] += votes
    totals = {c: {"days": sum(m[c]["days"] for m in months.values()),
                  "votes": sum(m[c]["votes"] for m in months.values())} for c in _CHAMBERS}
    return {
        "congress": congress, "start": lo, "end": (congress_start(congress + 1) - timedelta(days=1)).isoformat(),
        "today": eastern_today().isoformat(), "recordFrom": span[0].isoformat() if span else None,
        "days": dict(sorted(sat.items())),
        "months": [{"month": k, **v} for k, v in months.items()],
        "totals": totals,
        "maxVotes": max((v for c in sat.values() for v in c.values()), default=0),
    }


def glance(db: Session) -> dict:
    congress = settings.CURRENT_CONGRESS
    return {
        "calendar": sitting_calendar(db, congress),
        # The bill page's ladder, for no bill in particular: how many of the
        # bills sitting members sponsored this Congress reached each step.
        "pipeline": _pipeline(db, congress),
    }


def _pipeline(db: Session, congress: int) -> dict | None:
    m = stage_milestones(db, BillStage.INTRODUCED, congress)
    if m is None:
        return None
    m.pop("reached", None)
    return m


def _bills(db: Session, chamber: str, member_id: str, congress: int) -> tuple[int, list[dict]]:
    model, owner = (SponsoredBill, SponsoredBill.senator_id) if chamber == "senate" else (
        RepSponsoredBill, RepSponsoredBill.representative_id)
    query = db.query(model).filter(owner == member_id, model.congress == congress)
    rows = query.order_by(model.introduced_date.desc(), model.bill_id).limit(RECENT).all()
    named = cached_display_titles(db, [(b.congress, b.bill_id, b.title) for b in rows])
    out = []
    for b in rows:
        short = named.get(titles_key(b.congress, b.bill_id))
        out.append({"billId": b.bill_id, "billLabel": congress_service.bill_label(b.bill_id), "congress": b.congress, "title": short.title if short else b.title,
                    "introduced": b.introduced_date, "stage": b.stage or BillStage.INTRODUCED,
                    "latestAction": b.latest_action, "latestActionDate": b.latest_action_date})
    return query.count(), out


def _missed(db: Session, chamber: str, member_id: str, congress: int) -> int:
    vote_id = roll_call_member_ids(db, chamber, congress, {"m": member_id}).get(member_id)
    if not vote_id:
        return 0
    return db.query(func.count(RollCallPosition.id)).join(RollCall, RollCall.id == RollCallPosition.roll_call_id).filter(
        RollCall.chamber == chamber, RollCall.congress == congress,
        RollCallPosition.member_id == vote_id, RollCallPosition.position == "Not Voting",
    ).scalar() or 0


def state_members(db: Session, state: str) -> list[dict]:
    """A state's sitting senators and representatives, each with their
    record this Congress: how many roll calls they were recorded on and
    missed, their latest votes (each with whether it sided with their
    party, member_positions), and their latest sponsored bills. The state
    is the reader's own pick, sent in the URL and kept nowhere (AGENTS.md
    section 8)."""
    congress = settings.CURRENT_CONGRESS
    members = [(m, "senate") for m in db.query(Senator).filter(
        Senator.state == state, Senator.is_current.is_(True)).order_by(Senator.name)]
    members += [(m, "house") for m in db.query(Representative).filter(
        Representative.state == state, Representative.is_current.is_(True)).order_by(Representative.district)]
    out = []
    for m, chamber in members:
        votes = member_positions(db, chamber, m.id, 1, RECENT) or {}
        bill_count, bills = _bills(db, chamber, m.id, congress)
        recorded, missed = votes.get("total", 0), _missed(db, chamber, m.id, congress)
        out.append({
            "id": m.id, "name": m.name, "party": m.party, "chamber": chamber, "state": m.state,
            "district": getattr(m, "district", None) if chamber == "house" else None,
            "photo": bioguide_photo(m.bioguide_id),
            "rollCalls": recorded, "chamberRollCalls": votes.get("chamberRollCalls", 0),
            "voted": recorded - missed, "missed": missed,
            "votes": [{"date": v["date"], "vote": v["vote"], "billId": v["billId"], "billName": v["billName"],
                       "votedWithParty": v["votedWithParty"],
                       "rollCall": {k: v["rollCall"].get(k) for k in _ROLL_CALL_KEYS} if v["rollCall"] else None}
                      for v in votes.get("votes", [])],
            "billsSponsored": bill_count, "bills": bills,
        })
    return out

