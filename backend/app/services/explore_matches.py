"""Explore's typed match groups: the members, bills and agencies a search names.

Explore searches documents (speeches, rules, opinions), so a senator's surname gave
thirty floor speeches and no link to the senator's page, and "flag burning"
never offered the Flag Burners Act. These groups sit beside the results,
like the committee card: their own lists, never fused into the document
ranking, so Explore's ranking weights (config_definitions, AGENTS.md §3b)
don't move.

Members: every word of the query as the start of a word of a sitting
member's name or state (word_search, ranked by BM25 with the name weighted
over the state), and, when no name holds the words, a misspelt surname by
difflib's similarity to each word of the member's page address (the rule
the member 404 page's suggestions use, from 0.75). Bills: the bill a whole
bill number names, then bills whose titles hold every word
(bill_service.bills_matching). Agencies: every word of the query as the
start of a word of a Federal Register agency's name, short name or the
Register's own description of it (ranked by BM25, a name or short name
weighted over the description), so "aviation" offers the FAA. Names,
titles and descriptions only, verbatim; no model.
"""

import re
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from app.models import Agency, Representative, Senator
from app.photos import bioguide_photo
from app.services.bill_record import canonical_bill_id
from app.services.bill_service import bills_matching
from app.services.congress_service import bill_label
from app.services.word_search import build_index, ranked
from app.state_names import STATE_NAMES

# Shown per group; the rest are counted, and the Bills group links to them.
GROUP_LIMIT = 5
# A name term counts five times a state term: a surname that is also in a
# state's name ranks its member first, and "pennsylvania" alone still lists
# the state's delegation. BM25 column weights (name, state). Measured with
# scripts/evaluate_explore_search.py on 150 sitting members (2026-10-15):
# surname MRR 0.919 at 1:1, 0.926 at 5:1 and at 10:1.
_MEMBER_WEIGHTS = (5.0, 1.0)
# difflib's ratio a misspelt surname must reach, as /politicians/suggest
# (api/politicians.SUGGEST_MIN_RATIO). Only for a query of one word this
# long or longer: two or three letters are like half of every name.
_FUZZY_MIN_RATIO = 0.75
_FUZZY_MIN_LEN = 4
# BM25 column weights (name, short name, description): the Register's
# descriptions run to a paragraph of history, so a word there counts a
# tenth of one in the agency's name. A convention, like the member weights'
# order; the name-first order is what it fixes.
_AGENCY_WEIGHTS = (10.0, 10.0, 1.0)


def _members(db: Session) -> list[dict]:
    def row(pid, name, party, state, bioguide_id, chamber, district=None) -> dict:
        return {"id": pid, "name": name, "party": party, "state": state, "chamber": chamber,
                "district": district, "thumbnailUrl": bioguide_photo(bioguide_id)}

    current = (Senator.is_current == True, Representative.is_current == True)  # noqa: E712
    return [
        *(row(*r, "senate") for r in db.query(
            Senator.id, Senator.name, Senator.party, Senator.state, Senator.bioguide_id,
        ).filter(current[0])),
        *(row(*r[:5], "house", r[5]) for r in db.query(
            Representative.id, Representative.name, Representative.party, Representative.state,
            Representative.bioguide_id, Representative.district,
        ).filter(current[1])),
    ]


def member_matches(db: Session, q: str) -> tuple[int, list[dict]]:
    """(how many sitting members match, the best GROUP_LIMIT of them)."""
    members = {m["id"]: m for m in _members(db)}
    conn = build_index(((m["id"], m["name"], STATE_NAMES.get(m["state"], m["state"])) for m in members.values()),
                       n_texts=2)
    try:
        keys = ranked(conn, q, _MEMBER_WEIGHTS)
    finally:
        conn.close()
    if not keys:
        want = re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")
        if len(want) >= _FUZZY_MIN_LEN and "-" not in want:
            likeness = {
                pid: max(SequenceMatcher(None, want, w).ratio() for w in pid.split("-") if w)
                for pid in members
            }
            keys = sorted((p for p, r in likeness.items() if r >= _FUZZY_MIN_RATIO),
                          key=lambda p: (-likeness[p], members[p]["name"]))
    return len(keys), [members[k] for k in keys[:GROUP_LIMIT]]


def bill_matches(db: Session, q: str) -> tuple[int, list[dict]]:
    """(how many bills match, the best GROUP_LIMIT of them). A whole bill
    number is offered even when no sitting member sponsored it: every bill
    has a page, from Congress.gov's record."""
    total, bills = bills_matching(db, q, GROUP_LIMIT)
    items = [{
        "billId": b.bill_id, "congress": b.congress, "label": bill_label(b.bill_id) or b.bill_id,
        "title": b.title, "shortTitle": b.short_title, "stage": b.stage,
    } for b in bills]
    whole = canonical_bill_id(q.strip())
    if whole and not items:
        items = [{"billId": whole, "congress": None, "label": bill_label(whole) or whole,
                  "title": None, "shortTitle": None, "stage": None}]
        total = 1
    return total, items


def agency_matches(db: Session, q: str) -> tuple[int, list[dict]]:
    """(how many Federal Register agencies match, the best GROUP_LIMIT of
    them). None stored yet (the agency list not read) is no match."""
    agencies = {a.slug: a for a in db.query(Agency.slug, Agency.name, Agency.short_name, Agency.description)}
    if not agencies:
        return 0, []
    conn = build_index(((a.slug, a.name, a.short_name or "", a.description or "") for a in agencies.values()),
                       n_texts=3)
    try:
        keys = ranked(conn, q, _AGENCY_WEIGHTS)
    finally:
        conn.close()
    return len(keys), [{"slug": k, "name": agencies[k].name, "shortName": agencies[k].short_name}
                       for k in keys[:GROUP_LIMIT]]


def explore_matches(db: Session, q: str) -> dict:
    members_total, members = member_matches(db, q)
    bills_total, bills = bill_matches(db, q)
    agencies_total, agencies = agency_matches(db, q)
    return {
        "members": {"total": members_total, "items": members},
        "bills": {"total": bills_total, "items": bills},
        "agencies": {"total": agencies_total, "items": agencies},
    }
