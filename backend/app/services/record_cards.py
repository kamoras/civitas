"""Record cards: one official record as a picture's data, and that
picture's alt text, from one object.

A card is what an image of a record shows (app/api/og renders it, from
GET /api/cards/...): an eyebrow naming the record, a headline and lines
taken verbatim from its stored fields, a vote's tally by party, and a
source line naming where the record comes from, its page and the date the
data was read. `alt` is built from exactly those fields, so the alt text
states every figure the image does and nothing else
(test_record_cards: every field appears in it).

Read path only: ORM reads and string formatting, never a model. Whether a
bill's short title may be shown is decided at write time by the caller
(trend_poster runs the neutrality check) and passed as `show_title`.

Keys, the same in the URL and in TrendCandidate.record_key:
  vote:<chamber>/<congress>/<session>/<number>
  bill:<congress>/<bill id>
  event:<id>
  document:<id>
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.models import (
    CongressDay,
    CongressEvent,
    ExploreDocument,
    RepSponsoredBill,
    RollCall,
    RollCallPosition,
    SponsoredBill,
)
from app.ordinals import ordinal
from app.services.bill_record import bill_label, party_breakdown
from app.services.roll_call_shape import shape

SITE_DOMAIN = "civitas-research.org"

PARTY_NAMES = {"R": "Republican", "D": "Democratic", "I": "Independent", "ID": "Independent"}
# The tally's rows, in the image's fixed order.
POSITIONS = (("yea", "Yea"), ("nay", "Nay"), ("present", "Present"), ("notVoting", "Not voting"))
CHAMBER_NAMES = {"senate": "Senate", "house": "House"}
_VOTE_SOURCE = {"senate": "U.S. Senate roll-call vote record", "house": "Clerk of the House roll-call vote record"}
_EVENT_SOURCE = {"digest": "Congressional Record, Daily Digest", "floor_log": "the chamber's floor log"}
_EVENT_KIND = {
    "passed": "Measure passed", "failed": "Measure failed", "reported": "Measure reported",
    "confirmed": "Nomination confirmed", "committee": "Committee meeting", "floor": "Floor proceedings",
}
# A record line longer than this is left out of the card rather than cut:
# a cut verbatim sentence can say something the record doesn't.
MAX_LINE_CHARS = 400


def long_date(iso: str | None) -> str:
    """'2026-10-09' -> 'October 9, 2026'; '' for anything unparsable."""
    try:
        d = date.fromisoformat((iso or "")[:10])
    except ValueError:
        return ""
    return f"{d:%B} {d.day}, {d.year}"


@dataclass
class RecordCard:
    kind: str
    key: str
    eyebrow: str
    headline: str
    date: str  # the record's own date, ISO
    path: str  # its page on the site
    source_name: str
    as_of: str  # ISO date the data was read
    lines: list[str] = field(default_factory=list)
    # A vote's tally: [{"position", "label", "total", "parties": [{"party", "name", "count"}]}]
    tally: list[dict] = field(default_factory=list)

    @property
    def source(self) -> str:
        return f"Source: {self.source_name} · {SITE_DOMAIN}{self.path} · data as of {long_date(self.as_of)}"

    @property
    def alt(self) -> str:
        parts = [f"{self.eyebrow}, {long_date(self.date)}.", _sentence(self.headline)]
        parts += [_sentence(line) for line in self.lines]
        for row in self.tally:
            split = ", ".join(f"{p['name']} {p['count']}" for p in row["parties"] if p["count"])
            parts.append(f"{row['label']} {row['total']}" + (f": {split}." if split else "."))
        parts.append(self.source + ".")
        return " ".join(p for p in parts if p)

    def as_json(self) -> dict:
        out = asdict(self)
        out.update(source=self.source, alt=self.alt)
        return out


def _sentence(text: str) -> str:
    text = (text or "").strip()
    return text if not text or text[-1] in ".?!" else text + "."


def _as_of(moment: datetime | None) -> str:
    return (moment or datetime.now()).date().isoformat()


def _count(row: dict, bucket: str) -> int:
    """One party's count in a tally row: a position, or a candidate's votes."""
    if bucket.startswith("candidate:"):
        return row.get("candidates", {}).get(bucket.removeprefix("candidate:"), 0)
    return row[bucket]


def vote_card(db: Session, chamber: str, congress: int, session: int, number: int,
              show_title: bool = True) -> RecordCard | None:
    rc = db.query(RollCall).filter_by(chamber=chamber, congress=congress, session=session, number=number).one_or_none()
    if rc is None:
        return None
    positions = db.query(RollCallPosition).filter_by(roll_call_id=rc.id).all()
    rows = party_breakdown(positions)
    sh = shape(rc, positions)
    # An election of the Speaker: one row per candidate, then present and
    # not voting; a quorum call has no yeas or nays to show.
    buckets = [(f"candidate:{c['name']}", c["name"]) for c in sh["candidates"]] + [
        (b, lab) for b, lab in POSITIONS if not (sh["kind"] != "question" and b in ("yea", "nay"))]
    tally = []
    for bucket, label in buckets:
        total = sum(_count(r, bucket) for r in rows)
        if bucket == "present" and not total:
            continue
        tally.append({
            "position": bucket, "label": label, "total": total,
            "parties": [{"party": r["party"], "name": PARTY_NAMES.get(r["party"], r["party"]), "count": _count(r, bucket)}
                        for r in rows],
        })
    if not positions:  # the tally as recorded, when the positions weren't stored
        tally = [{"position": b, "label": lab, "total": t, "parties": []}
                 for (b, lab), t in zip(POSITIONS, (rc.yeas, rc.nays, rc.present, rc.not_voting))
                 if b != "present" or t]
    # What was voted on: the measure's title, or (when the caller leaves a
    # title out) its bill number. A vote with neither shows nothing of what
    # was voted on and has no card.
    measure = ((rc.title or "").strip() if show_title else "") or (bill_label(rc.bill_id) or "")
    if not measure and sh["kind"] == "election":
        measure = (rc.question or "").strip()  # "Election of the Speaker": the question is the subject
    if not measure:
        return None
    question = (rc.question or "").strip()
    named = measure.replace(" ", "") in question.replace(" ", "")  # "On the Motion to Discharge S.Res. 852"
    headline = question if question and named else f"{question}: {measure}" if question else measure
    return RecordCard(
        kind="vote", key=f"vote:{chamber}/{congress}/{session}/{number}",
        eyebrow=f"{CHAMBER_NAMES[chamber]} roll call {number}, {ordinal(congress)} Congress",
        headline=headline,
        lines=([f"Result: {sh['resultLabel']}"] if sh["resultLabel"] else []) + (
            [f"Tie broken: {sh['tieBreaker']['byWhom']} voted {sh['tieBreaker']['vote']}"] if sh["tieBreaker"] else []),
        tally=tally, date=rc.date,
        path=f"/congress/votes/{chamber}/{congress}/{session}/{number}",
        source_name=_VOTE_SOURCE[chamber], as_of=_as_of(rc.fetched_at),
    )


def bill_card(db: Session, congress: int, bill_id: str, show_title: bool = True) -> RecordCard | None:
    row = None
    for model in (SponsoredBill, RepSponsoredBill):
        row = db.query(model).filter(model.bill_id == bill_id, model.congress == congress).first()
        if row is not None:
            break
    if row is None:
        return None
    label = bill_label(bill_id) or bill_id
    lines = []
    if row.latest_action and len(row.latest_action) <= MAX_LINE_CHARS:
        lines.append(f"Latest action, {long_date(row.latest_action_date)}: {row.latest_action}")
    return RecordCard(
        kind="bill", key=f"bill:{congress}/{bill_id}",
        eyebrow=f"{label}, {ordinal(congress)} Congress",
        headline=row.title.strip() if show_title else label,
        lines=lines, date=row.latest_action_date or row.introduced_date,
        path=f"/congress/bills/{bill_id}",
        source_name="Congress.gov", as_of=_as_of(None),
    )


def event_card(db: Session, event_id: int) -> RecordCard | None:
    e = db.get(CongressEvent, event_id)
    if e is None:
        return None
    day = db.query(CongressDay).filter_by(chamber=e.chamber, date=e.date).one_or_none()
    text = (e.text or "").strip()
    return RecordCard(
        kind="event", key=f"event:{event_id}",
        eyebrow=f"{CHAMBER_NAMES.get(e.chamber, e.chamber)}: {_EVENT_KIND.get(e.kind, 'Record')}",
        headline=(e.name or "").strip(),
        lines=[text] if text and len(text) <= MAX_LINE_CHARS else [],
        date=e.date, path=f"/congress/{e.date}",
        source_name=_EVENT_SOURCE.get(e.source, "the Congressional Record"),
        as_of=_as_of(day.fetched_at if day else None),
    )


def document_card(db: Session, doc_id: int) -> RecordCard | None:
    d = db.get(ExploreDocument, doc_id)
    if d is None:
        return None
    lines = [f"Comments close {long_date(d.comments_close_on)}"] if d.comments_close_on else []
    return RecordCard(
        kind="document", key=f"document:{doc_id}",
        eyebrow=", ".join(x for x in (d.agency_name, d.doc_type) if x) or d.doc_type,
        headline=d.title.strip(), lines=lines, date=d.date[:10],
        path=f"/explore/{doc_id}",
        source_name=d.source or "Federal Register", as_of=_as_of(d.created_at),
    )


def card_for_key(db: Session, key: str, show_title: bool = True) -> RecordCard | None:
    """The card for a record key (module docstring); None for an unknown
    key or a record that isn't stored."""
    kind, _, rest = key.partition(":")
    try:
        if kind == "vote":
            chamber, congress, session, number = rest.split("/")
            if chamber not in CHAMBER_NAMES:
                return None
            return vote_card(db, chamber, int(congress), int(session), int(number), show_title)
        if kind == "bill":
            congress, bill_id = rest.split("/", 1)
            return bill_card(db, int(congress), bill_id, show_title)
        if kind == "event":
            return event_card(db, int(rest))
        if kind == "document":
            return document_card(db, int(rest))
    except ValueError:
        return None
    return None


def og_query(key: str, show_title: bool = True) -> str | None:
    """The /api/og query string that renders this record's card, or None
    for a kind with no card."""
    kind, _, rest = key.partition(":")
    params = {"vote": "vote", "bill": "bill", "event": "congressEvent", "document": "explore"}
    if kind not in params:
        return None
    return f"{params[kind]}={rest}" + ("" if show_title else "&title=0")
