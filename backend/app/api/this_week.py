"""This week: the strip on the homepage and on the pages readers land on.

Three things a reader can act on now, all from what the site already
stores: how far off Election Day is (with every state's ballot page to
pick from — navigation, never a question about where the reader lives),
what each chamber of Congress is doing (in session, next meeting, record
votes this week), and the next deadlines to comment on federal rules.
Every sentence is a template around stored figures; nothing is generated.
"""

import json
from collections import Counter
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.elections import ballot_state_codes
from app.api.response_helpers import cached_json
from app.database import get_db
from app.election_phase import active_election, election_today
from app.issue_ids import to_public_id
from app.models import ActionIssue, CongressDay, ExploreDocument, RollCall
from app.pipeline.congress_activity import eastern_today
from app.retractions import retraction_for_issue
from app.services.congress_service import CHAMBERS, bill_label, next_meeting, week_bounds
from app.time_utils import comment_period_today

router = APIRouter()

# The chambers' floor logs move every half hour on a session day; the
# Congress reports use the same lifetime.
_TTL_S = 300

_CHAMBER_NAME = {"senate": "Senate", "house": "House"}

# ── Congress ──────────────────────────────────────────────────────

def _long_date(d: date) -> str:
    return f"{d:%A}, {d:%B} {d.day}"


def _chamber(db: Session, chamber: str, today: date, monday: date, sunday: date) -> dict:
    iso = today.isoformat()
    met = {
        d for (d,) in db.query(CongressDay.date).filter(
            CongressDay.chamber == chamber, CongressDay.in_session.is_(True),
            CongressDay.date >= monday.isoformat(), CongressDay.date <= sunday.isoformat(),
        )
    }
    votes = db.query(RollCall).filter(
        RollCall.chamber == chamber, RollCall.date >= monday.isoformat(), RollCall.date <= sunday.isoformat(),
    ).all()
    met |= {v.date for v in votes}
    nxt, _ = next_meeting(db, chamber, today)
    return {
        "chamber": chamber,
        "inSessionToday": iso in met,
        "daysInSessionThisWeek": len(met),
        "recordVotesThisWeek": len(votes),
        "nextMeeting": nxt.isoformat() if nxt else None,
        "_votes": votes,
    }


def _chamber_sentence(c: dict, today: date) -> str:
    name = _CHAMBER_NAME[c["chamber"]]
    nxt = date.fromisoformat(c["nextMeeting"]) if c["nextMeeting"] else None
    if c["inSessionToday"]:
        return f"The {name} is in session today."
    if nxt == today:
        return f"The {name} meets today."
    if nxt is None:
        return f"The {name}'s next meeting is not on record yet."
    if not c["daysInSessionThisWeek"]:
        return f"The {name} is out until {_long_date(nxt)}."
    return f"The {name} next meets {_long_date(nxt)}."


def congress_week(db: Session, today: date | None = None) -> dict:
    today = today or eastern_today()
    monday, sunday = week_bounds(today)
    chambers = {c: _chamber(db, c, today, monday, sunday) for c in CHAMBERS}
    senate, house = chambers["senate"], chambers["house"]
    # Both chambers out, next meeting the same day: one sentence for Congress.
    together = (not senate["inSessionToday"] and not house["inSessionToday"]
                and senate["nextMeeting"] and senate["nextMeeting"] == house["nextMeeting"]
                and senate["nextMeeting"] > today.isoformat())
    if together:
        when = _long_date(date.fromisoformat(senate["nextMeeting"]))
        idle = not senate["daysInSessionThisWeek"] and not house["daysInSessionThisWeek"]
        sentence = f"Congress is out until {when}." if idle else f"Congress next meets {when}."
    else:
        sentence = " ".join(_chamber_sentence(chambers[c], today) for c in CHAMBERS)
    votes = sorted((v for c in CHAMBERS for v in chambers[c].pop("_votes")),
                   key=lambda v: (v.date, v.number), reverse=True)
    return {
        "weekStart": monday.isoformat(),
        "sentence": sentence,
        "chambers": chambers,
        # The latest few, newest first; the week's page lists every one.
        "recentVotes": [
            {
                "chamber": v.chamber, "congress": v.congress, "session": v.session, "number": v.number,
                "date": v.date, "question": v.question, "title": v.title, "result": v.result,
                "billId": v.bill_id, "billLabel": bill_label(v.bill_id),
            }
            for v in votes[:3]
        ],
    }


# ── Comment deadlines ─────────────────────────────────────────────

# Federal Register document types that take comments on a rule (fetch/
# fr_rulemaking.FR_TYPE_LABELS). Notices are left out by type, and with
# them every information-collection and meeting notice.
_RULE_TYPES = ("Proposed Rule", "Final Rule")

# An Action Center issue's related documents count as "in the news" for
# two weeks after the issue's day.
_NEWS_WINDOW_DAYS = 14

# A Regulation Identifier Number shared by this many open rules marks a
# templated series, not one rulemaking: the FAA files every airworthiness
# directive under one RIN, the Coast Guard every safety zone under another.
# Measured over the 185 rules open for comment on 2026-10-11: 79 RINs on
# one rule, 2 on two (a proposed rule and its correction), then 4, 4, 11
# and 54 (airspace, safety zones, drawbridges, airworthiness directives).
# A series also inflates the citation count (every document under the RIN
# "cites" the rest), so it can't stand in for a rule that drew citations.
_SERIES_MIN_RULES = 3

DEADLINES_SHOWN = 3


def _rins(doc: ExploreDocument) -> set[str]:
    try:
        ids = json.loads(doc.identifiers or "[]")
    except ValueError:
        return set()
    return {i for i in ids if isinstance(i, str) and i.startswith("rin:")}


def _issues_by_document(db: Session, today: date) -> dict[int, str]:
    """Explore document id -> public id of the newest Action Center issue
    (in the last two weeks, not withdrawn) that cites it."""
    since = (today - timedelta(days=_NEWS_WINDOW_DAYS)).isoformat()
    out: dict[int, str] = {}
    issues = (db.query(ActionIssue.id, ActionIssue.related_explore_ids)
              .filter(ActionIssue.date >= since).order_by(ActionIssue.date))
    for issue_id, related in issues:
        public_id = to_public_id(issue_id)
        if retraction_for_issue(public_id):
            continue
        try:
            ids = json.loads(related or "[]")
        except ValueError:
            continue
        for doc_id in ids:
            if isinstance(doc_id, int):
                out[doc_id] = public_id
    return out


def comment_deadlines(db: Session) -> list[dict]:
    """The next few comment deadlines worth a reader's time: rules an Action
    Center issue cites first, then rules other documents cite, then any
    other rule, each tier soonest deadline first."""
    today = comment_period_today()
    docs = db.query(ExploreDocument).filter(
        ExploreDocument.doc_type.in_(_RULE_TYPES),
        ExploreDocument.comment_url.isnot(None), ExploreDocument.comment_url != "",
        ExploreDocument.comments_close_on >= today,
    ).all()
    in_news = _issues_by_document(db, date.fromisoformat(today))
    rins = {d.id: _rins(d) for d in docs}
    per_rin = Counter(r for ids in rins.values() for r in ids)

    def tier(d: ExploreDocument) -> int | None:
        if d.id in in_news:
            return 0
        if any(per_rin[r] >= _SERIES_MIN_RULES for r in rins[d.id]):
            return None
        return 1 if (d.cited_by_count or 0) > 0 else 2

    ranked = sorted(
        ((t, d) for d in docs if (t := tier(d)) is not None),
        key=lambda td: (td[0], td[1].comments_close_on, td[1].id),
    )
    return [
        {
            "id": d.id, "title": d.title, "docType": d.doc_type, "agencyName": d.agency_name,
            "commentsCloseOn": d.comments_close_on, "commentUrl": d.comment_url,
            "daysLeft": (date.fromisoformat(d.comments_close_on) - date.fromisoformat(today)).days,
            "issueId": in_news.get(d.id),
            "citedBy": (d.cited_by_count or 0) if t == 1 else 0,
        }
        for t, d in ranked[:DEADLINES_SHOWN]
    ]


# ── Election ──────────────────────────────────────────────────────

def election(db: Session) -> dict:
    e = active_election(db)
    return {
        "date": e.election_day.isoformat(),
        "phase": e.phase,
        "daysUntil": (e.election_day - election_today()).days,
        # Every jurisdiction with a ballot page (DC included: it votes on
        # measures), for the state picker.
        "states": sorted(ballot_state_codes()),
    }


@router.get("/this-week")
def this_week(db: Session = Depends(get_db)) -> JSONResponse:
    return cached_json({
        "election": election(db),
        "congress": congress_week(db),
        "commentDeadlines": comment_deadlines(db),
    }, max_age=_TTL_S)
