"""The Supreme Court as a whole, for /court and each justice's case list:
where the sitting justices sit (Martin-Quinn), how often each pair voted
together, how the Court split by term, and each decision's lineup.

Records and measurements only: nothing here is a score or a ranking
(justice v3, docs/research/justice-scores.md). Every figure is counted from
the stored votes (`justice_votes`, from the Supreme Court Database and, for
a term it doesn't cover yet, Oyez) and the stored Martin-Quinn positions;
the frontend only words and draws them.
"""

import json
import re
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import ExploreDocument, Justice, JusticeVote
from app.photos import justice_photo
from app.services.explore_keys import public_key
from app.time_utils import utcnow

# The Court's term begins on the first Monday in October (28 U.S.C. § 2)
# and is named for the year it begins in.
_TERM_MONTH = 10
# The Supreme Court Database's own case id ("2024-001"), which a stored
# case id carries where the Database gave no docket number: not an Oyez
# address.
_SCDB_CASE_ID = re.compile(r"^\d{4}-\d{3}$")
# Explore's title for an opinion, "Name (No. 24-43)": the name is the
# Court's own capitalisation, where the Database's is upper case.
_DOCKET_SUFFIX = re.compile(r"\s*\(No\.[^)]*\)\s*$")
_SIDES = ("majority", "minority")
MAX_PER_PAGE = 50


def current_term(today: date | None = None) -> int:
    """The term in session or most recently begun on `today`."""
    today = today or utcnow().date()
    october = date(today.year, _TERM_MONTH, 1)
    first_monday = october + timedelta(days=(7 - october.weekday()) % 7)
    return today.year if today >= first_monday else today.year - 1


def _split_key(split: tuple[int, int]) -> tuple[int, int]:
    """Lopsided first: 9-0, 8-0, 8-1, 7-2 … 5-4."""
    maj, mnr = split
    return (-(maj - mnr), -maj)


def _positions(j: Justice) -> list[list]:
    try:
        points = json.loads(j.ideal_points or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return [p for p in points if isinstance(p, list) and len(p) >= 2] if isinstance(points, list) else []


def _position(j: Justice, term: int | None) -> dict | None:
    """The justice's Martin-Quinn estimate for `term`: the posterior mean
    and, where the file gave it, the 95% credible interval."""
    for p in _positions(j):
        if p[0] == term:
            out = {"term": p[0], "position": p[1], "low": None, "high": None}
            if len(p) >= 4:
                out["low"], out["high"] = p[2], p[3]
            return out
    return None


def _docket(case_id: str, term: str) -> str | None:
    """The docket number in a stored case id ("scotus-2024-23-191"), or
    None where it carries the Database's own id instead."""
    rest = case_id.removeprefix(f"scotus-{term}-")
    return None if rest == case_id or not rest or _SCDB_CASE_ID.match(rest) else rest


def _links(case_id: str, term: str, doc: ExploreDocument | None) -> dict:
    docket = _docket(case_id, term)
    url = (doc.url or "") if doc else ""
    return {
        "oyez": f"https://www.oyez.org/cases/{term}/{docket}" if docket else None,
        # The Court's slip opinion, once posted; before that Explore holds
        # the docket page, which is not an opinion.
        "opinion": url if "/opinions/" in url else None,
        "explore": f"/explore/{public_key(doc.external_id, doc.id)}" if doc else None,
    }


def _documents(db: Session, case_ids: set[str]) -> dict[str, ExploreDocument]:
    if not case_ids:
        return {}
    rows = db.query(ExploreDocument).filter(ExploreDocument.external_id.in_(case_ids)).all()
    return {d.external_id: d for d in rows}


def _case_name(stored: str, doc: ExploreDocument | None) -> str:
    if doc and doc.title:
        return _DOCKET_SUFFIX.sub("", doc.title) or stored
    return stored


def _cases(votes: list[JusticeVote]) -> dict[str, dict]:
    """Each case's lineup from its stored votes."""
    cases: dict[str, dict] = {}
    for v in votes:
        c = cases.setdefault(v.case_id, {
            "caseId": v.case_id, "name": v.case_name, "term": v.case_term, "decided": v.decided_date,
            "majorityVotes": 0, "minorityVotes": 0, "majority": [], "minority": [], "author": None,
        })
        # The Court's own tally, the same on every justice's row; a justice
        # no longer stored still counts in it.
        c["majorityVotes"] = max(c["majorityVotes"], v.majority_votes or 0)
        c["minorityVotes"] = max(c["minorityVotes"], v.minority_votes or 0)
        if v.vote in _SIDES:
            c[v.vote].append(v.justice_id)
        if v.opinion_type == "majority" and v.vote == "majority":
            c["author"] = v.justice_id
    for c in cases.values():
        c["split"] = f"{c['majorityVotes']}-{c['minorityVotes']}"
        c["unanimous"] = c["minorityVotes"] == 0 and c["majorityVotes"] > 0
    return cases


def _page(items: list, page: int, per_page: int) -> tuple[list, int, int]:
    per_page = max(1, min(per_page, MAX_PER_PAGE))
    page = max(1, page)
    start = (page - 1) * per_page
    return items[start:start + per_page], page, per_page


def _newest_first(cases: list[dict]) -> list[dict]:
    return sorted(cases, key=lambda c: (c["decided"] or "", c["name"]), reverse=True)


def decisions(db: Session, page: int = 1, per_page: int = 10, term: str | None = None) -> dict:
    """Decisions newest first, each with its lineup and its sources."""
    q = db.query(JusticeVote)
    if term:
        q = q.filter(JusticeVote.case_term == term)
    cases = _newest_first(list(_cases(q.all()).values()))
    shown, page, per_page = _page(cases, page, per_page)
    docs = _documents(db, {c["caseId"] for c in shown})
    for c in shown:
        doc = docs.get(c["caseId"])
        c["name"] = _case_name(c["name"], doc)
        c["links"] = _links(c["caseId"], c["term"], doc)
    return {"total": len(cases), "page": page, "perPage": per_page, "decisions": shown}


def justice_cases(db: Session, justice_id: str, page: int = 1, per_page: int = 20) -> dict | None:
    """One justice's votes newest first: the side, the opinion written and
    the Court's split. None for no such justice."""
    if db.get(Justice, justice_id) is None:
        return None
    rows = db.query(JusticeVote).filter(JusticeVote.justice_id == justice_id).all()
    rows.sort(key=lambda v: (v.decided_date or "", v.case_name), reverse=True)
    shown, page, per_page = _page(rows, page, per_page)
    docs = _documents(db, {v.case_id for v in shown})
    out = []
    for v in shown:
        doc = docs.get(v.case_id)
        out.append({
            "caseId": v.case_id, "name": _case_name(v.case_name, doc), "term": v.case_term,
            "decided": v.decided_date, "vote": v.vote, "opinion": v.opinion_type,
            "split": f"{v.majority_votes}-{v.minority_votes}", "links": _links(v.case_id, v.case_term, doc),
        })
    terms = sorted({int(v.case_term) for v in rows if v.case_term.isdigit()})
    return {"total": len(rows), "page": page, "perPage": per_page, "terms": terms, "cases": out}


def _agreement(sitting: list[str], cases: dict[str, dict]) -> list[dict]:
    """Every pair of sitting justices: the cases both decided, and how many
    of them they decided the same way (justice_analyzer's measure)."""
    index = {jid: i for i, jid in enumerate(sitting)}
    both: dict[tuple[str, str], int] = defaultdict(int)
    agree: dict[tuple[str, str], int] = defaultdict(int)
    for c in cases.values():
        side = {jid: s for s in _SIDES for jid in c[s] if jid in index}
        voted = sorted(side, key=index.get)
        for i, a in enumerate(voted):
            for b in voted[i + 1:]:
                both[(a, b)] += 1
                agree[(a, b)] += side[a] == side[b]
    return [
        {"a": a, "b": b, "agree": agree[(a, b)], "both": n, "share": round(agree[(a, b)] / n * 100, 1)}
        for (a, b), n in both.items()
    ]


def _lineups(cases: dict[str, dict]) -> dict:
    """How the Court split, term by term: the splits in order, and each
    term's count of each."""
    by_term: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    splits: set[tuple[int, int]] = set()
    for c in cases.values():
        by_term[c["term"]][c["split"]] += 1
        splits.add((c["majorityVotes"], c["minorityVotes"]))
    order = [f"{m}-{n}" for m, n in sorted(splits, key=_split_key)]
    return {
        "splits": order,
        "terms": [
            {"term": int(t), "cases": sum(counts.values()), "counts": {s: counts.get(s, 0) for s in order}}
            for t, counts in sorted(by_term.items()) if t.isdigit()
        ],
    }


def court(db: Session, recent: int = 10) -> dict:
    """Everything /court shows, from the stored record."""
    justices = db.query(Justice).filter(Justice.is_active.is_(True)).all()
    # The newest term any sitting justice has a position for: Martin and
    # Quinn publish a term after it ends, so it trails the record.
    mq_term = max((p[0] for j in justices for p in _positions(j)), default=None)
    rows = []
    for j in justices:
        rows.append({
            "id": j.id, "name": j.name, "lastName": j.last_name, "roleTitle": j.role_title,
            "appointingPresident": j.appointing_president or None, "appointingParty": j.appointing_party or None,
            "dateStart": j.date_start, "photo": justice_photo(j), "mq": _position(j, mq_term),
        })
    # Left to right; a justice with no position for that term (newly
    # confirmed) after, by seniority.
    rows.sort(key=lambda r: (r["mq"] is None, r["mq"]["position"] if r["mq"] else 0, r["dateStart"] or ""))
    placed = [r["id"] for r in rows if r["mq"]]
    median = [placed[len(placed) // 2]] if len(placed) % 2 else placed[len(placed) // 2 - 1:len(placed) // 2 + 1]

    sitting = [r["id"] for r in rows]
    all_votes = db.query(JusticeVote).all()
    cases = _cases(all_votes)
    terms = sorted({int(c["term"]) for c in cases.values() if c["term"].isdigit()})
    unanimous = sum(1 for c in cases.values() if c["unanimous"])
    one_vote = sum(1 for c in cases.values() if c["majorityVotes"] - c["minorityVotes"] == 1)
    now = current_term()
    return {
        "justices": rows,
        "mqTerm": mq_term,
        "median": median if placed else [],
        "record": {
            "cases": len(cases), "unanimous": unanimous, "oneVote": one_vote,
            "unanimousPct": round(unanimous / len(cases) * 100) if cases else None,
            "oneVotePct": round(one_vote / len(cases) * 100) if cases else None,
            "terms": terms,
        },
        "currentTerm": {"term": now, "decided": sum(1 for c in cases.values() if c["term"] == str(now))},
        "agreement": _agreement(sitting, cases),
        "lineups": _lineups(cases),
        "decisions": decisions(db, 1, recent),
    }

