"""The official record behind a Bluesky trend, found only on a pinned match.

A trend post states a record, never a reading of the trend, so the match
between the two has to be one a reader could check by eye. There are four,
and nothing else counts:

  - issue: an Action Center issue whose headline is at cosine >= 0.5 to
    the trend (all-MiniLM-L6-v2, the similarity model). The record shown is
    the official record the issue already links (a vote on its bill, a
    Federal Register document, the bill) when there is one, else the issue.
  - act: a statute's short title the trend names ("... Act", the naming
    convention of U.S. statutes) found in a record's own title.
  - nominee: a nominee a roll call names ("<Name>, of <State>, to be ...",
    the Senate's format for a nomination vote) named in full by the trend.
  - member: a member of Congress the trend names (in full, or by a surname
    under action_center's collision safeguards: one holder, not a common
    word, not part of someone else's name), AND a bill that member
    sponsored at cosine >= 0.5 to the trend. A named member with no such
    record is not a match: a post about a person is not a post about a
    record.

Every record must be dated on or before the trend's start plus
EVIDENCE_GRACE (a vote taken the day after a topic began trending is the
record it trended about; a record from weeks later is not), and within the
sitting Congress.

This module finds; trend_poster decides. Nothing here reads a party: the
parties are collected for the audit row only (Evidence.named_parties,
sponsor_parties), and selection never reads them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
from sqlalchemy.orm import Session

from app.config import settings
from app.issue_ids import to_public_id
from app.models import (
    ActionIssue,
    CongressEvent,
    ExploreDocument,
    Representative,
    RepSponsoredBill,
    RollCall,
    Senator,
    SponsoredBill,
)
from app.pipeline.analyze.action_center import _embed_texts_sim, _find_related_senators, _mentions_full_name
from app.pipeline.fetch.congress import congress_first_year

# The pinned cosine for an issue match and a member's bill: the trend-post
# design's decision, not a fit. It is only one of the gates (the relevance
# vote, the date rule and the record itself must also hold); the backtest
# in the PR that added it lists every trend it let through, with its record.
MATCH_MIN_COSINE = 0.5
# A record may be dated up to this long after the trend started.
EVIDENCE_GRACE = timedelta(days=2)
# How far back an Action Center issue can anchor a trend.
ISSUE_LOOKBACK = timedelta(days=14)
# Explore document types that are an agency's or the President's own
# record (floor speeches are a member talking, not an official act).
OFFICIAL_DOCUMENT_TYPES = frozenset({
    "Executive Order", "Final Rule", "Proposed Rule", "Notice", "Presidential Memorandum",
    "Proclamation", "Supreme Court Opinion",
})
# Congress events that record an act of the chamber or a committee.
EVENT_KINDS = ("passed", "failed", "reported", "confirmed", "committee")
# Which record of a story to show, most decisive first: a recorded vote, an
# agency's document, the chamber's record of the day, the bill, the issue.
RECORD_PREFERENCE = {"vote": 0, "document": 1, "event": 2, "bill": 3, "issue": 4}
OFFICIAL_KINDS = frozenset({"vote", "document", "event", "bill"})

_SITE = "https://civitas-research.org"
# A statute's short title as a trend writes it: capitalised words ending in
# "Act". A naming convention of U.S. law, not a classifier: the match is
# then an exact containment in a record's own title.
_ACT_PHRASE_RE = re.compile(r"\b((?:[A-Z][\w'’&-]*\s+){1,7}Act)\b")
_LEADING_ARTICLE_RE = re.compile(r"^(?:The|A|An|This|That)\s+")
# The Senate's nomination vote title: "<Name>, of <State>, to be ...".
_NOMINEE_RE = re.compile(r"^([^,]+),\s+of\s+")


@dataclass
class Record:
    kind: str  # vote | bill | event | document | issue
    key: str
    date: str
    url: str
    text: str  # what is embedded for the cosine
    bill_id: str | None = None
    congress: int | None = None
    parties: list[str] = field(default_factory=list)  # sponsors / named officials, audit only
    issue_id: int | None = None


@dataclass
class Evidence:
    match_type: str  # issue | act | nominee | member
    strength: float
    record: Record
    named_parties: list[str]
    sponsor_parties: list[str]
    anchor_issue_id: int | None = None
    detail: str = ""


def _iso(d: date) -> str:
    return d.isoformat()


class RecordIndex:
    """The records a run's trends can match, read once per run."""

    def __init__(self, db: Session, now: datetime):
        self.db = db
        congress = settings.CURRENT_CONGRESS
        self.congress = congress
        self.since = f"{congress_first_year(congress)}-01-03"
        self.today = now.date()
        self.votes = (db.query(RollCall).filter(RollCall.date >= self.since)
                      .order_by(RollCall.date.desc(), RollCall.number.desc()).all())
        self.events = (db.query(CongressEvent)
                       .filter(CongressEvent.date >= self.since, CongressEvent.kind.in_(EVENT_KINDS))
                       .order_by(CongressEvent.date.desc()).all())
        self.bills: dict[str, tuple[object, str]] = {}  # bill id -> (row, sponsor party)
        self.bills_by_member: dict[str, list] = {}
        for model, owner, fk in ((SponsoredBill, Senator, "senator_id"), (RepSponsoredBill, Representative, "representative_id")):
            party_of = dict(db.query(owner.id, owner.party).all())
            for row in db.query(model).filter(model.congress == congress).all():
                member = getattr(row, fk)
                self.bills_by_member.setdefault(member, []).append(row)
                self.bills.setdefault(row.bill_id, (row, party_of.get(member, "")))
        self.issues = (db.query(ActionIssue)
                       .filter(ActionIssue.date >= _iso(self.today - ISSUE_LOOKBACK - EVIDENCE_GRACE))
                       .order_by(ActionIssue.date.desc()).all())
        self._issue_emb: np.ndarray | None = None

    # -- records -------------------------------------------------------
    def sponsor_party(self, bill_id: str | None) -> list[str]:
        hit = self.bills.get(bill_id or "")
        return [hit[1]] if hit and hit[1] else []

    def vote_record(self, rc: RollCall) -> Record:
        return Record("vote", f"vote:{rc.chamber}/{rc.congress}/{rc.session}/{rc.number}", rc.date,
                      f"{_SITE}/congress/votes/{rc.chamber}/{rc.congress}/{rc.session}/{rc.number}",
                      f"{rc.question}: {rc.title}", bill_id=rc.bill_id, congress=rc.congress,
                      parties=self.sponsor_party(rc.bill_id))

    def event_record(self, e: CongressEvent) -> Record:
        return Record("event", f"event:{e.id}", e.date, f"{_SITE}/congress/{e.date}",
                      f"{e.name}: {e.text}", bill_id=e.bill_id, parties=self.sponsor_party(e.bill_id))

    def bill_record(self, row) -> Record:
        return Record("bill", f"bill:{row.congress}/{row.bill_id}", row.latest_action_date or row.introduced_date,
                      f"{_SITE}/congress/bills/{row.bill_id}", row.title, bill_id=row.bill_id,
                      congress=row.congress, parties=self.sponsor_party(row.bill_id))

    def document_record(self, d: ExploreDocument) -> Record:
        return Record("document", f"document:{d.id}", d.date[:10], f"{_SITE}/explore/{d.id}", d.title)

    def issue_record(self, issue: ActionIssue) -> Record:
        try:
            parties = [m.get("party", "") for m in json.loads(issue.related_senators or "[]") if m.get("party")]
        except ValueError:
            parties = []
        return Record("issue", f"issue:{to_public_id(issue.id)}", issue.date,
                      f"{_SITE}/issue/{to_public_id(issue.id)}", issue.title, parties=parties, issue_id=issue.id)

    def records_for_bill(self, bill_id: str, latest: str) -> list[Record]:
        """A bill's records dated on or before `latest`, most decisive first."""
        out = [self.vote_record(rc) for rc in self.votes if rc.bill_id == bill_id and rc.date <= latest]
        out += [self.event_record(e) for e in self.events if e.bill_id == bill_id and e.date <= latest]
        hit = self.bills.get(bill_id)
        if hit and (hit[0].latest_action_date or hit[0].introduced_date or "") <= latest:
            out.append(self.bill_record(hit[0]))
        return out

    def issue_embeddings(self) -> np.ndarray:
        if self._issue_emb is None:
            self._issue_emb = (_embed_texts_sim([i.title for i in self.issues]) if self.issues
                               else np.zeros((0, 384), dtype=np.float32))
        return self._issue_emb


def _best(records: list[Record]) -> Record | None:
    """The most decisive record, newest first within a kind."""
    if not records:
        return None
    return sorted(records, key=lambda r: (RECORD_PREFERENCE[r.kind], -int(r.date.replace("-", "") or 0)))[0]


def act_phrases(text: str) -> list[list[str]]:
    """Each statute title the text may name, as its candidate spellings
    longest first: the capitalised run before "Act" can start with words
    that aren't the title ("Illinois Democrat Jane Roe's Safe Streets Act"),
    so every tail of two or more words is a candidate, and the longest one a
    record's title contains is the match."""
    phrases = []
    for m in _ACT_PHRASE_RE.finditer(text or ""):
        words = _LEADING_ARTICLE_RE.sub("", m.group(1)).strip().lower().split()
        tails = [" ".join(words[i:]) for i in range(len(words) - 1)]
        if tails and tails not in phrases:
            phrases.append(tails)
    return phrases


def _contains_phrase(title: str, phrase: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", (title or "").lower()) is not None


def find_evidence(
    index: RecordIndex, name: str, description: str, started: datetime, trend_emb: np.ndarray,
) -> tuple[list[Evidence], list[dict]]:
    """Every pinned match for one trend (strongest first), and the members it
    names (action_center._find_related_senators' entries)."""
    text = f"{name}. {description}".strip()
    latest = _iso(started.date() + EVIDENCE_GRACE)
    named = _find_related_senators(name, description, [], index.db)
    named_parties = [m["party"] for m in named if m.get("party")]
    found: list[Evidence] = []

    # issue
    if index.issues:
        sims = index.issue_embeddings() @ trend_emb
        for issue, sim in zip(index.issues, sims):
            if sim < MATCH_MIN_COSINE or issue.date > latest or issue.date < _iso(started.date() - ISSUE_LOOKBACK):
                continue
            linked: list[Record] = []
            try:
                for bill in json.loads(issue.related_bill_ids or "[]"):
                    if bill.get("id") and int(bill.get("congress") or index.congress) == index.congress:
                        linked += index.records_for_bill(bill["id"], latest)
                doc_ids = [int(x) for x in json.loads(issue.related_explore_ids or "[]")]
            except (ValueError, TypeError):
                doc_ids = []
            if doc_ids:
                docs = index.db.query(ExploreDocument).filter(
                    ExploreDocument.id.in_(doc_ids), ExploreDocument.doc_type.in_(OFFICIAL_DOCUMENT_TYPES)).all()
                linked += [index.document_record(d) for d in docs if d.date[:10] <= latest]
            record = _best(linked) or index.issue_record(issue)
            found.append(Evidence("issue", float(sim), record, named_parties,
                                  record.parties or index.issue_record(issue).parties,
                                  anchor_issue_id=issue.id, detail=issue.title))

    # act
    for tails in act_phrases(text):
        for phrase in tails:
            records = [index.vote_record(rc) for rc in index.votes
                       if rc.date <= latest and _contains_phrase(rc.title, phrase)]
            records += [index.event_record(e) for e in index.events
                        if e.date <= latest and _contains_phrase(e.name, phrase)]
            records += [index.bill_record(row) for row, _ in index.bills.values()
                        if _contains_phrase(row.title, phrase)
                        and (row.latest_action_date or row.introduced_date or "") <= latest]
            record = _best(records)
            if record is not None:
                found.append(Evidence("act", 1.0, record, named_parties, record.parties, detail=phrase))
                break

    # nominee
    for rc in index.votes:
        if rc.date > latest or not rc.question.startswith("On the Nomination"):
            continue
        m = _NOMINEE_RE.match(rc.title or "")
        if not m:
            continue
        full = m.group(1).strip()
        parts = full.split()
        short = f"{parts[0]} {parts[-1]}" if len(parts) >= 2 else ""
        if len(parts) >= 2 and (_mentions_full_name(text, full) or _mentions_full_name(text, short)):
            found.append(Evidence("nominee", 1.0, index.vote_record(rc), named_parties, [], detail=full))
            break  # the newest vote on the nominee

    # member: a named member's own bill about the trend
    member_bills = [(m, row) for m in named for row in index.bills_by_member.get(m["id"], [])]
    if member_bills:
        sims = _embed_texts_sim([row.title for _, row in member_bills]) @ trend_emb
        best = max(range(len(member_bills)), key=lambda i: sims[i])
        member, row = member_bills[best]
        if sims[best] >= MATCH_MIN_COSINE:
            record = _best(index.records_for_bill(row.bill_id, latest))
            if record is not None:
                found.append(Evidence("member", float(sims[best]), record, named_parties, record.parties,
                                      detail=member["name"]))

    found.sort(key=lambda e: (-e.strength, RECORD_PREFERENCE[e.record.kind]))
    return found, named
