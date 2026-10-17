"""Read side of the nominations (pipeline/fetch/nominations.py): the list,
one nomination with the roll calls that cite it, the Congress's counts and
how long confirmations took — every figure counted here from the stored
rows, so a page draws it and computes nothing.

The roll-call join is on the identifier the Senate's own record states: the
vote question names its nominations by citation ("On the Nomination
PN999-1", or seventy at once for an en bloc vote), read as a token, never
matched loosely.
"""

import json
import statistics
from collections import Counter
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Agency, AgencyBudget, Nomination, RollCall
from app.pipeline.fetch.agencies import normalized_name
from app.ordinals import ordinal
from app.pipeline.fetch.nominations import HEARING_CODES, JUDICIARY, MAX_PER_RUN, STATUSES, read_marker
from app.services.bill_record import MemberLinks, vote_detail
from app.services.roll_call_shape import shape
from app.services.nomination_links import citations_in, nominee_names, vote_label

KINDS = ("civilian", "judicial", "military")
# Width of the time-to-confirm histogram's bars, in days.
BIN_DAYS = 30


def agency_index(db: Session) -> dict[str, str]:
    """{normalized name: agency slug} for every name an agency goes by in
    the registry (fetch/agencies.py): the Federal Register's name and short
    name, and the USAspending name its crosswalk already matched to it. A
    name two agencies share is left out, so a lookup is exact or nothing."""
    names: dict[str, set[str]] = {}
    for slug, name, short in db.execute(select(Agency.slug, Agency.name, Agency.short_name)):
        for n in (name, short):
            if n:
                names.setdefault(normalized_name(n), set()).add(slug)
    for name, slug in db.execute(
        select(AgencyBudget.name, AgencyBudget.agency_slug).where(AgencyBudget.agency_slug.isnot(None))
    ):
        names.setdefault(normalized_name(name), set()).add(slug)
    return {n: next(iter(slugs)) for n, slugs in names.items() if len(slugs) == 1}


def agency_slug(organization: str, index: dict[str, str]) -> str | None:
    """The registry agency Congress.gov's organization names, exactly; None
    when none does (the page shows the organization as text)."""
    return index.get(normalized_name(organization)) if organization else None


def kind_of(row: Nomination) -> str:
    """Congress.gov's own split: military, the judiciary (its
    organization), and every other civilian nomination."""
    if row.is_military:
        return "military"
    return "judicial" if row.organization == JUDICIARY else "civilian"


def _kind_filter(kind: str):
    if kind == "military":
        return Nomination.is_military.is_(True)
    if kind == "judicial":
        return (Nomination.is_military.is_(False)) & (Nomination.organization == JUDICIARY)
    return (Nomination.is_military.is_(False)) & (Nomination.organization != JUDICIARY)


def days_to_confirm(row: Nomination) -> int | None:
    if row.status != "confirmed" or not row.status_date or not row.received_date:
        return None
    try:
        return (date.fromisoformat(row.status_date) - date.fromisoformat(row.received_date)).days
    except ValueError:
        return None


def congress_gov_url(row: Nomination) -> str:
    part = int(row.part_number) if (row.part_number or "").isdigit() else 0
    tail = f"{row.number}/{part}" if part else f"{row.number}"
    return f"https://www.congress.gov/nomination/{ordinal(row.congress)}-congress/{tail}"


def summary(row: Nomination, agencies: dict[str, str] | None = None) -> dict:
    names = nominee_names(row)
    return {
        "agencySlug": agency_slug(row.organization, agencies) if agencies is not None else None,
        "congress": row.congress, "citation": row.citation, "description": row.description,
        "organization": row.organization, "positionTitle": row.position_title, "kind": kind_of(row),
        "nominees": names, "nomineeCount": row.nominee_count,
        "receivedDate": row.received_date, "status": row.status, "statusDate": row.status_date,
        "latestAction": {"date": row.latest_action_date, "text": row.latest_action_text},
        "daysToConfirm": days_to_confirm(row),
        "congressGovUrl": congress_gov_url(row),
    }


def list_nominations(
    db: Session, congress: int, *, status: str | None = None, kind: str | None = None,
    agency: str | None = None, q: str | None = None, page: int = 1, per_page: int = 25,
) -> dict:
    """Newest action first. `agency` is the organization as Congress.gov
    names it; `q` matches every word somewhere in the description (it names
    the nominee, their state and the office)."""
    base = select(Nomination).where(Nomination.congress == congress)
    if kind:
        base = base.where(_kind_filter(kind))
    if agency:
        base = base.where(Nomination.organization == agency)
    for word in (q or "").split():
        base = base.where(Nomination.description.ilike(f"%{word}%"))
    filtered = base.subquery()
    by_status = dict(db.execute(select(filtered.c.status, func.count()).group_by(filtered.c.status)).all())
    listed = base.where(Nomination.status == status) if status else base
    total = db.execute(select(func.count()).select_from(listed.subquery())).scalar_one()
    agencies = agency_index(db)
    rows = db.execute(
        listed.order_by(Nomination.latest_action_date.desc(), Nomination.number.desc(), Nomination.part_number)
        .offset((page - 1) * per_page).limit(per_page)
    ).scalars().all()
    return {
        "congress": congress, "total": total, "page": page, "perPage": per_page,
        "counts": {s: by_status.get(s, 0) for s in STATUSES},
        "allStatuses": sum(by_status.values()),
        "items": [summary(r, agencies) for r in rows],
    }


def _distribution(days: list[int]) -> dict:
    if not days:
        return {"n": 0, "median": None, "p25": None, "p75": None, "bins": []}
    quartiles = statistics.quantiles(days, n=4, method="inclusive") if len(days) > 1 else [days[0]] * 3
    counts = Counter(d // BIN_DAYS for d in days)
    return {
        "n": len(days), "median": statistics.median(days), "p25": quartiles[0], "p75": quartiles[2],
        "bins": [{"start": b * BIN_DAYS, "end": (b + 1) * BIN_DAYS, "count": counts.get(b, 0)}
                 for b in range(max(counts) + 1)],
    }


def backfill(db: Session, congress: int, read: int) -> dict:
    """Whether the sync has read every nomination of the Congress yet. Until
    it has, every count and median is of a sample (the oldest updates
    first), and the page says so: `read` of about `expected`, and how many
    more daily runs at MAX_PER_RUN the rest should take. Unknown (null)
    before the sync's first run."""
    marker = read_marker(db, congress)
    complete = bool(marker.get("backfillComplete"))
    expected = marker.get("expected")
    if expected is not None:
        expected = max(expected, read)
    nights = None if complete or expected is None else -(-(expected - read) // MAX_PER_RUN)
    return {"complete": complete, "read": read, "expected": expected, "nightsLeft": nights}


def stats(db: Session, congress: int) -> dict:
    """The Congress's nominations by kind and status, how many days each
    confirmed one took from receipt to confirmation (as a histogram of
    BIN_DAYS bars, with its quartiles), and the organizations named."""
    rows = db.execute(select(Nomination).where(Nomination.congress == congress)).scalars().all()
    by_kind: dict[str, dict] = {}
    for kind in KINDS:
        mine = [r for r in rows if kind_of(r) == kind]
        status_counts = Counter(r.status for r in mine)
        by_kind[kind] = {
            "nominations": len(mine),
            "people": sum(r.nominee_count for r in mine),
            "counts": {s: status_counts.get(s, 0) for s in STATUSES},
            "timeToConfirm": _distribution(sorted(d for r in mine if (d := days_to_confirm(r)) is not None)),
        }
    orgs = Counter(r.organization for r in rows if r.organization)
    agencies = agency_index(db)
    status_counts = Counter(r.status for r in rows)
    latest = max((r.update_date for r in rows), default=None)
    return {
        "congress": congress,
        "total": len(rows),
        "people": sum(by_kind[k]["people"] for k in KINDS),
        "backfill": backfill(db, congress, len(rows)),
        "counts": {s: status_counts.get(s, 0) for s in STATUSES},
        "kinds": by_kind,
        "binDays": BIN_DAYS,
        "organizations": [{"name": n, "count": c, "agencySlug": agency_slug(n, agencies)}
                          for n, c in sorted(orgs.items())],
        "asOf": latest[:10] if latest else None,
    }


def roll_calls_citing(db: Session, row: Nomination) -> list[RollCall]:
    """The Senate roll calls whose question names this citation, oldest
    first."""
    candidates = db.execute(select(RollCall).where(
        RollCall.chamber == "senate", RollCall.congress == row.congress,
        RollCall.question.like(f"%{row.citation}%"),
    ).order_by(RollCall.date, RollCall.session, RollCall.number)).scalars().all()
    return [rc for rc in candidates if row.citation in citations_in(rc.question)]


def detail(db: Session, congress: int, citation: str) -> dict | None:
    row = db.execute(select(Nomination).filter_by(congress=congress, citation=citation)).scalar_one_or_none()
    if row is None:
        return None
    links = MemberLinks(db)
    votes = roll_calls_citing(db, row)
    # The vote on the nomination itself (the last, if it came to more than
    # one), with every senator's position: who voted how, by state.
    final = next((rc for rc in reversed(votes) if rc.question.startswith("On the Nomination")), None)
    final_detail = vote_detail(db, final, links) if final else None
    return {
        **summary(row, agency_index(db)),
        "nominees": json.loads(row.nominees or "[]"),
        "actions": json.loads(row.actions or "[]"),
        "hearings": [
            {**h, "url": (f"https://www.govinfo.gov/app/details/CHRG-{row.congress}shrg{h['jacketNumber']}"
                          if h.get("jacketNumber") else None)}
            for h in json.loads(row.hearings or "[]")
        ],
        # An action says a hearing was held, whether or not Congress.gov
        # lists its transcript yet.
        "hearingHeld": any(a.get("code") in HEARING_CODES for a in json.loads(row.actions or "[]")),
        "committee": {"code": row.committee_code, "name": row.committee_name} if row.committee_code else None,
        "fjc": ({"judgeId": row.fjc_judge_id, "url": f"https://www.fjc.gov/node/{row.fjc_node_id}"}
                if row.fjc_judge_id else None),
        "rollCalls": [{
            "chamber": rc.chamber, "congress": rc.congress, "session": rc.session, "number": rc.number,
            "date": rc.date, "question": rc.question, "label": vote_label(rc.question)[1],
            "result": rc.result, "rejected": rc.rejected,
            "yeas": rc.yeas, "nays": rc.nays, "present": rc.present, "notVoting": rc.not_voting,
            "sourceUrl": rc.source_url, "citations": len(citations_in(rc.question)), **shape(rc),
            "parties": final_detail["parties"] if rc is final else None,
        } for rc in votes],
        "finalVote": ({"session": final.session, "number": final.number, "members": final_detail["members"]}
                      if final else None),
        "updateDate": row.update_date,
    }
