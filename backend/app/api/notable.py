"""On the record: the homepage's notable entries from Congress's latest
week of record votes.

Two kinds of entry, each a fixed template around the chamber's own roll
call, with the roll call to check it against:

- the closest simple-majority vote of the week, and
- a lone break: the one member of a party who voted the other way from
  every other member of that party, at most one per party (the newest), so
  neither party fills the list by rule.

The week is the latest Monday-to-Sunday week either chamber recorded a
vote in. During a long recess that week grows old, and past
``STALE_AFTER_DAYS`` the list is empty rather than reaching back for
something to show: an empty week shows nothing, not filler.
Nothing here is generated; every sentence is a template around stored
figures.
"""

from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.response_helpers import cached_json
from app.database import get_db
from app.models import Representative, RollCall, RollCallPosition, Senator
from app.pipeline.congress_activity import eastern_today
from app.pipeline.fetch.floor_logs import NAY_POSITIONS, YEA_POSITIONS
from app.pipeline.transform.normalize_votes import MAJORITY_LEADER_TITLES, is_housekeeping
from app.services.bill_record import MemberLinks
from app.services.congress_service import amendment_fields, bill_label, week_bounds
from app.services.roll_call_shape import shape

router = APIRouter()

_TTL_S = 300

# Past this many days since the latest record vote, the week is no longer
# news: a three-week recess still shows the last week in session (labelled
# with its dates), a recess longer than four weeks shows nothing.
STALE_AFTER_DAYS = 28

# A party needs this many members voting Yea or Nay before one dissenter
# is "the only one". With three voting, one of them is a third of the
# party, not a lone break.
LONE_BREAK_MIN_VOTING = 10

# The two parties a lone break is read against. Independents are left out:
# each caucuses with a party, and "the only independent" is not a break.
_PARTY_NOUN = {"D": "Democrat", "R": "Republican"}
_PARTY_PLURAL = {"D": "Democrats", "R": "Republicans"}

# Only simple-majority questions are compared for closeness: a 3/5 or 2/3
# question's distance from passing depends on the threshold, not on the
# gap between yeas and nays.
_SIMPLE_MAJORITY = "1/2"

_CHAMBER_NAME = {"senate": "Senate", "house": "House"}


def _side(position: str) -> str | None:
    p = (position or "").strip().lower()
    if p in YEA_POSITIONS:
        return "yea"
    if p in NAY_POSITIONS:
        return "nay"
    return None


def _vote_ref(rc: RollCall) -> dict:
    return {
        "chamber": rc.chamber, "congress": rc.congress, "session": rc.session, "number": rc.number,
        "date": rc.date, "question": rc.question, "title": rc.title, "result": rc.result,
        "yeas": rc.yeas, "nays": rc.nays, "billId": rc.bill_id, "billLabel": bill_label(rc.bill_id),
        "tieBreaker": shape(rc)["tieBreaker"],
        **amendment_fields(rc),
    }


def _closest(votes: list[RollCall]) -> dict | None:
    """The simple-majority vote with the smallest gap between yeas and
    nays; the newest on a tie."""
    pool = [v for v in votes if v.majority_requirement == _SIMPLE_MAJORITY and (v.yeas or v.nays)]
    if not pool:
        return None
    rc = min(pool, key=lambda v: (abs(v.yeas - v.nays), _desc_key(v)))
    margin = abs(rc.yeas - rc.nays)
    tie = shape(rc)["tieBreaker"]
    if margin == 0 and tie:
        sentence = (f"Tied {rc.yeas}–{rc.nays} in the {_CHAMBER_NAME[rc.chamber]}, broken by the "
                    f"{tie['byWhom']} voting {tie['vote']}: {rc.result}.")
    elif margin == 0:
        sentence = f"Tied {rc.yeas}–{rc.nays} in the {_CHAMBER_NAME[rc.chamber]}: {rc.result}."
    else:
        sentence = (f"Decided by {margin} vote{'' if margin == 1 else 's'} in the "
                    f"{_CHAMBER_NAME[rc.chamber]}, {rc.yeas}–{rc.nays}: {rc.result}.")
    return {"kind": "closest", "sentence": sentence, "vote": _vote_ref(rc)}


def _desc_key(v: RollCall) -> tuple:
    # Newest first under min(): negate the ordering fields.
    return (-date.fromisoformat(v.date).toordinal(), -v.session, -v.number)


def _majority_leaders(db: Session) -> set[str]:
    """Page ids of the chambers' majority leaders. Their vote with the
    prevailing side of a question their party lost is the reconsider switch
    (Senate Rule XIII, House Rule XIX), not a break — the same exemption
    Constituent Alignment makes. The current title stands for the whole
    week: ponytail: a leadership change inside the shown week would need
    leadership_tenures.json, as normalize_votes.majority_leader_spans
    reads it for scoring."""
    ids = set()
    for model in (Senator, Representative):
        ids |= {i for (i,) in db.query(model.id).filter(model.leadership_title.in_(MAJORITY_LEADER_TITLES))}
    return ids


def _lone_breaks(db: Session, votes: list[RollCall]) -> list[dict]:
    if not votes:
        return []
    by_id = {v.id: v for v in votes}
    positions: dict[int, list[RollCallPosition]] = defaultdict(list)
    for p in db.query(RollCallPosition).filter(RollCallPosition.roll_call_id.in_(list(by_id))):
        positions[p.roll_call_id].append(p)
    links = MemberLinks(db)
    leaders = _majority_leaders(db)
    names = {i: n for model in (Senator, Representative) for i, n in db.query(model.id, model.name)}
    senators = defaultdict(list)
    for sid, state in db.query(Senator.id, Senator.state).filter(Senator.is_current.is_(True)):
        senators[state].append(sid)

    newest: dict[str, dict] = {}
    for rc in sorted(votes, key=_desc_key):
        if is_housekeeping(rc.question, rc.chamber):
            continue
        for party in _PARTY_NOUN:
            if party in newest:
                continue
            sides = defaultdict(list)
            for p in positions[rc.id]:
                side = _side(p.position)
                if p.party == party and side:
                    sides[side].append(p)
            voting = len(sides["yea"]) + len(sides["nay"])
            if voting < LONE_BREAK_MIN_VOTING:
                continue
            lone = [s for s in ("yea", "nay") if len(sides[s]) == 1]
            if not lone:
                continue
            breaker = sides[lone[0]][0]
            href = (links.page(bioguide=breaker.member_id) if rc.chamber == "house"
                    else links.page(last_name=breaker.last_name, state=breaker.state, lis_id=breaker.member_id))
            member_id = href.rsplit("/", 1)[-1] if href else None
            prevailing = None if rc.rejected is None else ("nay" if rc.rejected else "yea")
            if member_id in leaders and lone[0] == prevailing:
                continue
            name = names.get(member_id) or f"{breaker.first_name} {breaker.last_name}".strip()
            rest = voting - 1
            others = "yea" if lone[0] == "nay" else "nay"
            others_word = sides[others][0].position
            sentence = (f"The only {_PARTY_NOUN[party]} to vote {breaker.position}: {name} "
                        f"({party}-{breaker.state}), against {rest} {_PARTY_PLURAL[party]} voting "
                        f"{others_word}.")
            colleague = [s for s in senators.get(breaker.state, []) if s != member_id]
            compare_with = (colleague[0] if rc.chamber == "senate" and member_id and len(colleague) == 1
                            else None)
            newest[party] = {
                "kind": "loneBreak",
                "sentence": sentence,
                "vote": _vote_ref(rc),
                "memberId": member_id,
                "memberName": name,
                "party": party,
                "state": breaker.state,
                # The member's state's other senator, for a side-by-side.
                "compareWithId": compare_with,
                "compareWithName": names.get(compare_with) if compare_with else None,
            }
        if len(newest) == len(_PARTY_NOUN):
            break
    # Newest vote first; two breaks on one vote in party-letter order.
    by_party = sorted(newest.values(), key=lambda e: e["party"])
    return sorted(by_party, key=lambda e: (e["vote"]["date"], e["vote"]["number"]), reverse=True)


def notable(db: Session, today: date | None = None) -> dict:
    today = today or eastern_today()
    latest = (db.query(RollCall.date).filter(RollCall.date <= today.isoformat())
              .order_by(RollCall.date.desc()).first())
    empty = {"weekStart": None, "weekEnd": None, "entries": []}
    if latest is None:
        return empty
    last_day = date.fromisoformat(latest[0])
    if (today - last_day) > timedelta(days=STALE_AFTER_DAYS):
        return empty
    monday, sunday = week_bounds(last_day)
    votes = db.query(RollCall).filter(
        RollCall.date >= monday.isoformat(), RollCall.date <= sunday.isoformat(),
    ).all()
    closest = _closest([v for v in votes if not is_housekeeping(v.question, v.chamber)])
    entries = ([closest] if closest else []) + _lone_breaks(db, votes)
    return {"weekStart": monday.isoformat(), "weekEnd": sunday.isoformat(), "entries": entries}


@router.get("/notable")
def notable_route(db: Session = Depends(get_db)) -> JSONResponse:
    return cached_json(notable(db), max_age=_TTL_S)
