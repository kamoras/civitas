"""Committee meetings and reports, findable by committee, by words and by
bill (GET /api/congress/committees, /api/congress/committee-record,
/api/bills/{id}/committee-record).

The rows are the Daily Digest's own (CongressEvent, written by the
nightly Congress activity run): a meeting is a committee's entry for the
day ("Committee concluded a hearing to examine ..."), a report a measure
the chamber received a committee report on. Their text is served as the
Record printed it. Nothing here is indexed or embedded: about four
thousand rows a Congress are read with plain SQL filters per request, so
there is no extra nightly step and nothing in the analysis fingerprint
moves.

The Digest names the committee for a meeting but not for a report (its
"Reports Filed" list names the measure and the report number), so reports
are findable by words and by bill, not by committee.
"""

import re

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import CongressEvent
from app.pipeline.fetch.congress import congress_first_year, congress_of_date
from app.pipeline.fetch.daily_digest import bill_ids, only_bill_id
from app.services.bill_record import parse_bill_id
from app.services.congress_service import bill_label
from app.services.pagination import paginate_bounds

# CongressEvent.kind for each, and the word the API uses.
KINDS = {"meeting": "committee", "report": "reported"}
_KIND_WORD = {v: k for k, v in KINDS.items()}

# The Record's "(H. Rept. 119-851)" / "(S. Rept. No. 119-155)".
_REPORT_NO = re.compile(r"\(([HS])\.\s?Rept\.\s?(?:No\.\s?)?(\d+-\d+)\)")

# Words shorter than this are ignored in a search ("of", "to"): every
# entry has them.
_MIN_WORD = 3


def _entry(e: CongressEvent) -> dict:
    report = _REPORT_NO.search(e.text) if e.kind == "reported" else None
    mentioned = bill_ids(e.text)
    if e.bill_id and e.bill_id not in mentioned:
        mentioned.insert(0, e.bill_id)
    return {
        "id": e.id,
        "kind": _KIND_WORD[e.kind],
        "chamber": e.chamber,
        "date": e.date,
        # The Congress the day fell in: a bill number names a different
        # bill in each, so a link to one needs it.
        "congress": congress_of_date(e.date),
        # A meeting's committee as the Digest names it; null for a report.
        "committee": (e.name or None) if e.kind == "committee" else None,
        "text": e.text,
        "pages": e.pages,
        "report": f"{report.group(1)}. Rept. {report.group(2)}" if report else None,
        "bills": [{"billId": b, "billLabel": bill_label(b)} for b in mentioned if bill_label(b)],
    }


def committees(db: Session) -> list[dict]:
    """Every committee with a meeting on record, by chamber and name: how
    many meetings, and the latest one's date."""
    rows: dict[tuple[str, str], dict] = {}
    for chamber, name, day in (
        db.query(CongressEvent.chamber, CongressEvent.name, CongressEvent.date)
        .filter(CongressEvent.kind == "committee", CongressEvent.source == "digest", CongressEvent.name != "")
    ):
        entry = rows.setdefault((chamber, name), {"chamber": chamber, "name": name, "meetings": 0, "latest": day})
        entry["meetings"] += 1
        entry["latest"] = max(entry["latest"], day)
    return sorted(rows.values(), key=lambda c: (c["chamber"], c["name"]))


def _words(q: str) -> list[str]:
    return [w for w in re.findall(r"[\w'-]+", q) if len(w) >= _MIN_WORD]


def search(
    db: Session,
    *,
    chamber: str | None = None,
    committee: str | None = None,
    q: str | None = None,
    bill: str | None = None,
    kind: str | None = None,
    congress: int | None = None,
    page: int = 1,
    per_page: int = 20,
) -> dict:
    """Meetings and reports, newest first, narrowed by chamber, committee
    (a meeting's), words (all of them, anywhere in the entry or the
    committee's name), a bill (named anywhere in the entry), the kind and
    the Congress. A query that is a bill number ("S. 465", "H.R. 1234") is
    read as that bill."""
    query = db.query(CongressEvent).filter(
        CongressEvent.source == "digest",
        CongressEvent.kind.in_([KINDS[kind]] if kind in KINDS else list(KINDS.values())),
    )
    if chamber:
        query = query.filter(CongressEvent.chamber == chamber)
    if committee:
        query = query.filter(CongressEvent.kind == "committee", CongressEvent.name == committee)
    if congress:
        first = congress_first_year(congress)
        query = query.filter(CongressEvent.date >= f"{first}-01-03", CongressEvent.date < f"{first + 2}-01-03")
    if q and not bill:
        typed = q.strip().upper()
        named = only_bill_id(q) or (typed if parse_bill_id(typed) else None)
        if named:
            bill, q = named, None
    for word in _words(q or ""):
        query = query.filter(or_(
            CongressEvent.text.contains(word, autoescape=True),
            CongressEvent.name.contains(word, autoescape=True),
        ))
    if bill:
        number = bill.split(".", 1)[-1]
        query = query.filter(or_(CongressEvent.bill_id == bill, CongressEvent.text.contains(number)))
    rows = query.order_by(CongressEvent.date.desc(), CongressEvent.seq).all()
    if bill:
        rows = [e for e in rows if e.bill_id == bill or bill in bill_ids(e.text)]
    total_pages, page = paginate_bounds(len(rows), page, per_page)
    return {
        "entries": [_entry(e) for e in rows[(page - 1) * per_page : page * per_page]],
        "total": len(rows),
        "page": page,
        "perPage": per_page,
        "totalPages": total_pages,
        "bill": {"billId": bill, "billLabel": bill_label(bill)} if bill else None,
        # For a bill: what the Digest holds on it, as one templated line
        # (the bill page's "In committee" box).
        "headline": _bill_headline(rows, bill) if bill else None,
    }


def _bill_headline(rows: list[CongressEvent], bill: str) -> str:
    meetings = sum(1 for e in rows if e.kind == KINDS["meeting"])
    reports = len(rows) - meetings
    label = bill_label(bill)
    if not rows:
        return f"No committee meeting or report in the Daily Digest names {label}"
    parts = [f"{n:,} {word if n == 1 else word + 's'}"
             for n, word in ((meetings, "committee meeting"), (reports, "report")) if n]
    return f"{' and '.join(parts)} in the Daily Digest {'names' if len(rows) == 1 else 'name'} {label}"

