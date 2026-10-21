"""The homepage's front page, "Entered into the record": a nameplate, one
lead story, a second tier of two and three columns (Congress, Agencies,
In the news), every entry picked by the fixed rule below from rows the
site already stores. Nothing here is generated: each headline is a
template around stored figures, or the record's own words (a law's
title, a rule's title, an outlet's headline).

Every window is one the site already defines, and no threshold is typed:

- "The latest week of business" is the Monday-to-Sunday week holding the
  latest day either chamber took a record vote or passed, failed or
  confirmed anything (congress_service.last_business_day, the day /congress
  names when Congress is out).
- "Congress is out" is /congress's own test (congress_service.recess): the
  latest day either chamber met had no business.

Candidates:

- the closest simple-majority record vote of the latest week of business
  (a tie the Vice President broke is the closest there is),
- a lone break in that week: the one member of a party who voted the other
  way from every other member of that party, at most one per party (the
  newest),
- each bill that became law, and each nomination (not a list, not
  military) the Senate confirmed, from the start of that week to today,
- open comment periods: the proposed or final rule whose comments close
  soonest first, then the most salient others (comment_periods.salience,
  paperwork notices left out),
- the Action Center's recent issues, one per story (a national monitor's
  later days fold under its newest, as on /issues/recent; those in the same
  Monday-to-Sunday week as the day shown are named beside it).

The lead is the first of these that holds, in this order: the closest vote,
if its margin is smaller than the median margin of this Congress's
simple-majority record votes (measured from the stored roll calls on each
request, so the bar moves with the record); else the newest law; else the
newest confirmation; else the newest lone break; else the day's top issue;
else the comment deadline closing soonest. While Congress is out, a vote
(the closest, or a lone break) doesn't lead: its week is the last one of
business, not this one. The second tier is the first remaining entry with a
figure from Congress, then Agencies, then In the news, two at most, so the
two never come from one column. Each column lists what is left of its own,
each kind's first entry before any kind's second. No entry appears twice.
A Congress entry is dated, so the last week of business reads as past.
Every figure (counts, shares, days left) is computed here; the page only
draws it.
"""

import json
import statistics
from collections import defaultdict
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.action import monitor_groups, recent_issues
from app.api.response_helpers import JSONResponse, cached_json
from app.comment_periods import comment_window, is_paperwork, salience
from app.database import get_db
from app.election_phase import CAMPAIGN, ELECTION_DAY, active_election
from app.issue_ids import to_public_id
from app.models import (
    ExploreDocument, FeedArticle, NationalMonitor, Nomination, Representative, RollCall, RollCallPosition, Senator,
)
from app.pipeline.congress_activity import eastern_today
from app.pipeline.fetch.floor_logs import NAY_POSITIONS, YEA_POSITIONS
from app.pipeline.fetch.fr_rulemaking import RULE_DOC_TYPES
from app.pipeline.fetch.news_feeds import NEWS_FEEDS
from app.pipeline.transform.normalize_votes import MAJORITY_LEADER_TITLES, is_housekeeping
from app.services import congress_service as cs
from app.services.bill_record import MemberLinks
from app.services.congress_service import amendment_fields, bill_label, week_bounds
from app.services.explore_keys import public_key
from app.services.nomination_links import nominee_names
from app.services.nomination_service import days_to_confirm, roll_calls_citing
from app.services.roll_call_shape import shape

router = APIRouter()

_TTL_S = 300

# Entries per column: a layout decision (three items keep the columns about
# as tall as the lead and tier above them at 1280px), not a rule about the
# data.
COLUMN_ITEMS = 3
# Stories read for the news: enough for the lead, the tier and a full column.
NEWS_STORIES = COLUMN_ITEMS + 2

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
_COLUMNS = ("congress", "agencies", "news")


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


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


def _split(rc: RollCall, caption: str) -> dict:
    total = (rc.yeas or 0) + (rc.nays or 0)
    return {"type": "split", "yeas": rc.yeas, "nays": rc.nays,
            "yeaShare": round(rc.yeas / total, 4) if total else 0, "caption": caption}


def _simple_majority(votes: list[RollCall]) -> list[RollCall]:
    return [v for v in votes if v.majority_requirement == _SIMPLE_MAJORITY and (v.yeas or v.nays)
            and not is_housekeeping(v.question, v.chamber)]


def median_margin(db: Session, congress: int) -> float | None:
    """The median gap between yeas and nays over the Congress's
    simple-majority record votes (housekeeping questions left out, as
    everywhere a vote is read for its substance); None before any."""
    rows = db.query(RollCall.question, RollCall.chamber, RollCall.yeas, RollCall.nays).filter(
        RollCall.congress == congress, RollCall.majority_requirement == _SIMPLE_MAJORITY,
    ).all()
    margins = [abs((r.yeas or 0) - (r.nays or 0)) for r in rows
               if (r.yeas or r.nays) and not is_housekeeping(r.question, r.chamber)]
    return statistics.median(margins) if margins else None


def _closest(votes: list[RollCall], median: float | None = None) -> dict | None:
    """The simple-majority vote with the smallest gap between yeas and
    nays; the newest on a tie. `close` when its gap is under `median`, the
    Congress's median margin."""
    pool = _simple_majority(votes)
    if not pool:
        return None
    rc = min(pool, key=lambda v: (abs(v.yeas - v.nays), _desc_key(v)))
    margin = abs(rc.yeas - rc.nays)
    tie = shape(rc)["tieBreaker"]
    chamber = _CHAMBER_NAME[rc.chamber]
    if margin == 0 and tie:
        headline = (f"Tied {rc.yeas}–{rc.nays} in the {chamber}, broken by the "
                    f"{tie['byWhom']} voting {tie['vote']}: {rc.result}.")
        caption = f"Tie broken by the {tie['byWhom']}"
    elif margin == 0:
        headline = f"Tied {rc.yeas}–{rc.nays} in the {chamber}: {rc.result}."
        caption = "Tied"
    else:
        headline = f"Decided by {_plural(margin, 'vote')} in the {chamber}, {rc.yeas}–{rc.nays}: {rc.result}."
        caption = f"Decided by {_plural(margin, 'vote')}"
    return {"kind": "closest", "column": "congress", "date": rc.date, "headline": headline,
            "vote": _vote_ref(rc), "figure": _split(rc, caption),
            "close": median is not None and margin < median}


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
            headline = (f"The only {_PARTY_NOUN[party]} to vote {breaker.position}: {name} "
                        f"({party}-{breaker.state}), against {rest} {_PARTY_PLURAL[party]} voting "
                        f"{others_word}.")
            colleague = [s for s in senators.get(breaker.state, []) if s != member_id]
            compare_with = (colleague[0] if rc.chamber == "senate" and member_id and len(colleague) == 1
                            else None)
            newest[party] = {
                "kind": "loneBreak",
                "column": "congress",
                "date": rc.date,
                "headline": headline,
                "vote": _vote_ref(rc),
                "memberId": member_id,
                "memberName": name,
                "party": party,
                "state": breaker.state,
                # The member's state's other senator, for a side-by-side.
                "compareWithId": compare_with,
                "compareWithName": names.get(compare_with) if compare_with else None,
                # One dot per member of the party voting Yea or Nay, the
                # breaker's marked.
                "figure": {"type": "dots", "party": party, "voting": voting,
                           "caption": f"1 of {voting} {_PARTY_PLURAL[party]} voted {breaker.position}"},
            }
        if len(newest) == len(_PARTY_NOUN):
            break
    # Newest vote first; two breaks on one vote in party-letter order.
    by_party = sorted(newest.values(), key=lambda e: e["party"])
    return sorted(by_party, key=lambda e: (e["vote"]["date"], e["vote"]["number"]), reverse=True)


def _business_week(db: Session, today: date) -> tuple[date, date] | None:
    """Monday and Sunday of the latest week of business, None before any."""
    last = cs.last_business_day(db, today)
    return week_bounds(last) if last else None


def _week_votes(db: Session, week: tuple[date, date] | None) -> list[RollCall]:
    if week is None:
        return []
    return db.query(RollCall).filter(
        RollCall.date >= week[0].isoformat(), RollCall.date <= week[1].isoformat(),
    ).all()


def _laws(db: Session, since: date, today: date) -> list[dict]:
    laws = cs.became_law(db, since, today)
    figure = {"type": "stat", "value": len(laws),
              "caption": f"{'law' if len(laws) == 1 else 'laws'} enacted since {since:%B} {since.day}"}
    return [{
        "kind": "law", "column": "congress", "date": law["date"],
        "headline": f"{law['billLabel'] or law['billId']} became law",
        # Congress.gov's title and the action as recorded.
        "dek": law["name"], "action": law["text"],
        "billId": law["billId"], "billLabel": law["billLabel"], "congress": law["congress"],
        "figure": figure,
    } for law in sorted(laws, key=lambda b: (b["date"], b["billId"]), reverse=True)]


def _confirmations(db: Session, since: date, today: date) -> list[dict]:
    rows = (db.query(Nomination)
            .filter(Nomination.status == "confirmed", Nomination.is_list.is_(False),
                    Nomination.is_military.is_(False),
                    Nomination.status_date >= since.isoformat(),
                    Nomination.status_date <= today.isoformat())
            .order_by(Nomination.status_date.desc(), Nomination.number.desc())
            .limit(COLUMN_ITEMS + 2).all())
    out = []
    for row in rows:
        names = nominee_names(row)
        if not names:
            continue
        final = next((rc for rc in reversed(roll_calls_citing(db, row))
                      if rc.question.startswith("On the Nomination")), None)
        days = days_to_confirm(row)
        if final is not None:
            figure = _split(final, f"Confirmed {final.yeas}–{final.nays}")
        elif days is not None:
            figure = {"type": "stat", "value": days, "caption": "days from nomination to confirmation"}
        else:
            figure = None
        out.append({
            "kind": "confirmed", "column": "congress", "date": row.status_date,
            "headline": f"Confirmed by the Senate: {names[0]}",
            # The position as Congress.gov names it.
            "dek": ", ".join(p for p in (row.position_title, row.organization) if p) or None,
            "congress": row.congress, "citation": row.citation,
            "vote": _vote_ref(final) if final is not None else None,
            "figure": figure,
        })
    return out


def _comments(db: Session, today: date) -> list[dict]:
    """The rule closing soonest, then the most salient other open periods."""
    iso = today.isoformat()
    docs = (db.query(
        ExploreDocument.id, ExploreDocument.external_id, ExploreDocument.title, ExploreDocument.agency_name,
        ExploreDocument.agency_slug, ExploreDocument.comments_close_on, ExploreDocument.comment_url,
        ExploreDocument.doc_type, ExploreDocument.date, ExploreDocument.fr_page_views,
        ExploreDocument.fr_toc_subject,
    ).filter(
        ExploreDocument.comment_url.isnot(None), ExploreDocument.comment_url != "",
        ExploreDocument.comments_close_on.isnot(None), ExploreDocument.comments_close_on >= iso,
    ).all())
    docs = [d for d in docs if d.title and not is_paperwork(d.doc_type, d.fr_toc_subject)]
    rules = sorted((d for d in docs if d.doc_type in RULE_DOC_TYPES),
                   key=lambda d: (d.comments_close_on, -(d.fr_page_views or 0)))
    first = rules[:1]
    rest = sorted((d for d in docs if d not in first), key=salience)[:COLUMN_ITEMS]
    out = []
    for d in first + rest:
        window = comment_window(d.date, d.comments_close_on, iso)
        out.append({
            "kind": "comment", "column": "agencies", "date": d.comments_close_on,
            # The Federal Register's own title.
            "headline": d.title, "agencyName": d.agency_name, "agencySlug": d.agency_slug or None,
            "docType": d.doc_type, "id": d.id, "key": public_key(d.external_id, d.id),
            "commentUrl": d.comment_url,
            "figure": {"type": "countdown", **window} if window else None,
        })
    return out


def _news(db: Session) -> list[dict]:
    groups = monitor_groups(recent_issues(db), db)[:NEWS_STORIES]
    if not groups:
        return []
    titles = {i.title for i, _, _ in groups}
    outlet_of: dict[tuple[str, str], str] = {}
    for title, url, source in (db.query(FeedArticle.title, FeedArticle.url, FeedArticle.source_name)
                               .filter(FeedArticle.title.in_(sorted(titles)))):
        outlet_of[(title, url)] = source
    slugs = set().union(*(s for _, s, _ in groups))
    monitors = dict(db.query(NationalMonitor.slug, NationalMonitor.title)
                    .filter(NationalMonitor.slug.in_(sorted(slugs)))) if slugs else {}
    out = []
    for issue, story, folded in groups:
        outlets = json.loads(issue.source_names or "[]")
        urls = json.loads(issue.source_urls or "[]")
        # The outlet whose headline titles the issue (the feed article
        # carrying it); the first outlet when that article isn't stored.
        outlet = next((outlet_of[(issue.title, u)] for u in urls if (issue.title, u) in outlet_of),
                      outlets[0] if outlets else None)
        week = week_bounds(date.fromisoformat(issue.date))
        also = sorted({f.date for f in folded
                       if f.date != issue.date and week[0] <= date.fromisoformat(f.date) <= week[1]},
                      reverse=True)
        monitor = next(({"slug": s, "title": monitors[s]} for s in sorted(story) if s in monitors), None)
        out.append({
            "kind": "issue", "column": "news", "date": issue.date,
            "headline": issue.title, "publicId": to_public_id(issue.id),
            "outlet": outlet, "outlets": outlets, "alsoDates": also, "monitor": monitor,
            "figure": {"type": "stat", "value": len(outlets),
                       "caption": f"{'newsroom' if len(outlets) == 1 else 'newsrooms'} reporting"}
            if outlets else None,
        })
    return out


def _one_of_each_first(entries: list[dict]) -> list[dict]:
    """Each kind's first entry before any kind's second, otherwise in
    order: a fortnight of laws doesn't crowd a lone break off the column."""
    seen: dict[str, int] = defaultdict(int)
    ranked = []
    for n, e in enumerate(entries):
        ranked.append((seen[e["kind"]], n, e))
        seen[e["kind"]] += 1
    return [e for _, _, e in sorted(ranked, key=lambda r: r[:2])]


def arrange(congress: list[dict], agencies: list[dict], news: list[dict], congress_out: bool = False) -> dict:
    """The fixed rule (module docstring) over the candidates, each list in
    its own priority order: lead, tier, columns, no entry twice."""
    closest = next((e for e in congress if e["kind"] == "closest"), None)
    first_of = {k: next((e for e in congress if e["kind"] == k), None) for k in ("law", "confirmed", "loneBreak")}
    lead = next((e for e in (
        closest if closest and closest["close"] and not congress_out else None,
        first_of["law"],
        first_of["confirmed"],
        None if congress_out else first_of["loneBreak"],
        news[0] if news else None,
        agencies[0] if agencies else None,
    ) if e is not None), None)
    pools = {"congress": [e for e in congress if e is not lead],
             "agencies": [e for e in agencies if e is not lead],
             "news": [e for e in news if e is not lead]}
    tier = []
    for col in _COLUMNS:
        pick = next((e for e in pools[col] if e.get("figure")), None)
        if pick is not None and len(tier) < 2:
            tier.append(pick)
            pools[col].remove(pick)
    columns = {col: _one_of_each_first(pools[col])[:COLUMN_ITEMS] for col in _COLUMNS}
    columns["congress"].sort(key=lambda e: e["date"], reverse=True)
    return {"lead": lead, "tier": tier, "columns": columns}


def _congress_line(db: Session, today: date, recess: dict | None, latest: date | None) -> str | None:
    """"In session today", or when each chamber next sits, as the Record
    gives it, after "Congress is out" when the latest day had no business."""
    if latest == today and recess is None:
        return "In session today"
    nxt = [(when, c, program) for c in cs.CHAMBERS for when, program in [cs.next_meeting(db, c, today)] if when]
    parts = ["Congress is out"] if recess else []
    if nxt:
        first = min(w for w, _, _ in nxt)
        who = [c for w, c, _ in nxt if w == first]
        both = len(who) == 2
        subject = "both chambers" if both else f"the {_CHAMBER_NAME[who[0]]}"
        if first == today:
            phrase = f"{subject} {'meet' if both else 'meets'} today"
        else:
            phrase = f"{subject} next {'sit' if both else 'sits'} {cs.long_date(first)}"
        pro_forma = all("pro forma" in (p or "").lower() for w, _, p in nxt if w == first)
        parts.append(phrase + (", pro forma" if pro_forma else ""))
    if not parts:
        return None
    line = "; ".join(parts)
    return line[0].upper() + line[1:]


def _election_line(db: Session, today: date) -> str | None:
    e = active_election(db, today)
    if e.phase == ELECTION_DAY:
        return "Election Day today"
    if e.phase != CAMPAIGN:
        return None
    return f"{_plural((e.election_day - today).days, 'day')} to Election Day"


def _sources_sentence() -> str:
    names = list(dict.fromkeys(f["name"] for f in NEWS_FEEDS))
    outlets = ", ".join(names[:-1]) + f" and {names[-1]}" if len(names) > 1 else "".join(names)
    return (f"From the House and Senate's own records, Congress.gov, the Federal Register, {outlets}, "
            "each in its own words")


def front_page(db: Session, today: date | None = None) -> dict:
    today = today or eastern_today()
    week = _business_week(db, today)
    votes = _week_votes(db, week)
    simple = _simple_majority(votes)
    closest = _closest(simple, median_margin(db, simple[0].congress) if simple else None)
    since = week[0] if week else today
    congress = (([closest] if closest else []) + _laws(db, since, today) + _confirmations(db, since, today)
                + _lone_breaks(db, votes))
    latest = cs.latest_day(db)
    recess = cs.recess(db, cs.day_report(db, latest)) if latest else None
    page = arrange(congress, _comments(db, today), _news(db), congress_out=recess is not None)
    if page["lead"] is None:
        return {"asOf": today.isoformat(), "lead": None, "tier": [], "columns": {c: [] for c in _COLUMNS}}

    this_week = week_bounds(today)[0]
    monday = week[0] if week else None
    return {
        "asOf": today.isoformat(),
        "dateline": [line for line in (cs.long_date(today), _congress_line(db, today, recess, latest),
                                       _election_line(db, today)) if line],
        "edition": {
            "sentence": _sources_sentence(),
            "week": monday.isoformat() if monday else None,
            "weekLabel": ("Everything this week" if monday == this_week
                          else f"The week of {monday:%B} {monday.day}") if monday else None,
        },
        # The latest week of business, which the Congress entries come from.
        "voteWeek": monday.isoformat() if monday else None,
        **page,
    }


@router.get("/front-page")
def front_page_route(db: Session = Depends(get_db)) -> JSONResponse:
    return cached_json(front_page(db), max_age=_TTL_S)
